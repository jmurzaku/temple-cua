"""One screenshot-only model episode, followed by external serial challenges.

The credential is read without echo, kept in process memory, and never saved.
"""
from datetime import datetime, timezone
import argparse
import json
from pathlib import Path
import secrets
import tempfile
import time

import yaml

from probe_openai_models import read_key
from temple_cua.bitmap_text import read_bitmap_text
from temple_cua.protocol import Action
from temple_cua.providers import OpenAIProvider
from temple_cua.runner import initialize_shell
from temple_cua.vm import TempleVM, VMConfig
from temple_cua.uart_judge import UARTJudge


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2) + "\n")


def standalone_marker_visible(visible: str, marker: str) -> bool:
    # Window borders can be unknown glyphs at the beginning/end of a decoded
    # row. Remove only those edges; never repair text inside the marker.
    return any(line.strip().strip("\ufffd").strip() == marker for line in visible.splitlines())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="gpt-6.1-sol")
    parser.add_argument("--qemu", default="./scripts/qemu-local")
    parser.add_argument("--max-steps", type=int, default=40)
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()
    key = read_key()
    if not key:
        raise SystemExit("No credential provided")
    provider = OpenAIProvider(args.model, api_key=key, timeout=120,
                              max_output_tokens=8192, max_history_steps=16, retries=1)
    key = None
    task = yaml.safe_load(Path("moonshots/uart/task.yaml").read_text())
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "task.json", task)
    # The task's private reward config never reaches the provider prompt.
    result = {"created_at": datetime.now(timezone.utc).isoformat(), "provider": "openai",
              "model": args.model, "task_id": task["id"], "status": "budget_exhausted",
              "steps": 0, "usage": {}, "budget": {"max_steps": args.max_steps, "timeout_seconds": args.timeout},
              "irq_dependence": "not independently proven by this pilot"}
    history = []
    episode_start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="uart-trial-") as transport:
        socket_path = Path(transport) / "serial.sock"
        events = output / "trace-events.txt"
        events.write_text("serial_read\nserial_write\npic_ioport_read\npic_ioport_write\npic_interrupt\n")
        extra = ("-chardev", f"socket,id=telemetry,path={socket_path},server=on,wait=off",
                 "-device", "isa-serial,chardev=telemetry,index=0,iobase=0x3f8,irq=4",
                 "-trace", f"events={events},file={output / 'uart-trace.log'}")
        config = VMConfig(iso=Path("assets/TempleOS.ISO"), qemu_binary=args.qemu,
                          boot_wait=12, extra_args=extra)
        try:
            with TempleVM(config, output / "vm") as vm:
                initialize_shell(vm)
                vm.execute(Action("type", text='Cd("B:/Bench",TRUE);DocClear;\n'))
                judge = UARTJudge(socket_path)
                judge.connect()
                vm.screenshot(output / "0000.png")
                vm.screenshot(output / "final.png")
                task_started = time.monotonic()
                result["setup_seconds"] = task_started - episode_start
                for step in range(1, args.max_steps + 1):
                    remaining = args.timeout - (time.monotonic() - task_started)
                    if remaining <= 0:
                        result["status"] = "timeout"
                        break
                    print(f"MODEL_STEP {step}/{args.max_steps} remaining={remaining:.1f}s", flush=True)
                    provider.timeout = min(120, remaining)
                    began = time.monotonic()
                    decision = provider.decide(task=task["prompt"], screenshot=output / f"{step-1:04d}.png",
                                               history=history, step=step)
                    record = {"step": step, "note": decision.note, "actions": [a.to_dict() for a in decision.actions],
                              "executed_actions": [], "usage": decision.usage}
                    for name in ("input_tokens", "output_tokens", "total_tokens", "latency_seconds"):
                        result["usage"][name] = result["usage"].get(name, 0) + decision.usage.get(name, 0)
                    done = False
                    execution_error = None
                    for action in decision.actions:
                        try:
                            if action.kind == "done":
                                done = True
                                result["completion_text"] = action.text
                            else:
                                vm.execute(action, deadline=task_started + args.timeout)
                            record["executed_actions"].append(action.to_dict())
                        except Exception as exc:
                            execution_error = exc
                            record["error"] = f"{type(exc).__name__}: {exc}"
                            break
                    time.sleep(0.25)
                    vm.screenshot(output / f"{step:04d}.png")
                    vm.screenshot(output / "final.png")
                    record["duration_seconds"] = time.monotonic() - began
                    record["observation"] = record.get("error", "Actions executed. Current screenshot follows.")
                    record["screenshot_after"] = f"{step:04d}.png"
                    with (output / "trajectory.jsonl").open("a") as log:
                        log.write(json.dumps(record) + "\n")
                    history.append(record)
                    result["steps"] = step
                    write_json(output / "result-progress.json", result)
                    print(f"STEP_DONE {step} actions={len(record['executed_actions'])} duration={record['duration_seconds']:.1f}s", flush=True)
                    if execution_error:
                        raise execution_error
                    if done:
                        result["status"] = "completed"
                        break
                result["policy_seconds"] = time.monotonic() - task_started
                print("HOST_SERIAL_EVALUATION", flush=True)
                result["serial_grade"] = judge.run()
                nonce = secrets.token_hex(6).upper()
                marker = "UART_UI_" + nonce
                vm.execute(Action("type", text=f'"{marker}\\n";\n'), deadline=time.monotonic()+15)
                time.sleep(0.2)
                vm.screenshot(output / "after-evaluation.png")
                # Search for the standalone output line, excluding the typed command.
                visible = read_bitmap_text(output / "after-evaluation.png")
                responsive = standalone_marker_visible(visible, marker)
                result["desktop_responsive"] = responsive
                raw_score = result["serial_grade"].get("score", 0)
                result["reward"] = round(0.9*raw_score + (0.1 if responsive and raw_score > 0 else 0), 6)
                result["host_ui_marker"] = marker
                judge.close()
        except Exception as exc:
            result["status"] = "timeout" if isinstance(exc, TimeoutError) else "error"
            result["error"] = f"{type(exc).__name__}: {exc}"
    result["elapsed_seconds"] = time.monotonic() - episode_start
    write_json(output / "result.json", result)
    print(json.dumps({k: result.get(k) for k in ("model", "status", "steps", "reward", "desktop_responsive", "usage", "error")}), flush=True)
    return 1 if result["status"] == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())
