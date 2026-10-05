"""Portable, escaped HTML reports and transparent model-level aggregation."""

from collections import Counter
import hashlib
from html import escape
import json
import math
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("results"), list):
        raise ValueError(f"{path}: expected an object containing a results list")
    if any(not isinstance(result, dict) for result in value["results"]):
        raise ValueError(f"{path}: every result must be an object")
    return value


def _clean(value: Any) -> str:
    """Keep errors useful while omitting common credential forms."""
    text = str(value)
    text = re.sub(r"\bsk-[A-Za-z0-9_-]{8,}", "[redacted key]", text)
    text = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._~+/-]+", r"\1[redacted]", text)
    return text


def _h(value: Any) -> str:
    return escape(_clean(value), quote=True)


def _safe_href(root: Path, value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    if re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", value):
        return None
    # Reports only link to artifacts inside this run, including through symlinks.
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        relative = candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return None
    if not relative.parts:
        return None
    return quote(relative.as_posix(), safe="/")


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except (OSError, ValueError):
        return False


def _link(root: Path, value: Any, label: str) -> str:
    href = _safe_href(root, value)
    return f'<a href="{escape(href, quote=True)}">{_h(label)}</a>' if href else _h(label)


def _number(value: Any, suffix: str = "") -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return _h(f"{value:g}{suffix}")
    return "—"


def _evidence_html(root: Path, artifact_dir: Any, evidence: Any) -> str:
    if not isinstance(evidence, list):
        return ""
    items = []
    for item in evidence:
        label = str(item)
        candidate = label
        if (isinstance(artifact_dir, str) and not Path(label).is_absolute()
                and _is_file(root / artifact_dir / label)):
            candidate = str(Path(artifact_dir) / label)
        href = _safe_href(root, candidate)
        if href and _is_file(root / Path(candidate)):
            items.append(f"<li>{_link(root, candidate, label)}</li>")
        else:
            items.append(f"<li>{_h(label)}</li>")
    return "<ul>" + "".join(items) + "</ul>" if items else ""


def build_report(run_dir: Path) -> Path:
    """Create a self-contained HTML document with relative artifact links.

    The report uses no JavaScript, CDNs or network resources. Screenshots and
    trajectory files remain alongside it so the whole run folder is portable.
    Execution outcomes and grade outcomes are deliberately reported separately.
    """
    run_dir = Path(run_dir)
    envelope = _load(run_dir / "results.json")
    results = envelope["results"]
    grades = Counter((result.get("grade") or {}).get("status", "needs_review")
                     for result in results if isinstance(result.get("grade") or {}, dict))
    execution = Counter(str(result.get("status", "unknown")) for result in results)
    cards = "".join(
        f'<div class="metric"><strong>{grades[status]}</strong><span>{label}</span></div>'
        for status, label in (("passed", "Passed"), ("failed", "Failed"),
                              ("needs_review", "Needs review"), ("error", "Grader errors"))
    )
    execution_text = "; ".join(f"{count} {status.replace('_', ' ')}"
                               for status, count in sorted(execution.items())) or "No tasks"
    sections = []
    for result in results:
        grading = result.get("grade") if isinstance(result.get("grade"), dict) else {}
        grade_status = str(grading.get("status", "needs_review"))
        status_class = grade_status if grade_status in {"passed", "failed", "needs_review", "error"} else "unknown"
        artifact_dir = result.get("artifact_dir")
        links = []
        if isinstance(artifact_dir, str):
            links.append(_link(run_dir, str(Path(artifact_dir) / "trajectory.jsonl"), "Trajectory (JSONL)"))
        screenshot_href = _safe_href(run_dir, result.get("final_screenshot"))
        screenshot = ""
        if screenshot_href:
            safe_url = escape(screenshot_href, quote=True)
            screenshot = (f'<a class="screenshot" href="{safe_url}"><img src="{safe_url}" '
                          f'alt="Final screenshot for {_h(result.get("task_id", "task"))}" loading="lazy"></a>')
        error = f'<p class="error">{_h(result["error"])}</p>' if result.get("error") else ""
        reason = _h(grading.get("reason", "No grade recorded; review the screenshot and trajectory."))
        evidence = _evidence_html(run_dir, artifact_dir, grading.get("evidence", []))
        # Include only numeric usage totals; arbitrary provider metadata may contain secrets.
        usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        token_fields = ("input_tokens", "output_tokens", "total_tokens", "cached_input_tokens",
                        "cache_read_input_tokens", "cache_creation_input_tokens")
        usage_text = ", ".join(f"{key.replace('_', ' ')}: {_number(usage[key])}"
                               for key in token_fields if key in usage
                               and isinstance(usage[key], (int, float)) and not isinstance(usage[key], bool))
        usage_html = f"<p class=\"muted\">{usage_text}</p>" if usage_text else ""
        sections.append(
            f'<article><h2>{_h(result.get("title", result.get("task_id", "Task")))}</h2>'
            f'<p class="muted">Task {_h(result.get("task_id", ""))}</p>'
            f'<p><span class="badge {status_class}">Grade: {_h(grade_status.replace("_", " "))}</span> '
            f'Execution: {_h(result.get("status", "unknown"))} · '
            f'Steps: {_number(result.get("steps"))} · '
            f'Elapsed: {_number(result.get("elapsed_seconds"), " s")} · '
            f'Score: {_number(grading.get("score"))}</p>'
            f'<p>{reason}</p>{error}{usage_html}{evidence}'
            f'<p>{" · ".join(links)}</p>{screenshot}</article>'
        )
    document = f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; base-uri 'none'">
<title>TempleOS CUA report</title><style>
:root{{color-scheme:light dark;font-family:system-ui,sans-serif}}body{{max-width:1100px;margin:2rem auto;padding:0 1rem;line-height:1.5}}
h1,h2{{line-height:1.2}}.muted{{opacity:.7}}.summary{{display:flex;flex-wrap:wrap;gap:1rem;margin:1.5rem 0}}
.metric{{border:1px solid #8886;border-radius:.5rem;padding:1rem;min-width:8rem}}.metric strong{{display:block;font-size:1.7rem}}
article{{border-top:1px solid #8886;padding:1.5rem 0;overflow-wrap:anywhere}}.badge{{border:1px solid #8886;border-radius:.3rem;padding:.2rem .5rem}}
.passed{{background:#228b2233}}.failed,.error{{background:#dc143c22}}.needs_review{{background:#e0a00022}}
img{{max-width:100%;height:auto;border:1px solid #8886;image-rendering:pixelated}}a{{color:#6699ff}}li{{margin:.3rem 0}}
</style></head><body><h1>TempleOS CUA report</h1>
<p><strong>{_h(envelope.get("provider", "unknown"))} / {_h(envelope.get("model", "unknown"))}</strong><br>
Created {_h(envelope.get("created_at", "unknown"))}</p>
<div class="summary">{cards}</div><p>Execution outcomes: {_h(execution_text)}.</p>
<p class="muted">OCR grades are heuristic visual evidence. Command echo or source text can satisfy a match; guest state is not verified.
Manual tasks require a reviewer. An execution outcome and a grade describe separate facts.</p>
{"".join(sections)}</body></html>'''
    path = run_dir / "report.html"
    path.write_text(document, encoding="utf-8")
    return path


def _accepted_score(grading: dict) -> bool:
    score = grading.get("score")
    return (grading.get("status") in {"passed", "failed"}
            and grading.get("type") in {None, "ocr", "manual"}
            and isinstance(score, (int, float)) and not isinstance(score, bool)
            and math.isfinite(score) and 0 <= score <= 1)


def _nonnegative_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0)


def _run_metadata(path: Path, envelope: dict) -> tuple[dict, list[str]]:
    """Retain effective budgets and experiment identity without provider secrets."""
    root = path.parent
    config = envelope.get("config") if isinstance(envelope.get("config"), dict) else {}
    visible_fields = ("baseline", "baseline_sha256", "iso", "iso_sha256", "memory_mb",
                      "max_steps_override", "timeout_override", "max_output_tokens", "history_turns",
                      "settle_seconds", "boot_wait")
    visible_config = {key: config[key] for key in visible_fields if key in config}
    identity = config.get("baseline_identity")
    if isinstance(identity, dict):
        visible_config["baseline_identity"] = {key: identity[key] for key in (
            "iso_sha256", "qemu_version", "memory_mb", "extra_args", "device_layout_version"
        ) if key in identity}
        if identity.get("iso_sha256"):
            visible_config.setdefault("iso_sha256", identity["iso_sha256"])
    options = config.get("model_options")
    if isinstance(options, dict):
        visible_config["model_options"] = {key: options[key] for key in (
            "max_output_tokens", "history_steps", "api_timeout"
        ) if key in options}
    task_counts = Counter()
    scored_counts = Counter()
    budgets = {}
    fingerprints = {}
    missing = []
    scores = []
    for record in envelope["results"]:
        task_id = str(record.get("task_id", "unknown"))
        task_counts[task_id] += 1
        grading = record.get("grade") if isinstance(record.get("grade"), dict) else {}
        if _accepted_score(grading):
            scored_counts[task_id] += 1
            scores.append(float(grading["score"]))
        task_config = {}
        artifact = record.get("artifact_dir")
        task_path = _safe_href(root, str(Path(artifact) / "task.json")) if isinstance(artifact, str) else None
        if task_path:
            # Do not use the URL-escaped value as a filesystem path.
            task_file = root / artifact / "task.json"
            try:
                candidate = json.loads(task_file.read_text(encoding="utf-8"))
                if isinstance(candidate, dict):
                    task_config = candidate
            except (OSError, ValueError):
                pass
        explicit = record.get("budget") if isinstance(record.get("budget"), dict) else {}
        step_limit = explicit.get("max_steps", config.get("max_steps_override"))
        if step_limit is None:
            step_limit = task_config.get("max_steps")
        time_limit = explicit.get("timeout_seconds", config.get("timeout_override"))
        if time_limit is None:
            time_limit = task_config.get("timeout_seconds")
        if type(step_limit) is not int or step_limit <= 0:
            step_limit = None
        if (not isinstance(time_limit, (int, float)) or isinstance(time_limit, bool)
                or not math.isfinite(time_limit) or time_limit <= 0):
            time_limit = None
        budgets[task_id] = {"max_steps": step_limit, "timeout_seconds": time_limit}
        if step_limit is None or time_limit is None:
            missing.append(f"{task_id}: effective task budgets")
        fingerprint = record.get("task_fingerprint")
        if isinstance(fingerprint, str) and re.fullmatch(r"[a-fA-F0-9]{64}", fingerprint):
            fingerprints[task_id] = fingerprint.lower()
        elif task_config:
            # Match the runner's canonical task fingerprint, excluding only host paths.
            fingerprint_data = {key: value for key, value in task_config.items() if key != "source"}
            fingerprints[task_id] = hashlib.sha256(
                json.dumps(fingerprint_data, sort_keys=True).encode()
            ).hexdigest()
        else:
            missing.append(f"{task_id}: task definition fingerprint")
    if not config.get("baseline") and not config.get("baseline_sha256"):
        missing.append("baseline identity")
    if "iso" not in visible_config and "iso_sha256" not in visible_config:
        missing.append("ISO identity")
    metadata = {
        "path": str(path), "provider": str(envelope.get("provider", "unknown")),
        "model": str(envelope.get("model", "unknown")),
        "tasks": sum(task_counts.values()), "scored_tasks": len(scores),
        "average_score": sum(scores) / len(scores) if scores else None,
        "task_ids": sorted(task_counts), "task_counts": dict(sorted(task_counts.items())),
        "scored_task_counts": dict(sorted(scored_counts.items())),
        "budgets": dict(sorted(budgets.items())), "config": visible_config,
        "task_fingerprints": dict(sorted(fingerprints.items())),
        "metadata_missing": missing,
    }
    return metadata, missing


def _comparability(metadata: list[dict]) -> tuple[bool, str]:
    if len(metadata) < 2:
        return False, "At least two runs are needed for a model comparison."
    issues = []
    first = metadata[0]
    if any(item["task_counts"] != first["task_counts"] for item in metadata[1:]):
        issues.append("task sets or repeat counts differ")
    if any(item["budgets"] != first["budgets"] for item in metadata[1:]):
        issues.append("effective task budgets differ")
    if any(item["scored_task_counts"] != first["scored_task_counts"] for item in metadata[1:]):
        issues.append("scored task coverage differs; score denominators are unequal")
    if any(item["task_fingerprints"] != first["task_fingerprints"] for item in metadata[1:]):
        issues.append("task definitions differ or cannot be matched")
    for identity in ("baseline", "iso"):
        # Content hashes take precedence when every run records one.
        hash_key = f"{identity}_sha256"
        key = hash_key if all(item["config"].get(hash_key) for item in metadata) else identity
        if any(item["config"].get(key) != first["config"].get(key) for item in metadata[1:]):
            issues.append(f"{identity} identities differ")
    for key in ("memory_mb", "max_output_tokens", "history_turns", "settle_seconds", "boot_wait"):
        if any(item["config"].get(key) != first["config"].get(key) for item in metadata[1:]):
            issues.append(f"{key.replace('_', ' ')} differs")
    if any(item["config"].get("model_options", {}) != first["config"].get("model_options", {})
           for item in metadata[1:]):
        issues.append("model request budgets or history settings differ")
    if any(item["config"].get("baseline_identity") != first["config"].get("baseline_identity")
           for item in metadata[1:]):
        issues.append("baseline runtime identities differ")
    incomplete = sum(bool(item["metadata_missing"]) for item in metadata)
    if incomplete:
        issues.append(f"comparison metadata is incomplete for {incomplete} run(s)")
    if issues:
        return False, "; ".join(issues) + ". Aggregate scores are descriptive only."
    return True, "Task sets, effective budgets, task definitions, recorded baseline/ISO identities and scoring coverage match."


def compare_results(paths: list[Path]) -> dict:
    """Aggregate only accepted, finite OCR/manual scores; do not score reviews.

    Input paths may be run directories or results.json files. Missing grader
    ``type`` is accepted for older run files; unknown types are not scored.
    Run errors with an accepted visual grade remain visible as execution errors.
    """
    if not paths:
        return {"runs": 0, "models": []}
    groups: dict[tuple[str, str], dict] = {}
    metadata = []
    for value in paths:
        path = Path(value)
        path = path / "results.json" if path.is_dir() else path
        envelope = _load(path)
        run_metadata, _ = _run_metadata(path, envelope)
        metadata.append(run_metadata)
        provider, model = str(envelope.get("provider", "unknown")), str(envelope.get("model", "unknown"))
        group = groups.setdefault((provider, model), {
            "provider": provider, "model": model, "runs": 0, "tasks": 0,
            "scored_tasks": 0, "average_score": None, "passed": 0, "failed": 0,
            "needs_review": 0, "errors": 0, "execution_errors": 0,
            "budget_exhausted": 0, "timeouts": 0,
            "total_input_tokens": 0, "total_output_tokens": 0, "total_tokens": 0,
            "average_steps": None, "average_elapsed_seconds": None, "average_setup_seconds": None,
            "_scores": [], "_steps": [], "_elapsed_seconds": [], "_setup_seconds": [],
        })
        group["runs"] += 1
        for result in envelope["results"]:
            group["tasks"] += 1
            grading = result.get("grade") if isinstance(result.get("grade"), dict) else {}
            grade_status = grading.get("status", "needs_review")
            if grade_status in {"passed", "failed", "needs_review"}:
                group[grade_status] += 1
            elif grade_status == "error":
                group["errors"] += 1
            execution_status = result.get("status")
            if execution_status == "error":
                group["execution_errors"] += 1
            elif execution_status == "budget_exhausted":
                group["budget_exhausted"] += 1
            elif execution_status == "timeout":
                group["timeouts"] += 1
            if _accepted_score(grading):
                group["_scores"].append(float(grading["score"]))
            usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            input_tokens = input_tokens if _nonnegative_number(input_tokens) else 0
            output_tokens = output_tokens if _nonnegative_number(output_tokens) else 0
            total_tokens = usage.get("total_tokens")
            total_tokens = total_tokens if _nonnegative_number(total_tokens) else input_tokens + output_tokens
            group["total_input_tokens"] += input_tokens
            group["total_output_tokens"] += output_tokens
            group["total_tokens"] += total_tokens
            for field in ("steps", "elapsed_seconds", "setup_seconds"):
                if _nonnegative_number(result.get(field)):
                    group[f"_{field}"].append(result[field])
    models = []
    for key in sorted(groups):
        group = groups[key]
        scores = group.pop("_scores")
        group["scored_tasks"] = len(scores)
        group["average_score"] = sum(scores) / len(scores) if scores else None
        for field in ("steps", "elapsed_seconds", "setup_seconds"):
            values = group.pop(f"_{field}")
            group[f"average_{field}"] = sum(values) / len(values) if values else None
        models.append(group)
    comparable, reason = _comparability(metadata)
    return {"runs": len(paths), "comparable": comparable, "comparison_reason": reason,
            "run_metadata": metadata, "models": models}
