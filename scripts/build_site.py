"""Build the task catalog and recorded runs for TempleOSBench."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import zipfile

import yaml

from temple_cua.tasks import load_tasks
from temple_cua.report import build_report

ROOT = Path(__file__).resolve().parents[1]
TASKS = {
    "arithmetic": ("Arithmetic", "Evaluate an integer expression.", "Shell"),
}
KEY_PATTERN = re.compile(
    rb"(?:sk-(?:proj|svcacct)-[A-Za-z0-9_-]{25,}"
    rb"|sk-ant-api[0-9]+-[A-Za-z0-9_-]{25,}"
    rb"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"
)


def portable_content(path):
    content = path.read_bytes()
    if path.name == "trajectory.jsonl":
        records = [json.loads(line) for line in content.decode().splitlines() if line.strip()]
        for record in records:
            record.pop("cua_output", None)
            record.pop("response_id", None)
        content = "".join(json.dumps(record) + "\n" for record in records).encode()
    elif path.name == "task.json":
        task = json.loads(content)
        task.pop("source", None)
        content = (json.dumps(task, indent=2) + "\n").encode()
    if KEY_PATTERN.search(content):
        raise ValueError(f"Credential pattern in {path.name}; build refused")
    return content


def copy(source, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(portable_content(source))


def asset_url(output, name):
    digest = hashlib.sha256((output / name).read_bytes()).hexdigest()[:12]
    return f"{name}?v={digest}"


def read_run(path, number):
    envelope = json.loads(portable_content(path / "results.json"))
    records = envelope.get("results")
    if envelope.get("provider") != "cua" or not isinstance(records, list) or not records:
        raise ValueError("Expected a recorded Cua run")
    ids = [record.get("task_id") for record in records if isinstance(record, dict)]
    if (len(ids) != len(records)
            or any(not isinstance(task_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", task_id)
                   for task_id in ids)):
        raise ValueError("Invalid recorded task ID")
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate recorded task IDs")
    if envelope.get("provenance", {}).get("execution_mode") != "live_model":
        raise ValueError("The recorded-results site requires a live model run")
    version = (envelope.get("config", {}).get("model_options", {}).get("cua_version")
               or envelope.get("cua_version")
               or envelope.get("provenance", {}).get("agent_version"))
    if not version:
        raise ValueError("Run does not record the installed Cua version")
    name = re.sub(r"[^A-Za-z0-9_.-]", "_", path.name).strip("._-") or "run"
    run_id = f"{number:02d}-{name}"
    metadata = {"id": run_id, "provider": envelope["provider"], "model": envelope["model"],
                "cua_version": version, "created_at": envelope["created_at"],
                "config": envelope.get("config", {}), "provenance": envelope.get("provenance", {})}
    return {"id": run_id, "path": path, "envelope": envelope, "metadata": metadata}


def read_review(folder):
    path = folder / "review.json"
    if not path.is_file():
        return None
    review = json.loads(portable_content(path))
    if (not isinstance(review, dict) or not isinstance(review.get("status"), str)
            or review["status"] not in {"passed", "failed", "incomplete"}
            or any(not isinstance(review.get(key), str) or not review[key].strip()
                   for key in ("reviewer", "reason"))
            or not isinstance(review.get("evidence"), list)
            or any(not isinstance(item, str) or not item.strip() for item in review["evidence"])):
        raise ValueError("Review must contain status, reviewer, reason, and an evidence string list")
    return review


def export_evaluation(folder, output, task_id, result):
    """Link host grading evidence without mixing it into the model replay."""
    if result.get("grade", {}).get("type") != "uart":
        return None
    artifacts = {}
    for name in result["grade"].get("evidence", []):
        if not isinstance(name, str) or Path(name).suffix not in {".json", ".png", ".txt"}:
            continue
        if not re.fullmatch(r"[A-Za-z0-9_-]+\.(?:json|png|txt)", name):
            raise ValueError("Host evaluation evidence must be a filename within its task directory")
        source = folder / name
        if source.is_symlink() or not source.is_file():
            raise ValueError(f"Missing or unsafe host evaluation evidence: {name}")
        relative = f"assets/evaluations/{task_id}/{name}"
        copy(source, output / relative)
        artifacts[name] = relative
    return artifacts


def export_run(source, destination, selected):
    envelope = source["envelope"]
    indexed = {result["task_id"]: result for result in envelope["results"]}
    filtered = {**envelope, "results": [indexed[task_id] for task_id in selected if task_id in indexed]}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "results.json").write_text(json.dumps(filtered, indent=2) + "\n")
    for result in filtered["results"]:
        for path in sorted((source["path"] / result["task_id"]).rglob("*")):
            relative = path.relative_to(source["path"])
            if (path.is_file() and "vm" not in relative.parts
                    and path.suffix in {".png", ".json", ".jsonl", ".txt", ".html"}):
                copy(path, destination / relative)
    build_report(destination)
    return filtered


def build(run, output, selected=None, additional_runs=None):
    run_paths = [Path(path).resolve() for path in (run, *(additional_runs or []))]
    output = Path(output).resolve()
    protected = (ROOT / "site", ROOT / "tasks", *run_paths)
    if any(output == source or output in source.parents or source in output.parents for source in protected):
        raise ValueError("Output must be separate from the source site, tasks, and run")
    sources = [read_run(path, number) for number, path in enumerate(run_paths, start=1)]
    results = {}
    for source in sources:
        for result in source["envelope"]["results"]:
            task_id = result["task_id"]
            if task_id in results:
                raise ValueError(f"Duplicate recorded task ID across runs: {task_id}")
            results[task_id] = (result, source)
    selected = selected if selected is not None else yaml.safe_load((ROOT / "site/tasks.yaml").read_text())
    if (not isinstance(selected, list) or not selected
            or any(not isinstance(task_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", task_id)
                   for task_id in selected)):
        raise ValueError("Select a nonempty list of task IDs")
    if len(selected) != len(set(selected)):
        raise ValueError("Duplicate selected task IDs")
    definitions = {task.id: task for task in load_tasks(ROOT / "tasks")}
    missing = set(selected) - (definitions.keys() | results.keys())
    if missing:
        raise ValueError(f"Unknown selected task IDs: {sorted(missing)}")
    if output.exists():
        shutil.rmtree(output)
    assets = output / "assets"
    output.mkdir(parents=True, exist_ok=True)
    for name in ("index.html", "styles.css", "app.js"):
        copy(ROOT / "site" / name, output / name)
    (output / ".nojekyll").touch()
    tasks = []
    for task_id in selected:
        if task_id not in results:
            task = definitions[task_id]
            copy(task.source, assets / "tasks" / f"{task.id}.yaml")
            tasks.append({"id": task.id, "title": task.title, "category": task.category.title(),
                          "difficulty": task.difficulty, "prompt": task.prompt, "grader": task.grader,
                          "result": None, "frames": []})
            continue
        result, source = results[task_id]
        folder = source["path"] / task_id
        records = [json.loads(line) for line in (folder / "trajectory.jsonl").read_text().splitlines() if line.strip()]
        frames = [{"step": 0, "src": f"assets/starter-frames/{task_id}/0000.png",
                   "note": "Before the first action.", "actions": [],
                   "requested_calls": [], "input_errors": []}]
        copy(folder / "0000.png", output / frames[0]["src"])
        for record in records:
            filename = record.get("screenshot_after") or f"{record['step']:04d}.png"
            if Path(filename).name != filename:
                raise ValueError("Screenshot must be a filename within its task directory")
            if not (folder / filename).is_file():
                raise ValueError(f"Missing screenshot for {task_id}, turn {record['step']}")
            src = f"assets/starter-frames/{task_id}/{filename}"
            copy(folder / filename, output / src)
            frames.append({"step": record["step"], "src": src, "note": record.get("note", ""),
                           "actions": record.get("executed_actions", record.get("actions", [])),
                           "requested_calls": record.get("requested_calls", []),
                           "input_errors": record.get("input_errors", [])})
        task = json.loads(portable_content(folder / "task.json"))
        if task["id"] != task_id:
            raise ValueError(f"Task snapshot does not match {task_id}")
        title, description, category = TASKS.get(
            task_id, (task["title"], "", task.get("category", "general").title()))
        definition = assets / "tasks" / f"{task_id}.yaml"
        definition.parent.mkdir(parents=True, exist_ok=True)
        definition.write_text(yaml.safe_dump({"version": 1, **task}, sort_keys=False))
        tasks.append({"id": task_id, "title": title, "description": description, "category": category,
                      "difficulty": task.get("difficulty", "medium"), "grader": task["grader"],
                      "prompt": task["prompt"], "result": result, "frames": frames,
                      "run": source["metadata"]})
        review = read_review(folder)
        if review is not None:
            tasks[-1]["review"] = review
        evaluation = export_evaluation(folder, output, task_id, result)
        if evaluation is not None:
            tasks[-1]["evaluation_artifacts"] = evaluation
    used_ids = {task["run"]["id"] for task in tasks if task.get("run")}
    used_sources = [source for source in sources if source["id"] in used_ids]
    # Keep the primary run alias for viewers cached before multi-run support.
    # Current viewers use each task's run metadata and the complete runs list.
    legacy_run = (used_sources or sources)[0]["metadata"]
    data = {"run": legacy_run, "runs": [source["metadata"] for source in used_sources], "tasks": tasks}
    (assets / "starter-data.json").write_text(json.dumps(data, indent=2) + "\n")
    index = output / "index.html"
    document = index.read_text()
    document = document.replace('src="app.js"', f'src="{asset_url(output, "app.js")}"')
    document = document.replace('href="styles.css"', f'href="{asset_url(output, "styles.css")}"')
    document = document.replace('data-task-data="assets/starter-data.json"',
                                f'data-task-data="{asset_url(output, "assets/starter-data.json")}"')
    index.write_text(document)
    export_sources = used_sources or (sources if len(sources) == 1 else [])
    with tempfile.TemporaryDirectory(prefix="templeosbench-run-") as scratch:
        archive_root = Path(scratch)
        if len(export_sources) == 1:
            export_run(export_sources[0], archive_root, selected)
        else:
            exported = [{"id": source["id"], **export_run(source, archive_root / source["id"], selected)}
                        for source in export_sources]
            collection = {"format": "templeosbench-recorded-runs", "version": 1, "runs": exported}
            (archive_root / "results.json").write_text(json.dumps(collection, indent=2) + "\n")
        copy(archive_root / "results.json", assets / "starter-results.json")
        with zipfile.ZipFile(assets / "starter-run.zip", "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(archive_root.rglob("*")):
                if not path.is_file():
                    continue
                entry = zipfile.ZipInfo("starter-run/" + path.relative_to(archive_root).as_posix())
                entry.compress_type = zipfile.ZIP_DEFLATED
                entry.external_attr = 0o100644 << 16
                archive.writestr(entry, portable_content(path))
    return {"tasks": len(tasks), "frames": sum(len(task["frames"]) for task in tasks), "output": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path, nargs="?", default=ROOT / "examples/cua-starter")
    parser.add_argument("--output", type=Path, default=ROOT / "build/site")
    parser.add_argument("--run", dest="additional_runs", action="append", type=Path, default=[],
                        help="Add a recorded run; repeat for each supplementary source")
    parser.add_argument("--task", action="append", help="Feature this task; repeat for each task in display order")
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    try:
        result = build(run, output, args.task, additional_runs=args.additional_runs)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
