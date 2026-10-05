"""Task loading; private grading rules never enter the model prompt."""

from dataclasses import dataclass, field
from pathlib import Path
import re
import math
import yaml

from .protocol import Action

_TASK_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,79}")


def create_task(task_id: str, *, tasks_dir: Path = Path("tasks"), title: str | None = None,
                prompt: str | None = None, expect: list[str] | None = None) -> Path:
    """Create an editable task without replacing a file or duplicating an ID."""
    if not isinstance(task_id, str) or not _TASK_ID_PATTERN.fullmatch(task_id):
        raise ValueError("Task ID must use 1..80 lowercase letters, digits, underscores or hyphens, starting with a letter or digit")
    title = title if title is not None else task_id.replace("_", " ").replace("-", " ").title()
    prompt = prompt if prompt is not None else (
        "Use the TempleOS command line to print TASK_READY on its own line. "
        "Leave the output visible and finish."
    )
    for name, value in (("title", title), ("prompt", prompt)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Task {name} must be nonempty text")
    if expect is not None and (not isinstance(expect, list) or any(
        not isinstance(text, str) or not text.strip() for text in expect
    )):
        raise ValueError("--expect requires nonempty text")
    grader = {
        "type": "ocr", "crop": [8, 16, 624, 456], "case_sensitive": True,
        "match_mode": "line", "all": list(expect),
    } if expect else {
        "type": "manual",
        "rubric": "Review the final screenshot and trajectory. Confirm the actions in the prompt were performed and the requested result remains visible.",
    }
    data = {"version": 1, "id": task_id, "title": title, "prompt": prompt,
            "max_steps": 40, "timeout_seconds": 180, "grader": grader}
    serialized = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    tasks_dir = Path(tasks_dir)
    destination = tasks_dir / f"{task_id}.yaml"
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Task file already exists: {destination}")
    if tasks_dir.is_dir():
        for existing in tasks_dir.glob("*.yaml"):
            if load_task(existing).id == task_id:
                raise ValueError(f"Task ID {task_id!r} already exists in {existing}")
    tasks_dir.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as output:
        output.write(serialized)
    return destination


@dataclass
class Task:
    id: str
    title: str
    prompt: str
    grader: dict
    category: str = "general"
    difficulty: str = "medium"
    max_steps: int = 40
    timeout_seconds: float = 180
    setup: list[Action] = field(default_factory=list)
    source: Path | None = None


def load_task(path: Path) -> Task:
    data = yaml.safe_load(path.read_text())
    if not isinstance(data, dict) or data.pop("version", None) != 1:
        raise ValueError(f"{path}: expected task format version 1")
    allowed = {"id", "title", "prompt", "grader", "category", "difficulty", "max_steps", "timeout_seconds", "setup"}
    if set(data) - allowed:
        raise ValueError(f"{path}: unknown fields {sorted(set(data) - allowed)}")
    for key in ("id", "title", "prompt"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"{path}: missing {key}")
    if not _TASK_ID_PATTERN.fullmatch(data["id"]):
        raise ValueError(f"{path}: invalid task id")
    if not isinstance(data.get("grader"), dict) or data["grader"].get("type") not in {"ocr", "manual", "uart"}:
        raise ValueError(f"{path}: grader must have type ocr, manual, or uart")
    if type(data.get("max_steps", 40)) is not int or not 1 <= data.get("max_steps", 40) <= 1000:
        raise ValueError(f"{path}: max_steps must be within 1..1000")
    timeout = data.get("timeout_seconds", 180)
    if not isinstance(timeout, (float, int)) or isinstance(timeout, bool) or not math.isfinite(timeout) or not 0 < timeout <= 7200:
        raise ValueError(f"{path}: timeout_seconds must be within 0..7200")
    setup = data.pop("setup", [])
    if not isinstance(setup, list):
        raise ValueError(f"{path}: setup must be an action list")
    data["setup"] = [Action.from_dict(a) for a in setup]
    if any(a.kind == "done" for a in data["setup"]):
        raise ValueError("Task setup cannot include done")
    return Task(**data, source=path)


def load_tasks(path: Path, selected: list[str] | None = None) -> list[Task]:
    files = [path] if path.is_file() else sorted(path.glob("*.yaml"))
    if not files:
        raise ValueError(f"No task YAML files in {path}")
    tasks = [load_task(p) for p in files]
    ids = [t.id for t in tasks]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate task IDs")
    if selected:
        missing = set(selected) - set(ids)
        if missing:
            raise ValueError(f"Unknown task IDs: {sorted(missing)}")
        tasks = [t for t in tasks if t.id in selected]
    return tasks
