import asyncio
import importlib.util
import json
from pathlib import Path
import time

from PIL import Image
import pytest

from temple_cua import cua_runner
from temple_cua.cua_runner import CuaBudgetExceeded, CuaOptions, EpisodeCallback, TempleCuaComputer
from temple_cua.tasks import Task
from temple_cua.vm import VMConfig, VMInputError


class FakeVM:
    instances = []

    def __init__(self, config, work_dir):
        self.config, self.closed, self.actions, self.events = config, False, [], []
        self.__class__.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.closed = True

    def execute(self, action, *, deadline=None):
        self.actions.append(action)
        if action.kind == "key" and action.keys == ["bad-key"]:
            raise VMInputError("Unsupported key")
        if action.kind == "type" and action.text == "timeout":
            raise TimeoutError("Deadline reached")

    def screenshot(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (640, 480), "white").save(path)
        return path

    def _events(self, events, deadline=None):
        self.events.extend(events)


@pytest.fixture
def fake_vm(monkeypatch):
    FakeVM.instances = []
    monkeypatch.setattr(cua_runner, "TempleVM", FakeVM)
    return FakeVM


@pytest.fixture
def upstream():
    if importlib.util.find_spec("cua_agent") is None:
        pytest.skip("Install .[cua] to exercise the upstream ComputerAgent")
    cua_runner.load_cua()


def task():
    return Task(id="simple", title="Simple", prompt="Type HELLO", grader={"type": "manual", "rubric": "Review"}, max_steps=4)


def config():
    return VMConfig(Path("image.iso"), baseline=Path("baseline.qcow2"))


def fixture_file(tmp_path, first=None):
    path = tmp_path / "fixture.json"
    path.write_text(json.dumps([
        {"actions": first or [{"kind": "type", "text": "HELLO"}, {"kind": "key", "keys": ["enter"]}]},
        {"actions": [{"kind": "done", "text": "Observed HELLO"}]},
    ]))
    return path


def test_real_computer_agent_fixture_preserves_frames_and_external_grade(upstream, fake_vm, tmp_path):
    folder = tmp_path / "episode"
    result = cua_runner.run_cua_task(task(), config(), folder, CuaOptions("openai/fixture", fixture=fixture_file(tmp_path)))
    assert result["status"] == "completed"
    assert result["steps"] == 2
    assert result["grade"]["status"] == "needs_review"
    assert result["grade"]["score"] is None
    assert result["usage"]["total_tokens"] == 0
    assert [a.kind for a in fake_vm.instances[0].actions] == ["type", "key"]
    assert fake_vm.instances[0].closed
    records = [json.loads(line) for line in (folder / "trajectory.jsonl").read_text().splitlines()]
    assert records[0]["executed_actions"][0]["text"] == "HELLO"
    assert records[0]["screenshot_after"] == "0001.png"
    assert (folder / "0000.png").is_file() and (folder / "0002.png").is_file()


def test_actual_api_attempt_budget_stops_before_another_prediction(upstream, fake_vm, tmp_path):
    result = cua_runner.run_cua_task(task(), config(), tmp_path / "episode", CuaOptions("openai/fixture", fixture=fixture_file(tmp_path)), max_steps=1)
    assert result["status"] == "budget_exhausted"
    assert result["steps"] == 1
    assert "model call" in result["budget_reason"]
    assert fake_vm.instances[0].closed


def test_action_timeout_retains_last_frame_and_vm_cleanup(upstream, fake_vm, tmp_path):
    folder = tmp_path / "episode"
    result = cua_runner.run_cua_task(task(), config(), folder, CuaOptions("openai/fixture", fixture=fixture_file(tmp_path, [{"kind": "type", "text": "timeout"}])))
    assert result["status"] == "timeout"
    assert result["steps"] == 1
    assert (folder / "final.png").is_file()
    assert fake_vm.instances[0].closed


def test_cua_suite_fixture_provenance_cannot_be_mistaken_for_model_results(upstream, fake_vm, tmp_path):
    output = tmp_path / "suite"
    result = cua_runner.run_cua_suite([task()], config(), output, CuaOptions("openai/fixture", fixture=fixture_file(tmp_path)))
    assert result["provenance"]["execution_mode"] == "deterministic_fixture"
    assert result["provenance"]["agent_version"] == "0.9.0"
    assert result["provenance"]["telemetry_enabled"] is False
    assert result["model"] == "deterministic-fixture (no model API)"
    assert (output / "report.html").is_file()


def computer(tmp_path):
    vm = FakeVM(config(), tmp_path)
    return TempleCuaComputer(vm, tmp_path, time.monotonic() + 30, max_actions=50)


def test_scroll_maps_pixels_to_bounded_ps2_wheel_notches(tmp_path):
    adapter = computer(tmp_path)
    asyncio.run(adapter.scroll(80, 120, 0, -240))
    assert [action.kind for action in adapter.vm.actions] == ["move", "scroll"]
    assert adapter.vm.actions[1].direction == "up"
    assert adapter.vm.actions[1].amount == 2
    adapter.vm.actions.clear()
    with pytest.raises(VMInputError, match="vertical"):
        asyncio.run(adapter.scroll(80, 120, 20, 240))
    assert adapter.vm.actions == []


def test_invalid_drag_is_rejected_before_any_input(tmp_path):
    adapter = computer(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(adapter.drag([{"x": 10, "y": 20}, {"x": 800, "y": 20}]))
    assert adapter.vm.actions == []
    assert adapter.vm.events == []


def test_mouse_state_released_when_drag_input_fails(tmp_path):
    adapter = computer(tmp_path)
    original = adapter.vm.execute

    def fail(action, *, deadline=None):
        if action.x == 20:
            raise TimeoutError("Action expired")
        return original(action, deadline=deadline)

    adapter.vm.execute = fail
    with pytest.raises(TimeoutError):
        asyncio.run(adapter.drag([{"x": 10, "y": 20}, {"x": 20, "y": 20}]))
    assert adapter.left_held is False
    assert [event["data"]["down"] for event in adapter.vm.events] == [True, False]


def test_multi_action_response_limit_precedes_dispatch(tmp_path):
    adapter = computer(tmp_path)
    callback = EpisodeCallback(adapter, 4)
    with pytest.raises(CuaBudgetExceeded, match="four"):
        asyncio.run(callback.on_llm_end([{"type": "computer_call", "action": {"type": "type", "text": "X"}}] * 5))
    assert adapter.vm.actions == []


def test_image_history_keeps_matching_call_outputs_for_parallel_batches(tmp_path):
    callback = EpisodeCallback(computer(tmp_path), 4, image_history=1)
    messages = [
        {"role": "user", "content": [{"type": "input_text", "text": "Task"}, {"type": "input_image", "image_url": "initial"}]},
        {"type": "computer_call", "call_id": "a", "action": {"type": "wait"}},
        {"type": "computer_call", "call_id": "b", "action": {"type": "wait"}},
        {"type": "computer_call_output", "call_id": "a", "output": {"type": "input_image", "image_url": "after-a"}},
        {"type": "computer_call_output", "call_id": "b", "output": {"type": "input_image", "image_url": "after-b"}},
    ]
    result = asyncio.run(callback.on_llm_start(messages))
    assert [item["call_id"] for item in result if item.get("type") == "computer_call"] == ["b"]
    assert [item["call_id"] for item in result if item.get("type") == "computer_call_output"] == ["b"]
    assert result[0]["content"] == [{"type": "input_text", "text": "Task"}]
    assert len(messages[0]["content"]) == 2  # Do not mutate Cua's retained trace.


@pytest.mark.parametrize("model", ["gpt-6.1-sol", "openai/", "other/model", ""])
def test_provider_prefixed_model_is_required(model):
    with pytest.raises(ValueError, match="provider-prefixed"):
        CuaOptions(model)


def test_holyc_examples_use_one_runtime_backslash():
    from temple_cua.providers import SYSTEM_PROMPT

    for prompt in (cua_runner.INSTRUCTIONS, SYSTEM_PROMPT):
        line = next(line for line in prompt.splitlines() if "Hello" in line)
        assert 'Print("Hello' + chr(92) + 'n");' in line
        assert line.count(chr(92)) == 1


def test_suite_reuses_async_loop_but_restores_each_vm(upstream, fake_vm, tmp_path, monkeypatch):
    loops = []
    original = cua_runner._episode

    async def capture(*args):
        loops.append(asyncio.get_running_loop())
        await original(*args)

    monkeypatch.setattr(cua_runner, "_episode", capture)
    one, two = task(), task()
    one.id, two.id = "one", "two"
    result = cua_runner.run_cua_suite([one, two], config(), tmp_path / "suite", CuaOptions("openai/fixture", fixture=fixture_file(tmp_path)))
    assert loops[0] is loops[1]
    assert len(fake_vm.instances) == 2 and all(vm.closed for vm in fake_vm.instances)
    assert len(result["results"]) == 2
