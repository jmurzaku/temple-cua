"""Build the task catalog and recorded runs for TempleOSBench."""

import argparse
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


def build(run, output, selected=None):
    run, output = Path(run).resolve(), Path(output).resolve()
    sources = (ROOT / "site", ROOT / "tasks", run)
    if any(output == source or output in source.parents or source in output.parents for source in sources):
        raise ValueError("Output must be separate from the source site, tasks, and run")
    envelope = json.loads((run / "results.json").read_text())
    results = {result["task_id"]: result for result in envelope["results"]}
    if envelope["provider"] != "cua" or not results:
        raise ValueError("Expected a recorded Cua run")
    if any(not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", task_id) for task_id in results):
        raise ValueError("Invalid recorded task ID")
    if envelope.get("provenance", {}).get("execution_mode") != "live_model":
        raise ValueError("The recorded-results site requires a live model run")
    version = (envelope.get("config", {}).get("model_options", {}).get("cua_version")
               or envelope.get("cua_version")
               or envelope.get("provenance", {}).get("agent_version"))
    if not version:
        raise ValueError("Run does not record the installed Cua version")
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
        result = results[task_id]
        folder = run / task_id
        records = [json.loads(line) for line in (folder / "trajectory.jsonl").read_text().splitlines() if line.strip()]
        frames = [{"step": 0, "src": f"assets/starter-frames/{task_id}/0000.png",
                   "note": "Before the first action.", "actions": []}]
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
                           "actions": record.get("executed_actions", record.get("actions", []))})
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
                      "prompt": task["prompt"], "result": result, "frames": frames})
    data = {"run": {"model": envelope["model"], "cua_version": version,
                    "created_at": envelope["created_at"],
                    "description": "One attempt per task, each from the same VM snapshot."}, "tasks": tasks}
    (assets / "starter-data.json").write_text(json.dumps(data, indent=2) + "\n")
    filtered = {**envelope, "results": [results[task_id] for task_id in selected if task_id in results]}
    with tempfile.TemporaryDirectory(prefix="templeosbench-run-") as scratch:
        archive_root = Path(scratch)
        (archive_root / "results.json").write_text(json.dumps(filtered, indent=2) + "\n")
        for task_id in selected:
            if task_id not in results:
                continue
            for path in sorted((run / task_id).rglob("*")):
                relative = path.relative_to(run)
                if (path.is_file() and "vm" not in relative.parts
                        and path.suffix in {".png", ".json", ".jsonl", ".txt", ".html"}):
                    copy(path, archive_root / relative)
        build_report(archive_root)
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
    parser.add_argument("--task", action="append", help="Feature this task; repeat for each task in display order")
    args = parser.parse_args()
    run, output = args.run.resolve(), args.output.resolve()
    try:
        result = build(run, output, args.task)
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
