"""Command-line setup, evaluation, and reporting."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import urllib.request

from .providers import OpenAIProvider, AnthropicProvider, ScriptedProvider
from .report import build_report, compare_results
from .runner import initialize_shell, run_suite
from .tasks import create_task, load_tasks
from .vm import TempleVM, VMConfig

ISO_URL = "https://templeos.org/Downloads/TempleOS.ISO"
ISO_SHA256 = "5d0fc944e5d89c155c0fc17c148646715bc1db6fa5750c0b913772cfec19ba26"


def _vm_options(parser):
    parser.add_argument("--iso", type=Path, default=Path("assets/TempleOS.ISO"))
    parser.add_argument("--disk", type=Path, help="Optional existing guest disk; a disposable copy is used")
    parser.add_argument("--baseline", type=Path, help="Prepared qcow2 RAM+disk snapshot")
    parser.add_argument("--qemu", default=os.environ.get("TEMPLE_CUA_QEMU", "qemu-system-x86_64"))
    parser.add_argument("--memory", type=int, default=512, help="Guest memory in MiB")
    parser.add_argument("--boot-wait", type=float, default=20)


def _config(args):
    return VMConfig(iso=args.iso, disk=args.disk, baseline=args.baseline,
                    qemu_binary=args.qemu, memory_mb=args.memory, boot_wait=args.boot_wait)


def _parser():
    parser = argparse.ArgumentParser(description="TempleOS screenshot-only computer-use harness")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch-iso", help="Download and verify the official TempleOS 5.03 live ISO")
    fetch.add_argument("--output", type=Path, default=Path("assets/TempleOS.ISO"))
    prepare = commands.add_parser("prepare", help="Boot the live ISO, initialize its shell and save a baseline")
    _vm_options(prepare)
    prepare.add_argument("--output", type=Path, default=Path("assets/baseline.qcow2"))
    prepare.add_argument("--work-dir", type=Path, default=Path("runs/prepare"))
    prepare.add_argument("--script", type=Path, help="Override trusted initialization with scripted actions")
    listing = commands.add_parser("list-tasks", help="List task IDs and scoring methods")
    listing.add_argument("--tasks", type=Path, default=Path("tasks"))
    new_task = commands.add_parser("new-task", help="Create an editable task YAML")
    new_task.add_argument("id", help="Lowercase task ID used for the YAML filename")
    new_task.add_argument("--tasks", type=Path, default=Path("tasks"))
    new_task.add_argument("--title", help="Task title; defaults to the ID in title case")
    new_task.add_argument("--prompt", help="Instructions the model will receive")
    new_task.add_argument("--expect", action="append", help="Required standalone output line; repeat for multiple lines")
    run = commands.add_parser("run", help="Evaluate one provider against selected tasks")
    _vm_options(run)
    run.add_argument("--provider", choices=["openai", "anthropic", "scripted", "cua"], required=True)
    run.add_argument("--model", help="Exact model API ID; required for live providers")
    run.add_argument("--base-url", help="Override the provider API root")
    run.add_argument("--script", type=Path, help="Scripted provider turn JSON; required for scripted runs")
    run.add_argument("--tasks", type=Path, default=Path("tasks"))
    run.add_argument("--task", action="append", help="Task ID to include; repeat to select multiple")
    run.add_argument("--output", type=Path, help="New artifact directory, must not exist")
    run.add_argument("--max-steps", type=int, help="Override the per-task model turn budget")
    run.add_argument("--timeout", type=float, help="Override the per-task time budget in seconds")
    run.add_argument("--api-timeout", type=float, default=90)
    run.add_argument("--max-output-tokens", type=int, default=4096)
    run.add_argument("--history-steps", type=int, default=12)
    run.add_argument("--cua-image-history", type=int, default=2, help="Recent screenshots retained by real cua-agent (1..12)")
    run.add_argument("--cua-fixture", type=Path, help="Replace Cua network inference with a deterministic JSON fixture; no model is tested")
    report = commands.add_parser("report", help="Rebuild a standalone HTML report")
    report.add_argument("run_dir", type=Path)
    compare = commands.add_parser("compare", help="Summarize scores and budgets across runs")
    compare.add_argument("runs", type=Path, nargs="+")
    doctor = commands.add_parser("doctor", help="Check local runtime tools and credential presence")
    _vm_options(doctor)
    return parser


def _fetch(output):
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        if hashlib.sha256(output.read_bytes()).hexdigest() == ISO_SHA256:
            print(f"Verified {output}")
            return
        raise ValueError(f"{output} exists with an unexpected checksum; choose a new output path")
    temporary = output.with_suffix(output.suffix + ".download")
    try:
        with urllib.request.urlopen(ISO_URL, timeout=60) as response, temporary.open("xb") as dest:
            shutil.copyfileobj(response, dest)
        if hashlib.sha256(temporary.read_bytes()).hexdigest() != ISO_SHA256:
            raise ValueError("Downloaded ISO checksum does not match the pinned official image")
        temporary.rename(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Downloaded and verified {output}")


def main(argv=None):
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "fetch-iso":
            _fetch(args.output)
        elif args.command == "list-tasks":
            for task in load_tasks(args.tasks):
                print(f"{task.id:24} {task.grader['type']:7} {task.max_steps:3} turns  {task.title}")
        elif args.command == "new-task":
            path = create_task(args.id, tasks_dir=args.tasks, title=args.title,
                               prompt=args.prompt, expect=args.expect)
            print(path)
            print(f"Edit the prompt and grader, then run with --task {args.id}.")
        elif args.command == "doctor":
            qemu = shutil.which(args.qemu)
            sibling = Path(qemu).with_name("qemu-img") if qemu else None
            print(json.dumps({"qemu": qemu, "qemu_img": str(sibling) if sibling and sibling.exists() else shutil.which("qemu-img"),
                "tesseract": shutil.which("tesseract"), "iso_exists": args.iso.is_file(),
                "baseline_exists": args.baseline.is_file() if args.baseline else None,
                "OPENAI_API_KEY_present": bool(os.environ.get("OPENAI_API_KEY")),
                "ANTHROPIC_API_KEY_present": bool(os.environ.get("ANTHROPIC_API_KEY"))}, indent=2))
        elif args.command == "prepare":
            if args.output.exists():
                raise ValueError(f"Baseline {args.output} already exists; choose a new output path")
            if args.work_dir.exists():
                raise ValueError(f"Work directory {args.work_dir} already exists; choose a new path")
            with TempleVM(_config(args), args.work_dir) as vm:
                vm.screenshot(args.work_dir / "boot.png")
                if args.script:
                    script = ScriptedProvider(args.script)
                    for step in range(1, 1001):
                        observation = args.work_dir / "current.png"
                        vm.screenshot(observation)
                        decision = script.decide(task="Trusted shell preparation", screenshot=observation, history=[], step=step)
                        done = False
                        for action in decision.actions:
                            if action.kind == "done":
                                done = True
                            else:
                                vm.execute(action)
                        if done:
                            break
                    else:
                        raise ValueError("Preparation script exceeded 1000 turns")
                else:
                    initialize_shell(vm)
                vm.screenshot(args.work_dir / "ready.png")
                vm.save_baseline(args.output)
            print(f"Baseline: {args.output}\nInspect shell screenshot: {args.work_dir / 'ready.png'}")
        elif args.command == "run":
            if args.max_steps is not None and not 1 <= args.max_steps <= 1000:
                parser.error("--max-steps must be 1..1000")
            if args.timeout is not None and not 0 < args.timeout <= 7200:
                parser.error("--timeout must be 0..7200")
            tasks = load_tasks(args.tasks, args.task)
            if args.cua_fixture and args.provider != "cua":
                parser.error("--cua-fixture is only valid with --provider cua")
            if args.provider == "cua":
                from .cua_runner import CuaOptions, run_cua_suite
                if not args.model:
                    parser.error("Cua requires --model with a provider prefix, e.g. openai/gpt-6.1-sol")
                if args.cua_fixture and len(tasks) != 1:
                    parser.error("Select exactly one --task when replaying a Cua fixture")
                options = CuaOptions(model=args.model, api_timeout=args.api_timeout,
                                     max_output_tokens=args.max_output_tokens, image_history=args.cua_image_history,
                                     base_url=args.base_url, fixture=args.cua_fixture)
                model = args.model
            elif args.provider == "scripted":
                if not args.script:
                    parser.error("--script is required for scripted provider")
                if len(tasks) != 1:
                    parser.error("Select exactly one --task when replaying a reference script")
                factory = lambda: ScriptedProvider(args.script)
                model = "scripted-reference"
            else:
                if not args.model:
                    parser.error("An exact --model API ID is required")
                provider_cls = OpenAIProvider if args.provider == "openai" else AnthropicProvider
                kwargs = {"timeout": args.api_timeout, "max_output_tokens": args.max_output_tokens,
                          "max_history_steps": args.history_steps}
                if args.base_url:
                    kwargs["base_url"] = args.base_url
                factory = lambda: provider_cls(args.model, **kwargs)
                factory()  # Validate credentials/options before booting a VM.
                model = args.model
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            output = args.output or Path("runs") / f"{args.provider}-{timestamp}"
            if args.provider == "cua":
                envelope = run_cua_suite(tasks, _config(args), output, options,
                                         max_steps=args.max_steps, timeout=args.timeout,
                                         progress=lambda line: print(line, flush=True))
            else:
                envelope = run_suite(tasks, factory, _config(args), output, provider_name=args.provider,
                                     model=model, max_steps=args.max_steps, timeout=args.timeout,
                                     progress=lambda line: print(line, flush=True),
                                     model_options={"max_output_tokens": args.max_output_tokens,
                                                    "history_steps": args.history_steps,
                                                    "api_timeout": args.api_timeout})
            print(f"Report: {output / 'report.html'}")
            return 1 if any(r["status"] == "error" or r["grade"]["status"] == "error" for r in envelope["results"]) else 0
        elif args.command == "report":
            print(build_report(args.run_dir))
        elif args.command == "compare":
            print(json.dumps(compare_results(args.runs), indent=2))
    except (ValueError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0
