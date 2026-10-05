"""Run Cua ComputerAgent episodes through the TempleOS QMP backend."""

from __future__ import annotations

import asyncio
import base64
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import math
import os
from pathlib import Path
import shutil
import time
from typing import Any

from PIL import Image

from .grading import grade
from .cua_compat import CuaProtocolError
from .protocol import Action, Decision
from .report import build_report
from .runner import initialize_shell, _write_json
from .tasks import Task
from .vm import TempleVM, VMConfig, VMInputError

CUA_VERSION = "0.9.0"
INSTRUCTIONS = """You are operating TempleOS 5.03 through screenshots and keyboard/mouse actions.
The actual framebuffer is 640x480: absolute pixels x=0..639, y=0..479, origin top left.
The transport environment tag is linux only because Cua has no TempleOS tag.
The guest is TempleOS, NOT Linux; the command line executes HolyC, NOT bash.
There is no guest shell API, host terminal, browser, network or accessibility API.
Type printable US ASCII, using keypress for chords such as ['ctrl','c'] or ['enter'].
HolyC examples: Print("Hello\\n"); prints text; Dir; lists the current directory.
Submit command lines with Enter or a trailing newline. Use screenshots to confirm effects.
Scroll uses vertical pixel distances (positive down, negative up), translated to PS/2 wheel notches.
Use at most four computer actions in each response. Wait only when needed.
After completing the task, verify the visible result, then give a short final text answer.
Your final answer does not decide the grade; an independent host grader evaluates the task.
"""


class CuaBudgetExceeded(RuntimeError):
    """The next inference or input would exceed an episode budget."""


@dataclass(frozen=True)
class CuaOptions:
    model: str
    api_timeout: float = 90
    max_output_tokens: int = 4096
    image_history: int = 2
    base_url: str | None = None
    fixture: Path | None = None

    def __post_init__(self):
        if not isinstance(self.model, str) or not self.model.startswith(("openai/", "anthropic/")) or not self.model.split("/", 1)[1]:
            raise ValueError("Cua requires an exact provider-prefixed model, e.g. openai/gpt-6.1-sol or anthropic/<model-id>")
        if not math.isfinite(self.api_timeout) or self.api_timeout <= 0:
            raise ValueError("Cua API timeout must be positive and finite")
        if type(self.max_output_tokens) is not int or self.max_output_tokens <= 0:
            raise ValueError("Cua output token limit must be a positive integer")
        if type(self.image_history) is not int or not 1 <= self.image_history <= 12:
            raise ValueError("Cua image history must be 1..12")
        if self.fixture is not None and not self.model.startswith("openai/"):
            raise ValueError("Deterministic Cua fixtures use the OpenAI Responses compatibility loop")


def load_cua():
    """Import lazily and suppress Cua's import-time product telemetry too."""
    try:
        installed = version("cua-agent")
    except PackageNotFoundError as exc:
        raise RuntimeError("Install the optional integration with: pip install -e '.[cua]'") from exc
    if installed != CUA_VERSION:
        raise RuntimeError(f"This adapter requires cua-agent=={CUA_VERSION}; found {installed}")
    previous = os.environ.get("CUA_TELEMETRY")
    os.environ["CUA_TELEMETRY"] = "0"
    try:
        from cua_agent import ComputerAgent
        from cua_agent.computers.custom import CustomComputerHandler
        from .cua_compat import register_openai_loop
        register_openai_loop()
    finally:
        if previous is None:
            os.environ.pop("CUA_TELEMETRY", None)
        else:
            os.environ["CUA_TELEMETRY"] = previous
    return ComputerAgent, CustomComputerHandler


class TempleCuaComputer:
    """Cua's async computer dictionary, mapped to validated TempleVM input.

    An explicit CustomComputerHandler wraps this dictionary. This prevents the
    released OpenAI loop from losing dimensions when it inspects the tool.
    """

    def __init__(self, vm: TempleVM, folder: Path, deadline: float, max_actions: int):
        self.vm, self.folder, self.deadline = vm, folder, deadline
        self.max_actions = max_actions
        self.input_actions = 0
        self.screenshots = 0
        self.last_screenshot = "0000.png"
        self.record: dict | None = None
        self.left_held = False

    def _check(self):
        if time.monotonic() >= self.deadline:
            raise TimeoutError("Cua episode deadline reached")

    def tools(self) -> dict:
        return {
            "dimensions": (640, 480), "environment": "linux",
            "screenshot": self.screenshot, "click": self.click,
            "double_click": self.double_click, "type": self.type,
            "keypress": self.keypress, "move": self.move, "scroll": self.scroll,
            "drag": self.drag, "wait": self.wait,
            "left_mouse_down": self.left_mouse_down,
            "left_mouse_up": self.left_mouse_up,
        }

    async def screenshot(self) -> bytes:
        self._check()
        self.screenshots += 1
        relative = f"observations/{self.screenshots:04d}.png"
        path = self.vm.screenshot(self.folder / relative)
        with Image.open(path) as captured:
            if captured.size != (640, 480):
                raise VMInputError(f"Cua requires a 640x480 framebuffer, received {captured.size}")
        self._check()
        self.last_screenshot = relative
        if self.record is not None:
            self.record["screenshot_after"] = relative
        return path.read_bytes()

    def _execute(self, action: Action):
        self._check()
        if self.input_actions >= self.max_actions:
            raise CuaBudgetExceeded("Cua input action budget exhausted")
        self.input_actions += 1
        value = action.to_dict()
        if self.record is not None:
            self.record["actions"].append(value)
        try:
            self.vm.execute(action, deadline=self.deadline)
        except Exception:
            if self.record is not None:
                self.record["partial_action"] = value
            raise
        if self.record is not None:
            self.record["executed_actions"].append(value)

    async def click(self, x, y, button="left"):
        self._execute(Action("click", x=x, y=y, button=button))

    async def double_click(self, x, y):
        self._execute(Action("click", x=x, y=y, clicks=2))

    async def type(self, text):
        self._execute(Action("type", text=text))

    async def keypress(self, keys):
        if isinstance(keys, str):
            keys = keys.split("+") if "+" in keys else [keys]
        self._execute(Action("key", keys=keys))

    async def move(self, x, y):
        self._execute(Action("move", x=x, y=y))

    async def scroll(self, x, y, scroll_x=0, scroll_y=0):
        Action("move", x=x, y=y)  # Validate before sending any partial input.
        if type(scroll_x) is not int or type(scroll_y) is not int or scroll_x != 0:
            raise VMInputError("TempleOS supports integer vertical wheel scrolling only")
        if not 0 < abs(scroll_y) <= 2400:
            raise VMInputError("Vertical scroll must be nonzero and within +/-2400 pixels")
        self._execute(Action("move", x=x, y=y))
        self._execute(Action("scroll", direction="down" if scroll_y > 0 else "up", amount=math.ceil(abs(scroll_y) / 120)))

    async def wait(self, ms=1000):
        if not isinstance(ms, (int, float)) or isinstance(ms, bool) or not math.isfinite(ms) or not 0 <= ms <= 10000:
            raise VMInputError("Cua wait must be 0..10000 milliseconds")
        self._execute(Action("wait", seconds=ms / 1000))

    async def left_mouse_down(self, x=None, y=None):
        self._check()
        if x is None or y is None:
            raise VMInputError("Mouse down requires explicit screenshot coordinates")
        self._execute(Action("move", x=x, y=y))
        if self.left_held:
            raise VMInputError("Left mouse button is already held")
        self.left_held = True
        self.vm._events([{"type": "btn", "data": {"button": "left", "down": True}}], self.deadline)
        self._log_button(True)

    def _log_button(self, down):
        if self.record is not None:
            value = {"kind": "cua_mouse_button", "button": "left", "down": down}
            self.record["actions"].append(value)
            self.record["executed_actions"].append(value)

    async def left_mouse_up(self, x=None, y=None):
        if x is not None or y is not None:
            self._execute(Action("move", x=x, y=y))
        self.release()
        self._log_button(False)

    def release(self):
        if self.left_held:
            try:
                self.vm._events([{"type": "btn", "data": {"button": "left", "down": False}}])
            finally:
                self.left_held = False

    async def drag(self, path):
        if not isinstance(path, list) or not 2 <= len(path) <= 32:
            raise VMInputError("Drag requires 2..32 screenshot points")
        actions = [Action("move", x=p.get("x"), y=p.get("y")) if isinstance(p, dict) and set(p) == {"x", "y"} else None for p in path]
        if any(action is None for action in actions):
            raise VMInputError("Each drag point requires only x and y")
        if self.input_actions + len(actions) > self.max_actions:
            raise CuaBudgetExceeded("Drag would exceed the input action budget")
        try:
            await self.left_mouse_down(actions[0].x, actions[0].y)
            for action in actions[1:]:
                self._execute(action)
        finally:
            self.release()
            self._log_button(False)


class EpisodeCallback:
    """Bound real API attempts and retain local evidence without logging keys."""

    def __init__(self, computer: TempleCuaComputer, max_steps: int, progress=None, image_history=2):
        self.computer, self.max_steps, self.progress = computer, max_steps, progress
        self.image_history = image_history
        self.calls = 0
        self.usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        self.completion_text = ""
        self.current: dict | None = None
        self.started = 0.0

    async def on_llm_start(self, messages):
        # Released Cua's retention callback only removes immediately adjacent
        # call/output pairs. Parallel action batches separate those items, so
        # remove whole pairs by call ID first, avoiding orphaned function calls.
        outputs = [item for item in messages if item.get("type") == "computer_call_output"
                   and isinstance(item.get("output"), dict) and "image_url" in item["output"]]
        keep = outputs[-self.image_history:]
        removed = {item.get("call_id") for item in outputs[:-self.image_history]}
        result = []
        initial_images_left = max(0, self.image_history - len(keep))
        for original in messages:
            if original.get("type") in {"computer_call", "computer_call_output"} and original.get("call_id") in removed:
                continue
            item = deepcopy(original)
            content = item.get("content")
            if item.get("role") == "user" and isinstance(content, list):
                parts = []
                for part in content:
                    if part.get("type") == "input_image":
                        if initial_images_left:
                            initial_images_left -= 1
                            parts.append(part)
                    else:
                        parts.append(part)
                item["content"] = parts or [{"type": "input_text", "text": "Earlier screenshot omitted by image history limit."}]
            result.append(item)
        return result

    def flush(self):
        if self.current is None:
            return
        self.current["duration_seconds"] = round(time.monotonic() - self.started, 3)
        numbered = f"{self.current['step']:04d}.png"
        latest = self.computer.folder / self.computer.last_screenshot
        if latest.is_file():
            shutil.copyfile(latest, self.computer.folder / numbered)
            self.current["screenshot_after"] = numbered
        self.current["observation"] = "Current screenshot follows; independent grading occurs after the episode."
        with (self.computer.folder / "trajectory.jsonl").open("a") as log:
            log.write(json.dumps(self.current) + "\n")
        self.current = None
        self.computer.record = None

    async def on_api_start(self, kwargs):
        del kwargs  # Can contain API credentials. Never persist it.
        self.computer._check()
        if self.calls >= self.max_steps:
            raise CuaBudgetExceeded("Cua model call budget exhausted")
        self.flush()
        self.calls += 1
        self.started = time.monotonic()
        self.current = {"step": self.calls, "note": "", "actions": [], "executed_actions": [],
                        "usage": {}, "screenshot_before": f"{self.calls - 1:04d}.png",
                        "screenshot_after": self.computer.last_screenshot}
        self.computer.record = self.current
        if self.progress:
            self.progress(f"Cua model call {self.calls}/{self.max_steps}")

    async def on_llm_end(self, output):
        calls = [item for item in output if item.get("type") in {"computer_call", "function_call"}]
        if len(calls) > 4:
            raise CuaBudgetExceeded("Cua response exceeds four computer actions")
        allowed = {"click", "double_click", "type", "keypress", "move", "scroll", "drag", "screenshot", "wait", "left_mouse_down", "left_mouse_up"}
        for item in calls:
            if item.get("type") != "computer_call" or item.get("action", {}).get("type") not in allowed:
                raise VMInputError("Cua response requested an unsupported action or function")
        return output

    async def on_usage(self, usage):
        if self.current is not None:
            self.current["usage"] = usage
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = usage.get(name, 0)
            if type(value) is int and value >= 0:
                self.usage[name] += value
        # LiteLLM costs are estimates and unknown model prices commonly become 0.
        cost = usage.get("response_cost")
        if isinstance(cost, (float, int)) and not isinstance(cost, bool) and math.isfinite(cost) and cost > 0:
            self.usage["estimated_cost_usd"] = self.usage.get("estimated_cost_usd", 0) + cost

    async def on_text(self, item):
        text = "\n".join(part.get("text", "") for part in item.get("content", []) if isinstance(part, dict))
        if item.get("role") == "assistant":
            self.completion_text = text[:8192]
        if self.current is not None:
            self.current["note"] = text[:8192]

    async def on_responses(self, kwargs, responses):
        del kwargs
        if self.current is not None:
            self.current["cua_output"] = responses.get("output", [])
            self.current["response_id"] = responses.get("id")


@contextmanager
def fixture_responses(path: Path | None):
    """Replace only network inference; ComputerAgent and guest input stay real."""
    if path is None:
        yield
        return
    import litellm
    loaded = json.loads(path.read_text())
    if isinstance(loaded, dict):
        loaded = loaded.get("steps")
    if not isinstance(loaded, list) or not loaded:
        raise ValueError("Cua fixture requires a nonempty turn list")
    turns = [Decision([Action.from_dict(a) for a in turn["actions"]], note=turn.get("note", "")) for turn in loaded]
    index = 0
    previous = litellm.aresponses

    async def response(**kwargs):
        nonlocal index
        del kwargs
        if index >= len(turns):
            raise RuntimeError("Cua deterministic fixture exhausted")
        turn = turns[index]
        index += 1
        output = []
        for n, action in enumerate(turn.actions):
            if action.kind == "done":
                output.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": action.text}]})
                continue
            args = action.to_dict()
            kind = args.pop("kind")
            args["action"] = {"key": "keypress"}.get(kind, kind)
            if kind == "click":
                clicks = args.pop("clicks")
                if clicks not in {1, 2}:
                    raise ValueError("Cua fixtures support single and double clicks")
                if clicks == 2:
                    args["action"] = "double_click"
                    args.pop("button")
            elif kind == "wait":
                # Cua0.9.0's standard wait action has a fixed one-second delay.
                args.pop("seconds")
            elif kind == "scroll":
                args.update(x=0, y=0, scroll_x=0, scroll_y=args.pop("amount") * (120 if args.pop("direction") == "down" else -120))
            output.append({"type": "function_call", "id": f"fc_fixture_{index}_{n}", "call_id": f"call_fixture_{index}_{n}", "name": "computer", "arguments": json.dumps(args)})
        return {"id": f"resp_fixture_{index}", "model": "deterministic-fixture", "status": "completed", "output": output,
                "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}

    litellm.aresponses = response
    try:
        yield
    finally:
        litellm.aresponses = previous


async def _episode(computer: TempleCuaComputer, callback: EpisodeCallback, task: Task, options: CuaOptions):
    ComputerAgent, CustomComputerHandler = load_cua()
    handler = CustomComputerHandler(computer.tools())
    generation = {"request_timeout": options.api_timeout}
    if options.model.startswith("openai/"):
        generation["max_output_tokens"] = options.max_output_tokens
        generation["parallel_tool_calls"] = False
    else:
        generation["max_tokens"] = options.max_output_tokens
    agent = ComputerAgent(
        model=options.model, tools=[handler], callbacks=[callback], instructions=INSTRUCTIONS,
        only_n_most_recent_images=options.image_history, telemetry_enabled=False,
        max_retries=0, screenshot_delay=0.25, api_base=options.base_url, **generation,
    )
    initial = await computer.screenshot()
    messages = [{"role": "user", "content": [
        {"type": "input_text", "text": task.prompt},
        {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(initial).decode()},
    ]}]
    stream = agent.run(messages)
    try:
        async with asyncio.timeout(max(0.001, computer.deadline - time.monotonic())):
            async for _ in stream:
                pass
    finally:
        await stream.aclose()


def run_cua_task(task: Task, config: VMConfig, artifact_dir: Path, options: CuaOptions,
                 *, max_steps=None, timeout=None, progress=None, async_runner: asyncio.Runner | None = None) -> dict:
    artifact_dir.mkdir(parents=True, exist_ok=False)
    data = asdict(task)
    data.pop("source")
    _write_json(artifact_dir / "task.json", {**data, "source": str(task.source) if task.source else None})
    step_limit = max_steps if max_steps is not None else task.max_steps
    time_limit = timeout if timeout is not None else task.timeout_seconds
    result = {"task_id": task.id, "title": task.title, "status": "budget_exhausted", "steps": 0,
              "elapsed_seconds": 0, "usage": {}, "artifact_dir": artifact_dir.name,
              "final_screenshot": f"{artifact_dir.name}/final.png",
              "grade": {"status": "error", "score": None, "reason": "No screenshot"},
              "budget": {"max_steps": step_limit, "timeout_seconds": time_limit, "max_input_actions": step_limit * 8},
              "task_fingerprint": hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()}
    started = time.monotonic()
    final = artifact_dir / "final.png"
    computer = None
    try:
        with TempleVM(config, artifact_dir / "vm") as vm:
            if config.baseline is None:
                initialize_shell(vm)
            for action in task.setup:
                vm.execute(action)
            vm.screenshot(artifact_dir / "0000.png")
            vm.screenshot(final)
            task_started = time.monotonic()
            result["setup_seconds"] = task_started - started
            computer = TempleCuaComputer(vm, artifact_dir, task_started + time_limit, step_limit * 8)
            callback = EpisodeCallback(computer, step_limit, progress, options.image_history)
            try:
                with fixture_responses(options.fixture):
                    if async_runner is None:
                        asyncio.run(_episode(computer, callback, task, options))
                    else:
                        async_runner.run(_episode(computer, callback, task, options))
                result["status"] = "completed" if callback.completion_text else "budget_exhausted"
                if callback.completion_text:
                    result["completion_text"] = callback.completion_text
            finally:
                computer.release()
                vm.screenshot(final)
                callback.flush()
                result["steps"] = callback.calls
                result["usage"] = callback.usage
                result["input_actions"] = computer.input_actions
                result["observations"] = computer.screenshots
    except CuaBudgetExceeded as exc:
        result["status"] = "budget_exhausted"
        result["budget_reason"] = str(exc)
    except Exception as exc:
        result["status"] = "timeout" if isinstance(exc, TimeoutError) or (computer and time.monotonic() >= computer.deadline) else "error"
        # Model errors can contain headers/request details; retain their class,
        # and include only our own clearly controlled validation messages.
        status = getattr(exc, "status_code", None)
        detail = str(exc) if isinstance(exc, (VMInputError, CuaProtocolError)) else f"HTTP {status}" if type(status) is int else "See provider status and runtime configuration"
        result["error"] = f"{type(exc).__name__}: {detail}"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    if final.exists():
        try:
            result["grade"] = grade(task.grader, final, artifact_dir)
        except Exception as exc:
            result["grade"] = {"status": "error", "score": None, "reason": f"{type(exc).__name__}: grading failed"}
    _write_json(artifact_dir / "result.json", result)
    return result


def run_cua_suite(tasks: list[Task], config: VMConfig, output: Path, options: CuaOptions,
                  *, max_steps=None, timeout=None, progress=None) -> dict:
    load_cua()  # Validate optional dependency before creating artifacts or VMs.
    from cua_agent.decorators import find_agent_config
    agent_config = find_agent_config(options.model)
    output.mkdir(parents=True, exist_ok=False)
    envelope = {"created_at": datetime.now(timezone.utc).isoformat(), "provider": "cua",
                "model": options.model if options.fixture is None else "deterministic-fixture (no model API)",
                "config": {"iso": str(config.iso), "baseline": str(config.baseline) if config.baseline else None,
                           "memory_mb": config.memory_mb, "max_steps_override": max_steps, "timeout_override": timeout,
                           "boot_wait": config.boot_wait,
                           "model_options": {"cua_version": version("cua-agent"), "max_output_tokens": options.max_output_tokens, "api_timeout": options.api_timeout,
                                             "image_history": options.image_history}},
                "provenance": {"agent_package": "cua-agent", "agent_version": version("cua-agent"),
                               "inference": "upstream ComputerAgent and LiteLLM",
                               "agent_loop": agent_config.agent_class.__name__ if agent_config else "unresolved",
                               "openai_loop": "registered TempleOpenAIComputerUseConfig compatibility subclass of upstream OpenAIComputerUseConfig",
                               "backend": "TempleVM/QMP legacy PC devices", "framebuffer": [640, 480],
                               "environment_tag": "linux (Cua transport tag; guest is TempleOS)",
                               "execution_mode": "deterministic_fixture" if options.fixture else "live_model",
                               "telemetry_enabled": False, "max_actions_per_response": 4,
                               "step_definition": "deterministic fixture prediction turns; no API requests" if options.fixture else "actual model API attempts; Cua automatic retries disabled",
                               "cost_source": "LiteLLM estimate only when positive; unknown model prices omitted"},
                "results": []}
    if config.baseline:
        sidecar = Path(str(config.baseline) + ".json")
        if sidecar.is_file():
            metadata = json.loads(sidecar.read_text())
            envelope["config"]["baseline_identity"] = metadata.get("identity")
            envelope["config"]["baseline_sha256"] = metadata.get("baseline_sha256")
    _write_json(output / "results.json", envelope)
    # LiteLLM maintains async logging workers. Keep one event loop for the suite
    # rather than leaving those workers attached to a closed loop after task 1.
    with asyncio.Runner() as async_runner:
        for task in tasks:
            if progress:
                progress(f"Starting {task.id} with Cua")
            result = run_cua_task(task, config, output / task.id, options, max_steps=max_steps, timeout=timeout, progress=progress, async_runner=async_runner)
            envelope["results"].append(result)
            _write_json(output / "results.json", envelope)
            if progress:
                progress(f"{task.id}: {result['status']}; grade={result['grade']['status']}")
    build_report(output)
    return envelope
