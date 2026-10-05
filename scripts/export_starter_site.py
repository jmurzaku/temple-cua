"""Export measured starter runs and portable source for the static research site."""
import argparse
import json
from pathlib import Path
import re
import shutil
import zipfile

from temple_cua.tasks import load_tasks

STARTER = {
    "arithmetic": ("Arithmetic", "Evaluate an integer expression in the HolyC shell.", "Shell"),
    "sum_of_squares": ("A simple loop", "Sum the first twelve squares using a HolyC loop.", "Loop"),
    "triangular_function": ("Define a function", "Define a function and call it with an integer argument.", "Function"),
    "string_statistics": ("String operations", "Measure a string and count a character in it.", "Strings"),
    "directory_navigation": ("Directory navigation", "Navigate to the graphics examples and leave their listing visible.", "Filesystem"),
    "file_roundtrip": ("Write and read a file", "Save three lines to the RAM drive, then read the file back.", "Filesystem"),
}
STARTER_README = r"""# TempleOS starter harness

Six screenshot-based tasks: arithmetic, a loop, a function, strings, directory
navigation, and a file round trip. The actual Cua ComputerAgent owns the model
and action loop. TempleOS QMP input, reset, time budgets and visual grading stay
in the local harness. Other research modules remain available separately.
All ten task definitions and the test suite are included. The four additional
desktop tasks require manual review; the command below selects only the six
starter tasks shown on the website.

Linux, Python 3.11+, and QEMU are required. Use your own OpenAI credential in
OPENAI_API_KEY; credentials are never included in the download.

```sh
python -m venv .venv
.venv/bin/pip install -e '.[cua,dev]'
.venv/bin/temple-cua fetch-iso
.venv/bin/temple-cua prepare --output assets/baseline.qcow2
.venv/bin/temple-cua run --provider cua --model openai/gpt-6.1-sol \
  --baseline assets/baseline.qcow2 --boot-wait 1 \
  --task arithmetic --task sum_of_squares --task triangular_function \
  --task string_statistics --task directory_navigation --task file_roundtrip \
  --max-steps 24 --timeout 300 --api-timeout 90 \
  --max-output-tokens 4096 --cua-image-history 2 --output runs/starter
```

Add `--task arithmetic` to select one task. The generated report and JSONL
trajectories keep screenshots, actions and grading evidence. Visual checks reject
ordinary command echoes, but do not prove every implementation requirement or
hidden filesystem state. See docs/CUA_AGENT.md for the SDK bridge and limits.

The ISO and VM snapshots are obtained locally, not bundled. scripts/qemu-local
is a workspace-specific wrapper; install normal system QEMU on another machine.
"""
KEY_PATTERN = re.compile(
    rb"(?:sk-(?:proj|svcacct)-[A-Za-z0-9_-]{25,}"
    rb"|sk-ant-api[0-9]+-[A-Za-z0-9_-]{25,}"
    rb"|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})"
)


def portable_content(path):
    """Keep reproducible evidence without provider IDs or opaque reasoning."""
    content = path.read_bytes()
    if path.name == "trajectory.jsonl":
        records = [json.loads(line) for line in content.decode().splitlines() if line.strip()]
        for record in records:
            record.pop("cua_output", None)
            record.pop("response_id", None)
        return ("".join(json.dumps(record) + "\n" for record in records)).encode()
    if path.name == "task.json":
        task = json.loads(content)
        task.pop("source", None)
        return (json.dumps(task, indent=2) + "\n").encode()
    return content


def safe_copy(source, destination):
    data = source.read_bytes()
    if KEY_PATTERN.search(data):
        raise ValueError(f"Credential pattern detected in {source.name}; export refused")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    parser.add_argument("site", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    run = args.run.resolve()
    assets = args.site.resolve() / "dist/assets"
    envelope = json.loads((run / "results.json").read_text())
    if envelope["provider"] != "cua":
        raise ValueError("Export requires an actual Cua run")
    results = {r["task_id"]: r for r in envelope["results"]}
    if set(results) != set(STARTER):
        raise ValueError("Run must contain exactly the six starter tasks")
    tasks = {t.id: t for t in load_tasks(root / "tasks", list(STARTER))}
    exported = []
    for task_id, (title, description, category) in STARTER.items():
        task = tasks[task_id]
        folder = run / task_id
        records = [json.loads(line) for line in (folder / "trajectory.jsonl").read_text().splitlines() if line.strip()]
        frames = [{"step": 0, "src": f"assets/starter-frames/{task_id}/0000.png", "note": "Initial desktop, before the agent acts.", "actions": []}]
        safe_copy(folder / "0000.png", assets / "starter-frames" / task_id / "0000.png")
        for record in records:
            filename = record.get("screenshot_after") or f"{record['step']:04d}.png"
            if Path(filename).name != filename:
                raise ValueError("Screenshot filename must be local to the task")
            if not (folder / filename).is_file():
                continue
            safe_copy(folder / filename, assets / "starter-frames" / task_id / filename)
            frames.append({"step": record["step"], "src": f"assets/starter-frames/{task_id}/{filename}", "note": record.get("note", ""), "actions": record.get("executed_actions", record.get("actions", []))})
        safe_copy(task.source, assets / "tasks" / f"{task_id}.yaml")
        exported.append({"id": task_id, "title": title, "description": description, "category": category, "prompt": task.prompt, "result": results[task_id], "frames": frames})
    version = envelope.get("config", {}).get("model_options", {}).get("cua_version") or envelope.get("cua_version") or envelope.get("provenance", {}).get("agent_version")
    if not version:
        raise ValueError("Run must record the actual installed Cua version")
    data = {"run": {"model": envelope["model"], "cua_version": version, "created_at": envelope["created_at"], "description": "One fresh-reset attempt per task using the real Cua ComputerAgent loop, a TempleOS computer adapter, and an OpenAI Responses compatibility bridge. Step and time budgets are enforced by the harness."}, "tasks": exported}
    (assets / "starter-data.json").write_text(json.dumps(data, indent=2) + "\n")
    safe_copy(run / "results.json", assets / "starter-results.json")
    with zipfile.ZipFile(assets / "starter-run.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(run.rglob("*")):
            relative = path.relative_to(run)
            if not path.is_file() or "vm" in relative.parts or path.suffix not in {".png", ".json", ".jsonl", ".txt", ".html"}:
                continue
            content = portable_content(path)
            if KEY_PATTERN.search(content):
                raise ValueError(f"Credential pattern in {relative}; export refused")
            archive.writestr("starter-run/" + relative.as_posix(), content)
    excluded = {".git", ".openai", ".pytest_cache", ".venv", ".runtime",
                "assets", "runs", "research-sources", "__pycache__", "site"}
    with zipfile.ZipFile(assets / "starter-harness.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            relative = path.relative_to(root)
            if not path.is_file() or any(part in excluded or part.endswith(".egg-info") for part in relative.parts) or path.suffix in {".zip", ".pyc"}:
                continue
            if path.name == ".env" or path.name.startswith(".env."):
                continue
            content = path.read_bytes()
            if KEY_PATTERN.search(content):
                raise ValueError(f"Credential pattern in {relative}; export refused")
            if relative.as_posix() == "README.md":
                content = STARTER_README.encode()
            archive.writestr("temple-cua/" + relative.as_posix(), content)
    print(json.dumps({"tasks": len(exported), "frames": sum(len(t["frames"]) for t in exported), "cua_version": version, "assets": str(assets)}))


if __name__ == "__main__":
    main()
