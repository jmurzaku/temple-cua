"""Visual grading for screenshot-only runs.

OCR is a deliberately weak signal: text visible in a command or editor can satisfy
a check without the requested action having succeeded. It never verifies guest
filesystem or program state. Tasks needing that assurance require human review.
"""

from pathlib import Path
import os
import re
import subprocess
from typing import Any

from PIL import Image

from .bitmap_text import read_bitmap_text


_OCR_LIMITATION = (
    "Heuristic visual evidence only: printed constants, source text or a fake "
    "listing can satisfy text checks; guest state is not verified."
)


def _result(kind: str, status: str, score: float | None, reason: str,
            evidence: list[str] | None = None) -> dict[str, Any]:
    return {"type": kind, "status": status, "score": score,
            "reason": reason, "evidence": evidence or []}


def _normalize(text: str, case_sensitive: bool) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if case_sensitive else text.casefold()


def _literals(value: Any, name: str, *, required: bool = False) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip()
                                          for item in value):
        raise ValueError(f"OCR '{name}' must be a list of nonempty strings")
    if required and not value:
        raise ValueError("OCR 'all' must contain at least one required literal")
    return value


def grade(spec: dict, screenshot: Path, artifact_dir: Path) -> dict:
    """Grade a final screenshot using a grader config (or its containing task).

    OCR requires a nonempty ``all`` list. A nonempty optional ``any`` list adds
    one check that passes when at least one of its literals is present. Literal
    matching normalizes whitespace and defaults to case-insensitive substring
    matching. ``match_mode: line`` requires a literal to equal a physical line;
    ``line_patterns`` and ``last_line_pattern`` add full-line regex constraints.
    These checks reject ordinary command echoes, not intentionally fake output.
    No OCR text or unavailable OCR tooling returns ``error``, never a
    vacuous pass. A manual grader leaves the score unset for a human reviewer.
    """
    if not isinstance(spec, dict):
        return _result("unknown", "error", None, "Grader config must be an object")
    config = spec.get("grader", spec)
    if not isinstance(config, dict):
        return _result("unknown", "error", None, "Grader config must be an object")
    kind = config.get("type", "manual")
    if kind == "manual":
        rubric = config.get("rubric", "Review the final screenshot and trajectory against the task.")
        if isinstance(rubric, list):
            rubric = " ".join(str(item) for item in rubric)
        return _result("manual", "needs_review", None, str(rubric),
                       ["Manual review required; no automatic success claim was made."])
    if kind != "ocr":
        return _result(str(kind), "error", None, f"Unsupported grader type: {kind}")

    evidence = [_OCR_LIMITATION]
    try:
        required = _literals(config.get("all"), "all", required=True)
        alternatives = _literals(config.get("any", []), "any")
        case_sensitive = config.get("case_sensitive", False)
        if type(case_sensitive) is not bool:
            raise ValueError("OCR 'case_sensitive' must be true or false")
        match_mode = config.get("match_mode", "substring")
        if match_mode not in {"substring", "line"}:
            raise ValueError("OCR 'match_mode' must be substring or line")
        patterns = _literals(config.get("line_patterns", []), "line_patterns")
        last_pattern = config.get("last_line_pattern")
        if last_pattern is not None and (not isinstance(last_pattern, str) or not last_pattern.strip()):
            raise ValueError("OCR 'last_line_pattern' must be a nonempty regex string")
        try:
            regex_flags = 0 if case_sensitive else re.IGNORECASE
            compiled_patterns = [re.compile(pattern, regex_flags) for pattern in patterns]
            compiled_last = re.compile(last_pattern, regex_flags) if last_pattern is not None else None
        except re.error as exc:
            raise ValueError(f"Invalid OCR line regex: {exc}") from exc
        crop = config.get("crop")
        if crop is not None and (
            not isinstance(crop, list) or len(crop) != 4
            or any(type(value) is not int for value in crop)
            or crop[0] < 0 or crop[1] < 0 or crop[2] <= 0 or crop[3] <= 0
        ):
            raise ValueError("OCR crop must be [x, y, width, height] with nonnegative origin and positive size")

        artifact_dir = Path(artifact_dir)
        artifact_dir.mkdir(parents=True, exist_ok=True)
        input_path = artifact_dir / "grader-input.png"
        text_path = artifact_dir / "grader-ocr.txt"
        engine = "tesseract"
        recognized = ""
        with Image.open(screenshot) as original:
            source = original.convert("RGB")
            if crop is not None:
                x, y, width, height = crop
                if x + width > source.width or y + height > source.height:
                    raise ValueError("OCR crop extends beyond the screenshot")
                source = source.crop((x, y, x + width, y + height))
            if original.size == (640, 480):
                origin = ((-crop[0]) % 8, (-crop[1]) % 8) if crop else (0, 0)
                recognized = read_bitmap_text(source, origin=origin)
                if _normalize(recognized, case_sensitive):
                    engine = "templeos-bitmap"
                    source.save(input_path)
            if engine == "tesseract":
                # Nonstock frames use a conventional OCR fallback. Preserve pixel edges.
                source.resize((source.width * 3, source.height * 3),
                              Image.Resampling.NEAREST).save(input_path)
        if engine == "tesseract":
            completed = subprocess.run(
                ["tesseract", str(input_path), "stdout", "--psm", "6"],
                capture_output=True, text=True, check=True, timeout=30,
                env={**os.environ, "OMP_THREAD_LIMIT": "1"},
            )
            recognized = completed.stdout
        else:
            evidence.append("Exact stock TempleOS 8x8 glyph decoding; unknown cells remain U+FFFD.")
        text_path.write_text(recognized, encoding="utf-8")
        evidence.append(text_path.name)
        normalized = _normalize(recognized, case_sensitive)
        if not normalized:
            return _result("ocr", "error", None, "OCR produced no text; review the screenshot manually.", evidence)

        physical_lines = [_normalize(line, True) for line in recognized.splitlines() if line.strip()]
        normalized_lines = {_normalize(line, case_sensitive) for line in physical_lines}

        def literal_matched(literal: str) -> bool:
            needle = _normalize(literal, case_sensitive)
            return needle in normalized_lines if match_mode == "line" else needle in normalized

        missing = [literal for literal in required if not literal_matched(literal)]
        any_matched = not alternatives or any(literal_matched(literal) for literal in alternatives)
        missing_patterns = [pattern.pattern for pattern in compiled_patterns
                            if not any(pattern.fullmatch(line) for line in physical_lines)]
        last_matched = (compiled_last is None or bool(physical_lines)
                        and bool(compiled_last.fullmatch(physical_lines[-1])))
        passed = not missing and any_matched and not missing_patterns and last_matched
        if passed:
            reason = "Required visual text matched. " + _OCR_LIMITATION
        else:
            messages = []
            if missing:
                messages.append("Missing required visual text: " + ", ".join(repr(item) for item in missing))
            if not any_matched:
                messages.append("None of the alternative visual literals matched")
            if missing_patterns:
                messages.append("Required output line patterns did not match")
            if not last_matched:
                messages.append("Final visible terminal line did not match the required prompt")
            reason = "; ".join(messages) + ". " + _OCR_LIMITATION
        result = _result("ocr", "passed" if passed else "failed", 1.0 if passed else 0.0,
                         reason, evidence)
        result["engine"] = engine
        result["match_mode"] = match_mode
        return result
    except FileNotFoundError as exc:
        if exc.filename == "tesseract":
            reason = "Tesseract OCR is unavailable; install tesseract-ocr or use a manual grader."
        else:
            reason = f"Screenshot or grading artifact unavailable: {exc}"
    except subprocess.TimeoutExpired:
        reason = "Tesseract OCR timed out after 30 seconds."
    except subprocess.CalledProcessError as exc:
        reason = f"Tesseract OCR failed (exit code {exc.returncode})."
    except (OSError, ValueError, TypeError) as exc:
        reason = f"Cannot grade screenshot: {exc}"
    return _result("ocr", "error", None, reason, evidence)
