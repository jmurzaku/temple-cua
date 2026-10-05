"""Run host UART checks after a model episode, while its VM remains open.

Host commands and observations are retained separately from model trajectories.
The numeric reward measures observable protocol and recovery behavior. It does
not establish arbitrary ring-0 code safety or exclusive interrupt processing.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time

from PIL import Image

from .bitmap_text import read_bitmap_text
from .protocol import Action


SERIAL_CASE_NAMES = (
    "valid_short", "random_payload_64", "fragmented_request", "back_to_back_burst_8",
    "bad_checksum_then_valid", "valid_after_bad_checksum", "invalid_length_then_valid",
    "embedded_header_payload",
)
COMMAND_DEADLINE_SECONDS = 60.0
CHALLENGE_OBSERVATION_SECONDS = 5.0


def _make_probe(vm, judge):
    # Imported lazily so ordinary non-UART harness runs have no dependency on
    # this optional, QEMU-specific hardware inspection path.
    from .uart_hardware import UARTHardwareProbe
    return UARTHardwareProbe(vm, judge)


def _compare_restoration(before, after):
    from .uart_hardware import compare_restoration
    return compare_restoration(before, after)


def _error(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


def _challenge(vm, purpose: str, path: Path, deadline: float, *, stop=False) -> dict:
    """Require an exact new output line whose value is absent from its echo."""
    nonce = os.urandom(8).hex()
    values = os.urandom(3)
    a, b, c = (1 + value % 97 for value in values)
    label = f"UA_{purpose}_{nonce}"
    expected = f"{label}={a * b + c}"
    # Padding keeps the first label character clear of the window's left
    # border. Clearing the terminal prevents prior long source listings from
    # leaving the new output below the visible document viewport.
    print_statement = f'"\\n  {label}=%d\\n",{a}*{b}+{c};'
    # A compound statement prevents a failed BenchStop() from being followed
    # by an independently evaluated print statement that would falsely pass.
    # HolyC's interactive lexer can wait for lookahead after a bare closing
    # brace. The explicit trailing semicolon finishes this command without
    # requiring input from the next host challenge.
    command = "{DocClear;" + ("BenchStop();" if stop else "") + print_statement + "};\n"
    started = time.monotonic()
    record = {"purpose": purpose, "command": command, "expected_line": expected,
              "screenshot": path.name, "status": "failed", "observed_text": "", "observations": []}
    try:
        vm.execute(Action("type", text=command), deadline=min(deadline, started + 15.0))
        observation_deadline = min(deadline, time.monotonic() + CHALLENGE_OBSERVATION_SECONDS)
        while True:
            vm.screenshot(path)
            raw_text = read_bitmap_text(path)
            with Image.open(path) as screenshot:
                if screenshot.size == (640, 480):
                    # The vertical task title at the left border can decode
                    # as valid ASCII rather than U+FFFD. Remove only known
                    # native border cells; retain the original PNG and text.
                    box = (8, 16, 632, 472)
                    recognized = read_bitmap_text(screenshot.crop(box))
                    record["extraction_crop_box"] = list(box)
                else:
                    recognized = raw_text
            record["observed_text"] = recognized
            record["observed_full_frame_text"] = raw_text
            # Remove border cells only, never damage inside the fresh label.
            lines = [line.strip().strip("\ufffd").strip() for line in recognized.splitlines()]
            matches = lines.count(expected)
            record["observations"].append({"at_ms": round((time.monotonic() - started) * 1000, 3),
                                            "exact_line_matches": matches, "observed_text": recognized,
                                            "observed_full_frame_text": raw_text})
            if matches == 1:
                record["status"] = "passed"
                record["reason"] = "Exact fresh arithmetic output line observed"
                break
            if matches > 1 or time.monotonic() >= observation_deadline:
                record["reason"] = "Fresh output line missing or ambiguous; command echo is insufficient"
                break
            remaining = max(0.0, min(0.1, observation_deadline - time.monotonic()))
            vm.execute(Action("wait", seconds=remaining), deadline=deadline)
    except Exception as exc:
        record["reason"] = record["error"] = _error(exc)
        # Retain the screen even if typing or settling failed, when possible.
        try:
            vm.screenshot(path)
        except Exception:
            pass
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return record


def _cleanup(vm, probe, initial_hardware: dict, folder: Path, deadline: float) -> dict:
    commands = []
    snapshots = []
    comparisons = []
    errors = []
    for index in (1, 2):
        commands.append(_challenge(vm, f"STOP{index}", folder / f"evaluation-stop-{index}.png",
                                   deadline, stop=True))
        if probe is None:
            snapshots.append(None)
            comparisons.append({"status": "error", "reason": "Hardware inspection unavailable"})
            continue
        try:
            snapshot = probe.capture_state()
            snapshots.append(snapshot)
            comparisons.append(_compare_restoration(initial_hardware, snapshot))
        except Exception as exc:
            snapshots.append(None)
            comparisons.append({"status": "error", "reason": _error(exc)})
            errors.append(_error(exc))
    commands.append(_challenge(vm, "ALIVE", folder / "evaluation-liveness.png", deadline))
    errors.extend(command["error"] for command in commands if command.get("error"))
    passed = (all(item["status"] == "passed" for item in commands)
              and all(item.get("status") == "passed" for item in comparisons))
    unverifiable = bool(errors) or any(item.get("status") == "error" for item in comparisons)
    return {"name": "safe_stop_restore_liveness", "status": "error" if unverifiable else "passed" if passed else "failed",
            "reason": ("BenchStop returned twice, checked hardware was restored after each call, and shell responded"
                       if passed else "Stop return, hardware restoration, or fresh shell response could not be verified"),
            "host_commands": commands, "hardware_after_each_stop": snapshots,
            "restoration_checks": comparisons, "errors": errors}


def evaluate_uart(vm, judge, initial_hardware: dict, artifact_dir: Path) -> dict:
    """Return a ten-check grade and save host-only ``evaluation.json``.

    Call only after policy actions end, with a connected UARTJudge and a
    hardware snapshot captured before the policy starts. This function uses a
    new OS-generated seed even if the socket was connected before that episode.
    It never modifies the model's action history or step counters.
    """
    folder = Path(artifact_dir)
    folder.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    deadline = started + COMMAND_DEADLINE_SECONDS
    errors = []
    judge.seed = int.from_bytes(os.urandom(32), "big")
    try:
        protocol = judge.run()
        if not isinstance(protocol, dict) or not isinstance(protocol.get("cases"), list) or any(
                not isinstance(case, dict) for case in protocol["cases"]):
            raise ValueError("UARTJudge returned malformed host evaluation evidence")
    except Exception as exc:
        errors.append(_error(exc))
        protocol = {"status": "error", "reason": _error(exc), "cases": [],
                    "seed_hex": format(judge.seed, "x")}
    checks = []
    protocol_cases = protocol.get("cases", [])
    for name in SERIAL_CASE_NAMES:
        matches = [case for case in protocol_cases if case.get("name") == name]
        case = matches[0] if len(matches) == 1 else None
        # Require actual matching replies, including valid recovery after bad
        # requests. Silence or a claimed aggregate score cannot earn credit.
        passed = bool(case and case.get("status") == "passed"
                      and case.get("expected_replies_hex")
                      and case.get("received_replies_hex") == case["expected_replies_hex"]
                      and not case.get("anomalies"))
        case_error = protocol.get("status") == "error" or bool(case and case.get("infrastructure_error"))
        if case and case.get("infrastructure_error"):
            errors.append(case.get("reason", "UART transport observation failed"))
        if not case and not case_error:
            errors.append(f"UARTJudge omitted or duplicated required case: {name}")
            case_error = True
        checks.append({"name": name, "status": "error" if case_error else "passed" if passed else "failed",
                       "score": None if case_error else 0.1 if passed else 0.0,
                       "reason": case.get("reason", "Exact replies not verified") if case
                       else "Named protocol case missing or duplicated"})
    functional = any(check["status"] == "passed" for check in checks)
    probe = None
    try:
        probe = _make_probe(vm, judge)
        if probe is None:
            raise RuntimeError("Hardware probe construction returned no inspector")
    except Exception as exc:
        errors.append(_error(exc))
    if functional and probe is not None:
        try:
            dependency = probe.irq4_dependence()
        except Exception as exc:
            errors.append(_error(exc))
            dependency = {"status": "error", "reason": _error(exc), "infrastructure_error": True}
    else:
        dependency = {"status": "skipped", "reason": "Requires at least one valid serial case and hardware inspection"}
    irq_passed = functional and dependency.get("status") == "passed"
    irq_error = bool(dependency.get("infrastructure_error")) or (functional and probe is None)
    if dependency.get("infrastructure_error"):
        errors.append(dependency.get("reason", "IRQ4 host probe failed"))
    checks.append({"name": "irq4_mask_dependency", "status": "error" if irq_error else "passed" if irq_passed else "failed",
                   "score": None if irq_error else 0.1 if irq_passed else 0.0,
                   "reason": dependency.get("reason", "IRQ4 dependence not verified")})
    # Always attempt cleanup, even when its credit is gated off, so a failed
    # solution is still evaluated for safe stop behavior and useful evidence.
    cleanup = _cleanup(vm, probe, initial_hardware, folder, deadline)
    errors.extend(cleanup["errors"])
    cleanup_passed = functional and cleanup["status"] == "passed"
    cleanup_error = cleanup["status"] == "error"
    checks.append({"name": "safe_stop_restore_liveness", "status": "error" if cleanup_error else "passed" if cleanup_passed else "failed",
                   "score": None if cleanup_error else 0.1 if cleanup_passed else 0.0,
                   "reason": cleanup["reason"] if functional else "Credit requires at least one valid serial case"})
    passed = sum(check["status"] == "passed" for check in checks)
    evidence = ["evaluation.json"] + [path.name for path in sorted(folder.glob("evaluation-*.png"))]
    limits = ["IRQ4 masking tests causal dependence for one challenge; it does not prove every RX path uses interrupts.",
              "Behavioral checks do not prove bounded buffering, race freedom, or absence of privileged modifications.",
              "Write-only UART FIFO details and pending device contents cannot all be restored or inspected.",
              "Fresh shell output rejects ordinary echoes and constants; it is not a security boundary against malicious ring-0 code."]
    grade = {"type": "uart", "status": "error" if errors else "passed" if passed == 10 else "failed",
             "score": None if errors else passed / 10,
             "passed_cases": passed, "total_cases": 10,
             "reason": "Host UART evaluation could not be completed: " + errors[0] if errors
             else f"{passed}/10 host UART checks passed",
             "cases": checks, "evidence": evidence, "limitations": limits}
    evaluation = {"schema_version": 1, "phase": "host_evaluation_after_policy",
                  "created_at": datetime.now(timezone.utc).isoformat(),
                  "elapsed_seconds": round(time.monotonic() - started, 3),
                  "command_deadline_seconds": COMMAND_DEADLINE_SECONDS,
                  "timeout_scope": "Cleanup commands use an absolute deadline; UART and QMP calls have individual timeouts.",
                  "grade": grade,
                  "initial_hardware": initial_hardware, "protocol": protocol,
                  "irq4_dependence": dependency, "cleanup": cleanup, "errors": errors,
                  "model_actions_added": 0}
    (folder / "evaluation.json").write_text(json.dumps(evaluation, indent=2) + "\n")
    return grade
