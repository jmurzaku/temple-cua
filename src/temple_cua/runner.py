"""Run each task in a fresh VM and retain the complete visual trajectory."""

from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import time

from .grading import grade
from .protocol import Action
from .report import build_report
from .tasks import Task
from .vm import TempleVM, VMConfig


def initialize_shell(vm: TempleVM):
    """Trusted setup for the official TempleOS 5.03 live ISO, before model access."""
    for action in (
        Action("type", text="n"), Action("wait", seconds=1),
        Action("type", text="n"), Action("wait", seconds=1),
        Action("type", text='AutoComplete(OFF);ms_grid.x=ms_grid.y=1;WinMax;Cd("::/Home");DocClear;\n'),
        Action("wait", seconds=1),
    ):
        vm.execute(action)


def _write_json(path: Path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def run_task(task: Task, provider, config: VMConfig, artifact_dir: Path,
             *, max_steps: int | None = None, timeout: float | None = None,
             settle_seconds: float = 0.25, progress=None) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=False)
    task_data = asdict(task)
    task_data.pop("source")
    task_fingerprint = hashlib.sha256(json.dumps(task_data, sort_keys=True).encode()).hexdigest()
    _write_json(artifact_dir / "task.json", {**task_data, "source": str(task.source) if task.source else None})
    history = []
    result = {"task_id": task.id, "title": task.title, "status": "budget_exhausted", "steps": 0,
              "elapsed_seconds": 0, "grade": {"status": "error", "score": None, "reason": "No screenshot"},
              "usage": {}, "artifact_dir": artifact_dir.name,
              "final_screenshot": f"{artifact_dir.name}/final.png"}
    step_limit = max_steps if max_steps is not None else task.max_steps
    time_limit = timeout if timeout is not None else task.timeout_seconds
    result["budget"] = {"max_steps": step_limit, "timeout_seconds": time_limit}
    result["task_fingerprint"] = task_fingerprint
    started = time.monotonic()
    final = artifact_dir / "final.png"
    trajectory = artifact_dir / "trajectory.jsonl"
    try:
        with TempleVM(config, artifact_dir / "vm") as vm:
            if config.baseline is None:
                initialize_shell(vm)
            for action in task.setup:
                vm.execute(action)
            vm.screenshot(artifact_dir / "0000.png")
            vm.screenshot(final)
            # Budget starts at the initial task observation; reset/boot is logged separately.
            task_started = time.monotonic()
            result["setup_seconds"] = task_started - started
            for step in range(1, step_limit + 1):
                remaining = time_limit - (time.monotonic() - task_started)
                if remaining <= 0:
                    result["status"] = "timeout"
                    break
                if progress:
                    progress(f"{task.id}: step {step}/{step_limit}")
                # API request timeout is bounded by the remaining task budget.
                old_timeout = getattr(provider, "timeout", None)
                if old_timeout is not None:
                    provider.timeout = min(old_timeout, remaining)
                before = artifact_dir / f"{step - 1:04d}.png"
                turn_started = time.monotonic()
                try:
                    decision = provider.decide(task=task.prompt, screenshot=before, history=history, step=step)
                finally:
                    if old_timeout is not None:
                        provider.timeout = old_timeout
                timed_out = time.monotonic() - task_started >= time_limit
                record = {"step": step, "note": decision.note,
                          "actions": [a.to_dict() for a in decision.actions],
                          "usage": decision.usage, "screenshot_before": before.name,
                          "executed_actions": [], "observation": ""}
                for key, value in decision.usage.items():
                    if isinstance(value, (int, float)):
                        result["usage"][key] = result["usage"].get(key, 0) + value
                done = False
                execution_error = None
                if not timed_out:
                    for action in decision.actions:
                        if time.monotonic() - task_started >= time_limit:
                            timed_out = True
                            break
                        if action.kind == "done":
                            done = True
                            result["completion_text"] = action.text
                        else:
                            try:
                                vm.execute(action, deadline=task_started + time_limit)
                            except TimeoutError:
                                timed_out = True
                                record["partial_action"] = action.to_dict()
                                break
                            except Exception as exc:
                                execution_error = exc
                                record["error"] = f"{type(exc).__name__}: {exc}"
                                record["partial_action"] = action.to_dict()
                                break
                        record["executed_actions"].append(action.to_dict())
                timed_out = timed_out or time.monotonic() - task_started >= time_limit
                if settle_seconds:
                    time.sleep(min(settle_seconds, max(0, time_limit - (time.monotonic() - task_started))))
                after = artifact_dir / f"{step:04d}.png"
                vm.screenshot(after)
                vm.screenshot(final)
                record["screenshot_after"] = after.name
                record["duration_seconds"] = time.monotonic() - turn_started
                record["observation"] = "Task time limit reached." if timed_out else "Actions executed. Current screenshot follows."
                with trajectory.open("a") as log:
                    log.write(json.dumps(record) + "\n")
                history.append(record)
                result["steps"] = step
                if execution_error:
                    raise execution_error
                if timed_out:
                    result["status"] = "timeout"
                    break
                if done:
                    result["status"] = "completed"
                    break
    except Exception as exc:
        deadline_reached = "task_started" in locals() and time.monotonic() - task_started >= time_limit
        result["status"] = "timeout" if isinstance(exc, TimeoutError) or deadline_reached else "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if final.exists():
        try:
            result["grade"] = grade(task.grader, final, artifact_dir)
        except Exception as exc:
            result["grade"] = {"status": "error", "score": None, "reason": f"{type(exc).__name__}: {exc}"}
    _write_json(artifact_dir / "result.json", result)
    return result


def run_suite(tasks: list[Task], provider_factory, config: VMConfig, output: Path,
              *, provider_name: str, model: str, max_steps=None, timeout=None, progress=None,
              model_options: dict | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    envelope = {"created_at": datetime.now(timezone.utc).isoformat(), "provider": provider_name,
                "model": model, "config": {"iso": str(config.iso), "baseline": str(config.baseline) if config.baseline else None,
                "memory_mb": config.memory_mb, "max_steps_override": max_steps,
                "timeout_override": timeout, "model_options": model_options or {},
                "boot_wait": config.boot_wait, "settle_seconds": 0.25}, "results": []}
    if config.baseline:
        sidecar = Path(str(config.baseline) + ".json")
        if sidecar.is_file():
            metadata = json.loads(sidecar.read_text())
            envelope["config"]["baseline_identity"] = metadata.get("identity")
            envelope["config"]["baseline_sha256"] = metadata.get("baseline_sha256")
    _write_json(output / "results.json", envelope)
    for task in tasks:
        if progress:
            progress(f"Starting {task.id}: {task.title}")
        result = run_task(task, provider_factory(), config, output / task.id,
                          max_steps=max_steps, timeout=timeout, progress=progress)
        envelope["results"].append(result)
        _write_json(output / "results.json", envelope)
        if progress:
            progress(f"{task.id}: {result['status']}; grade={result['grade']['status']}")
    build_report(output)
    return envelope
