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


@pytest.mark.parametrize("direction", ["up", "down"])
def test_scroll_fixture_preserves_native_notches(upstream, fake_vm, tmp_path, direction):
    result = cua_runner.run_cua_task(
        task(), config(), tmp_path / "episode",
        CuaOptions("openai/fixture", fixture=fixture_file(tmp_path, [
            {"kind": "scroll", "direction": direction, "amount": 3},
        ])),
    )
    assert result["status"] == "completed"
    assert [a.to_dict() for a in fake_vm.instances[0].actions] == [
        {"kind": "move", "x": 0, "y": 0},
        {"kind": "scroll", "direction": direction, "amount": 3},
    ]


def computer(tmp_path):
    vm = FakeVM(config(), tmp_path)
    return TempleCuaComputer(vm, tmp_path, time.monotonic() + 30, max_actions=50)


def test_scroll_maps_pixels_to_bounded_ps2_wheel_notches(tmp_path):
    adapter = computer(tmp_path)
    asyncio.run(adapter.scroll(80, 120, 0, -240))
    assert [action.kind for action in adapter.vm.actions] == ["move", "scroll", "scroll"]
    assert adapter.vm.actions[1].direction == "up"
    assert [action.amount for action in adapter.vm.actions[1:]] == [20, 10]
    adapter.vm.actions.clear()
    with pytest.raises(VMInputError, match="vertical"):
        asyncio.run(adapter.scroll(80, 120, 20, 240))
    assert adapter.vm.actions == []


@pytest.mark.parametrize("distance", [5000, -5000, 5001])
def test_large_scroll_preserves_quantized_distance_in_bounded_chunks(tmp_path, distance):
    adapter = computer(tmp_path)
    asyncio.run(adapter.scroll(80, 120, 0, distance))
    moves, *scrolls = adapter.vm.actions
    assert moves.kind == "move"
    assert all(action.kind == "scroll" and 1 <= action.amount <= 20 for action in scrolls)
    assert sum(action.amount for action in scrolls) == (abs(distance) + 7) // 8
    assert all(action.direction == ("up" if distance < 0 else "down") for action in scrolls)
    assert adapter.input_actions == len(adapter.vm.actions)


def test_scroll_zero_is_noop_and_impossible_distance_rejects_before_input(tmp_path):
    adapter = computer(tmp_path)
    asyncio.run(adapter.scroll(80, 120, 0, 0))
    assert adapter.input_actions == 0 and adapter.vm.actions == []
    with pytest.raises(VMInputError, match="shorter distance"):
        asyncio.run(adapter.scroll(80, 120, 0, 10**100))
    assert adapter.input_actions == 0 and adapter.vm.actions == []


def test_repeated_mouse_down_rejects_before_moving_pointer(tmp_path):
    adapter = computer(tmp_path)
    asyncio.run(adapter.left_mouse_down(80, 120))
    actions = list(adapter.vm.actions)
    events = list(adapter.vm.events)
    with pytest.raises(VMInputError, match="already held"):
        asyncio.run(adapter.left_mouse_down(180, 220))
    assert adapter.vm.actions == actions and adapter.vm.events == events
    adapter.release()


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


def test_real_upstream_episode_recovers_model_and_runtime_rejections_without_losing_evidence(upstream, fake_vm, tmp_path, monkeypatch):
    import base64
    from io import BytesIO
    import litellm

    def call(call_id, arguments):
        return {"type": "function_call", "name": "computer", "call_id": call_id,
                "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments)}

    turns = [
        [call("malformed", "not-json"), call("batch-sibling", {"action": "type", "text": "MUST NOT EXECUTE"})],
        [call("oversized-scroll", {"action": "scroll", "x": 80, "y": 120,
                                  "scroll_x": 0, "scroll_y": 5000})],
        [call("small-scroll", {"action": "scroll", "x": 80, "y": 120,
                              "scroll_x": 0, "scroll_y": 16}),
         call("corrected-type", {"action": "type", "text": "RECOVERED\n"})],
        [call("finish", {"action": "terminate", "status": "success"})],
    ]
    requests = []

    async def fake_responses(**kwargs):
        requests.append(kwargs)
        attempt = len(requests)
        assert attempt <= 4, "Recovery must not make an extra API request"
        assert kwargs["model"] == "openai/fixture-recovery"
        messages = kwargs["input"]
        calls = {item["call_id"]: item for item in messages if item.get("type") == "function_call"}
        outputs = {item["call_id"]: item for item in messages if item.get("type") == "function_call_output"}
        assert set(calls) == set(outputs), "History trimming must remove whole call/output pairs"
        assert not any(item.get("type") in {"computer_call", "computer_call_output"} for item in messages)
        for index, item in enumerate(messages):
            if item.get("type") != "function_call_output":
                continue
            screenshot = messages[index + 1]
            assert screenshot["role"] == "user"
            image = screenshot["content"][0]
            assert image["type"] == "input_image"
            assert image["image_url"].startswith("data:image/png;base64,")
            png = base64.b64decode(image["image_url"].split(",", 1)[1])
            with Image.open(BytesIO(png)) as captured:
                assert captured.size == (640, 480)
        if attempt <= 3:
            assert fake_vm.instances[0].actions == [], "Rejected calls must send no guest input"
        if attempt == 2:
            assert set(outputs) == {"malformed", "batch-sibling"}
            for original in turns[0]:
                call_id = original["call_id"]
                assert calls[call_id]["arguments"] == original["arguments"]
                rejected = json.loads(outputs[call_id]["output"])
                assert rejected["ok"] is False and rejected["input_executed"] is False
                assert rejected["error"]["code"] == "invalid_computer_action"
                assert rejected["error"]["message"]
                assert rejected["screenshot"] == "attached"
        if attempt == 3:
            assert "malformed" not in calls  # The oldest rejection pair exceeds image_history=2.
            assert "oversized-scroll" in calls
            rejected = json.loads(outputs["oversized-scroll"]["output"])
            assert rejected["ok"] is False and rejected["input_executed"] is False
            assert rejected["error"]["code"] == "invalid_computer_input"
            assert "shorter distance" in rejected["error"]["message"]
            assert json.loads(calls["oversized-scroll"]["arguments"])["scroll_y"] == 5000
        if attempt == 4:
            assert set(calls) == {"small-scroll", "corrected-type"}
            assert all(json.loads(item["output"])["ok"] is True for item in outputs.values())
        return {"id": f"fixture-response-{attempt}", "model": "fixture-response-model",
                "output": turns[attempt - 1],
                "usage": {"input_tokens": attempt * 10, "output_tokens": attempt,
                          "total_tokens": attempt * 11},
                "_hidden_params": {"response_cost": attempt * 0.001}}

    monkeypatch.setattr(litellm, "aresponses", fake_responses)
    folder = tmp_path / "episode"
    result = cua_runner.run_cua_task(task(), config(), folder,
                                     CuaOptions("openai/fixture-recovery", image_history=2), max_steps=4)
    assert result["status"] == "completed" and result["steps"] == len(requests) == 4
    assert result["budget"]["max_steps"] == 4 and result["budget"]["max_input_actions"] == 32
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
                               "estimated_cost_usd": pytest.approx(0.01)}
    assert result["input_actions"] == 3
    assert result["grade"]["status"] == "needs_review" and result["grade"]["score"] is None
    vm = fake_vm.instances[0]
    assert [action.to_dict() for action in vm.actions] == [
        {"kind": "move", "x": 80, "y": 120},
        {"kind": "scroll", "direction": "down", "amount": 2},
        {"kind": "type", "text": "RECOVERED\n"},
    ]
    assert vm.events == [] and vm.closed
    records = [json.loads(line) for line in (folder / "trajectory.jsonl").read_text().splitlines()]
    assert [record["step"] for record in records] == [1, 2, 3, 4]
    expected_requests = [[{key: item[key] for key in ("call_id", "name", "arguments")}
                          for item in turn] for turn in turns]
    assert [record["requested_calls"] for record in records] == expected_requests
    for attempt, record in enumerate(records, start=1):
        assert record["response_model"] == "fixture-response-model"
        assert record["usage"]["input_tokens"] == attempt * 10
        assert record["usage"]["output_tokens"] == attempt
        assert record["usage"]["total_tokens"] == attempt * 11
        assert (folder / record["screenshot_after"]).is_file()
    assert all(records[index]["actions"] == records[index]["executed_actions"] == [] for index in (0, 1))
    assert [error["arguments"] for error in records[0]["input_errors"]] == [item["arguments"] for item in turns[0]]
    assert [error["call_id"] for error in records[0]["input_errors"]] == ["malformed", "batch-sibling"]
    runtime_error = records[1]["input_errors"][0]
    assert runtime_error["call_id"] == "oversized-scroll"
    assert json.loads(runtime_error["arguments"]) == {"type": "scroll", "x": 80, "y": 120,
                                                     "scroll_x": 0, "scroll_y": 5000}
    assert runtime_error["error"]["code"] == "invalid_computer_input"
    assert "shorter distance" in runtime_error["error"]["message"]
    for record in records[:2]:
        for error in record["input_errors"]:
            assert error["input_executed"] is False
            assert (folder / error["screenshot"]).is_file()
            assert error["screenshot"].startswith("observations/")
    assert "input_errors" not in records[2] and "input_errors" not in records[3]
    assert json.loads((folder / "result.json").read_text())["usage"] == result["usage"]


@pytest.mark.parametrize("failure", ["final-screenshot", "release"])
def test_cleanup_infrastructure_failure_retains_flushed_calls_and_usage(upstream, fake_vm, tmp_path, monkeypatch, failure):
    import litellm

    requests = []

    async def fake_responses(**kwargs):
        requests.append(kwargs)
        assert len(requests) <= 2
        arguments = {"action": "type", "text": "HELLO"} if len(requests) == 1 else {
            "action": "terminate", "status": "success"}
        return {"model": "fixture-cleanup-model", "output": [
            {"type": "function_call", "name": "computer", "call_id": f"cleanup-{len(requests)}",
             "arguments": json.dumps(arguments)}],
            "usage": {"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}}

    monkeypatch.setattr(litellm, "aresponses", fake_responses)
    if failure == "final-screenshot":
        original = fake_vm.screenshot
        final_attempts = []

        def fail_final_capture(vm, path):
            if path.name == "final.png":
                final_attempts.append(path)
                if len(final_attempts) == 2:
                    raise RuntimeError("Synthetic final screenshot failure")
            return original(vm, path)

        monkeypatch.setattr(fake_vm, "screenshot", fail_final_capture)
    else:
        def fail_release(computer):
            raise RuntimeError("Synthetic release failure")

        monkeypatch.setattr(TempleCuaComputer, "release", fail_release)
    folder = tmp_path / "episode"
    result = cua_runner.run_cua_task(task(), config(), folder, CuaOptions("openai/fixture-cleanup"))
    assert result["status"] == "error"
    assert result["error"].startswith("RuntimeError:")
    assert result["steps"] == len(requests) == 2
    assert result["usage"] == {"input_tokens": 14, "output_tokens": 6, "total_tokens": 20}
    assert result["input_actions"] == 1
    assert result["grade"]["status"] == "needs_review" and result["grade"]["score"] is None
    assert fake_vm.instances[0].closed
    assert [action.text for action in fake_vm.instances[0].actions] == ["HELLO"]
    records = [json.loads(line) for line in (folder / "trajectory.jsonl").read_text().splitlines()]
    assert [record["step"] for record in records] == [1, 2]
    assert all(record["usage"]["total_tokens"] == 10 for record in records)
    assert [record["requested_calls"][0]["call_id"] for record in records] == ["cleanup-1", "cleanup-2"]
    assert records[1]["executed_actions"] == []
    assert (folder / records[1]["screenshot_after"]).is_file()
    persisted = json.loads((folder / "result.json").read_text())
    assert persisted["status"] == "error" and persisted["steps"] == 2
    assert persisted["usage"] == result["usage"]
