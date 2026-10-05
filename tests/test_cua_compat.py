import asyncio
import base64
from io import BytesIO
import json

from PIL import Image
import pytest

from temple_cua.cua_compat import CuaProtocolError, _action, _cua_output, _known_cost, _wire_history, register_openai_loop


def flat_tool_arguments(action, **active):
    # This is the observed upstream flat tool shape, including NONNULL
    # inactive defaults returned by the real OpenAI Responses API.
    fields = {"action": action, "x": 0, "y": 0, "text": "", "keys": [],
              "scroll_x": 0, "scroll_y": 0, "button": "left",
              "start_x": 0, "start_y": 0, "end_x": 0, "end_y": 0,
              "status": "success"}
    return {**fields, **active}


@pytest.mark.parametrize("arguments, expected", [
    (flat_tool_arguments("type", text='Print("HELLO\\n");'),
     {"type": "type", "text": 'Print("HELLO\\n");'}),
    (flat_tool_arguments("keypress", keys=["enter"]),
     {"type": "keypress", "keys": ["enter"]}),
])
def test_real_flat_schema_defaults_are_projected_onto_selected_action(arguments, expected):
    assert _action(arguments, 640, 480) == expected


def test_projected_flat_defaults_do_not_hide_invalid_active_values_or_unknown_fields():
    for arguments in (flat_tool_arguments("type", text="\u2603"),
                      flat_tool_arguments("keypress", keys="enter"),
                      flat_tool_arguments("type", text="HELLO", host_command="anything")):
        with pytest.raises(CuaProtocolError):
            _action(arguments, 640, 480)


def test_history_bridge_keeps_function_result_and_visible_image_separate():
    history = [
        {"type": "computer_call", "call_id": "c1", "action": {"type": "type", "text": "HELLO"}},
        {"type": "computer_call_output", "call_id": "c1", "output":
         {"type": "input_image", "image_url": "data:image/png;base64,abc"}},
    ]
    output = _wire_history(history)
    assert [item.get("type") for item in output] == ["function_call", "function_call_output", None]
    assert json.loads(output[0]["arguments"]) == {"action": "type", "text": "HELLO"}
    assert json.loads(output[1]["output"]) == {"ok": True, "screenshot": "attached"}
    assert output[2]["content"][0]["image_url"] == "data:image/png;base64,abc"
    assert history[0]["action"]["type"] == "type"


@pytest.mark.parametrize("arguments", [
    {"action": "launch_shell", "text": "anything"},
    {"action": "type", "text": "HELLO", "host_command": "anything"},
    {"action": "type", "text": "\u2603"},
    {"action": "click", "x": 640, "y": 1},
    {"action": "click", "x": True, "y": 1},
    {"action": "move", "x": 1.5, "y": 1},
    {"action": "keypress", "keys": "enter"},
    {"action": "scroll", "x": 2, "y": 2, "scroll_y": 1.5},
    {"action": "scroll", "x": 2, "y": 2, "scroll_y": True},
    {"action": "keypress", "keys": ["invented_key"]},
    {"action": "keypress", "keys": ["enter", "return"]},
    {"action": "wait", "seconds": float("nan")},
    {"action": "terminate", "status": "maybe"},
])
def test_invalid_model_actions_are_rejected(arguments):
    with pytest.raises(ValueError):
        _action(arguments, 640, 480)


def test_drag_and_right_click_are_canonical_and_reversible():
    right = _action({"action": "right_click", "x": 1, "y": 2}, 640, 480)
    assert right == {"type": "click", "x": 1, "y": 2, "button": "right"}
    drag = _action({"action": "drag", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4}, 640, 480)
    wire = _wire_history([{"type": "computer_call", "call_id": "drag", "action": drag}])
    assert json.loads(wire[0]["arguments"]) == {"action": "drag", "start_x": 1, "start_y": 2, "end_x": 3, "end_y": 4}


def test_scroll_preserves_integer_pixel_distance_and_accepts_zero():
    assert _action({"action": "scroll", "x": 0, "y": 0, "scroll_y": 2400}, 640, 480) == {
        "type": "scroll", "x": 0, "y": 0, "scroll_x": 0, "scroll_y": 2400}
    for amount in (0, 100000, -(10 ** 100)):
        assert _action({"action": "scroll", "x": 0, "y": 0, "scroll_y": amount}, 640, 480) == {
            "type": "scroll", "x": 0, "y": 0, "scroll_x": 0, "scroll_y": amount}
    with pytest.raises(ValueError, match="vertical"):
        _action({"action": "scroll", "x": 0, "y": 0, "scroll_x": 120, "scroll_y": 120}, 640, 480)


def test_unknown_function_remains_protocol_error_and_mixed_termination_rejects_batch():
    with pytest.raises(ValueError, match="Unexpected OpenAI function"):
        _cua_output([{"type": "function_call", "name": "host_shell"}], 640, 480)
    calls = [{"type": "function_call", "name": "computer", "call_id": "c1",
              "arguments": '{"action":"type","text":"HELLO"}'},
             {"type": "function_call", "name": "computer", "call_id": "c2",
              "arguments": '{"action":"terminate","status":"success"}'}]
    rejected = _cua_output(calls, 640, 480)
    assert all(item["action"] == {"type": "rejected"} for item in rejected)
    assert "retry valid actions separately" in rejected[0]["validation_error"]["message"]
    assert "separately" in rejected[1]["validation_error"]["message"]
    assert [item["rejected_arguments"] for item in rejected] == [item["arguments"] for item in calls]


@pytest.mark.parametrize("raw", [None, 1, {}])
def test_missing_json_string_is_a_protocol_error(raw):
    with pytest.raises(ValueError):
        _cua_output([{"type": "function_call", "name": "computer", "call_id": "c1", "arguments": raw}], 640, 480)


@pytest.mark.parametrize("raw", ["not-json", "[]", "null", '{"action":"unsupported"}',
                                 '{"action":"click","x":640,"y":1}', "[" * 1200 + "]" * 1200])
def test_model_argument_errors_become_recoverable_calls_with_original_evidence(raw):
    rejected = _cua_output([{"type": "function_call", "name": "computer", "call_id": "c1",
                             "arguments": raw}], 640, 480)
    assert len(rejected) == 1
    assert rejected[0]["action"] == {"type": "rejected"}
    assert rejected[0]["call_id"] == "c1"
    assert rejected[0]["rejected_arguments"] == raw
    assert rejected[0]["validation_error"]["code"] == "invalid_computer_action"


@pytest.mark.parametrize("items", [
    [{"type": "function_call", "name": "computer", "arguments": "{}"}],
    [{"type": "function_call", "name": "computer", "call_id": "same", "arguments": "{}"},
     {"type": "function_call", "name": "computer", "call_id": "same", "arguments": "{}"}],
    [{"type": "unexpected"}],
    [None],
])
def test_structural_protocol_errors_are_not_model_action_feedback(items):
    with pytest.raises(CuaProtocolError):
        _cua_output(items, 640, 480)


def test_usage_keeps_measured_tokens_and_only_available_positive_cost():
    usage = {"input_tokens": 17, "output_tokens": 9, "total_tokens": 26, "response_cost": 0}
    _known_cost(usage)
    assert usage == {"input_tokens": 17, "output_tokens": 9, "total_tokens": 26}
    usage["response_cost"] = 0.002
    _known_cost(usage)
    assert usage["response_cost"] == 0.002


def test_actual_cua_loop_uses_exact_model_and_valid_second_turn(monkeypatch):
    monkeypatch.setenv("CUA_TELEMETRY_ENABLED", "false")
    cua = pytest.importorskip("cua_agent")
    import litellm
    from cua_agent.computers import CustomComputerHandler
    from cua_agent.decorators import find_agent_config, get_agent_configs

    image = BytesIO()
    Image.new("RGB", (640, 480), "black").save(image, format="PNG")
    png = image.getvalue()
    requests, typed, usages = [], [], []

    async def fake_responses(**kwargs):
        requests.append(kwargs)
        assert kwargs["model"] == "openai/gpt-6.1-sol"
        assert "640x480" in kwargs["tools"][0]["description"]
        if len(requests) == 1:
            arguments = flat_tool_arguments("type", text="HELLO")
        else:
            types = [item.get("type") for item in kwargs["input"]]
            assert "computer_call" not in types and "computer_call_output" not in types
            call = next(item for item in kwargs["input"] if item.get("type") == "function_call")
            output = next(item for item in kwargs["input"] if item.get("type") == "function_call_output")
            assert call["call_id"] == output["call_id"] == "c1"
            assert any(item.get("role") == "user" and isinstance(item.get("content"), list)
                       and item["content"][0].get("type") == "input_image" for item in kwargs["input"])
            arguments = flat_tool_arguments("terminate", status="success")
        return {"output": [{"type": "function_call", "name": "computer",
                            "call_id": f"c{len(requests)}", "arguments": json.dumps(arguments)}],
                "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}}

    class UsageCallback:
        async def on_usage(self, usage):
            usages.append(dict(usage))

    monkeypatch.setattr(litellm, "aresponses", fake_responses)
    register_openai_loop()
    count = len(get_agent_configs())
    register_openai_loop()
    assert len(get_agent_configs()) == count
    assert find_agent_config("openai/gpt-6.1-sol").agent_class.__name__ == "TempleOpenAIComputerUseConfig"
    assert find_agent_config("openai/computer-use-preview").agent_class.__name__ == "OpenAIComputerUseConfig"

    async def run():
        handler = CustomComputerHandler({"screenshot": lambda: png, "dimensions": (640, 480),
                                         "environment": "linux", "type": typed.append})
        agent = cua.ComputerAgent(model="openai/gpt-6.1-sol", tools=[handler],
                                  callbacks=[UsageCallback()], telemetry_enabled=False,
                                  max_retries=0, screenshot_delay=0)
        return [result async for result in agent.run([{"role": "user", "content": [
            {"type": "input_text", "text": "Print HELLO"},
            {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode()},
        ]}])]

    results = asyncio.run(run())
    assert len(requests) == 2 and typed == ["HELLO"]
    assert len(usages) == 2
    assert all(u["input_tokens"] == 11 and u["output_tokens"] == 3 for u in usages)
    assert all("response_cost" not in u for u in usages)
    assert all("response_cost" not in r["usage"] for r in results)
    assert results[-1]["output"][0]["content"][0]["text"] == "Task complete (success)."


@pytest.mark.parametrize("bad_arguments", [
    "not-json",
    json.dumps(flat_tool_arguments("unsupported")),
    json.dumps(flat_tool_arguments("scroll", scroll_x=1, scroll_y=480)),
    json.dumps(flat_tool_arguments("keypress", keys=["invented_key"])),
])
def test_actual_cua_rejects_whole_batch_then_repairs_with_matching_errors_and_current_image(monkeypatch, bad_arguments):
    monkeypatch.setenv("CUA_TELEMETRY_ENABLED", "false")
    cua = pytest.importorskip("cua_agent")
    import litellm
    from cua_agent.computers import CustomComputerHandler

    def png(color):
        buffer = BytesIO()
        Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
        return buffer.getvalue()

    before, after = png("red"), png("green")
    before_url = "data:image/png;base64," + base64.b64encode(before).decode()
    after_url = "data:image/png;base64," + base64.b64encode(after).decode()
    requests, typed, usages, captured, outputs = [], [], [], [], []
    valid_arguments = json.dumps(flat_tool_arguments("type", text="MUST_NOT_RUN"))

    async def fake_responses(**kwargs):
        requests.append(kwargs)
        assert kwargs["model"] == "openai/gpt-6.1-sol"
        if len(requests) == 1:
            calls = [{"type": "function_call", "name": "computer", "call_id": call_id, "arguments": arguments}
                     for call_id, arguments in (("valid-in-rejected-batch", valid_arguments),
                                                ("invalid", bad_arguments))]
            # A trailing assistant message must not end the loop before repair.
            calls.append({"type": "message", "role": "assistant", "content": [
                {"type": "output_text", "text": "Attempting the batch", "annotations": []}], "status": "completed"})
        elif len(requests) == 2:
            assert typed == []
            history = kwargs["input"]
            errors = {item["call_id"]: json.loads(item["output"]) for item in history
                      if item.get("type") == "function_call_output"}
            assert set(errors) == {"valid-in-rejected-batch", "invalid"}
            assert all(not error["ok"] and error["input_executed"] is False for error in errors.values())
            assert all(error["error"]["code"] == "invalid_computer_action" for error in errors.values())
            assert "retry valid actions separately" in errors["valid-in-rejected-batch"]["error"]["message"]
            arguments_by_id = {item["call_id"]: item["arguments"] for item in history
                               if item.get("type") == "function_call"}
            assert arguments_by_id == {"valid-in-rejected-batch": valid_arguments, "invalid": bad_arguments}
            images = [part["image_url"] for item in history if isinstance(item.get("content"), list)
                      for part in item["content"] if part.get("type") == "input_image"]
            assert images[-2:] == [before_url, before_url]
            calls = [{"type": "function_call", "name": "computer", "call_id": "repaired",
                      "arguments": json.dumps(flat_tool_arguments("type", text="REPAIRED"))}]
        else:
            assert len(requests) == 3 and typed == ["REPAIRED"]
            history = kwargs["input"]
            repaired = next(item for item in history if item.get("type") == "function_call_output"
                            and item["call_id"] == "repaired")
            assert json.loads(repaired["output"])["ok"] is True
            assert history[-1]["content"][0]["image_url"] == after_url
            calls = [{"type": "function_call", "name": "computer", "call_id": "done",
                      "arguments": json.dumps(flat_tool_arguments("terminate"))}]
        return {"output": calls, "usage": {"input_tokens": 11, "output_tokens": 3, "total_tokens": 14}}

    class Callback:
        calls = 0

        async def on_api_start(self, kwargs):
            self.calls += 1
            assert self.calls <= 3

        async def on_usage(self, usage):
            usages.append(dict(usage))

        async def on_responses(self, kwargs, response):
            outputs.append(response["output"])

    def screenshot():
        captured.append(list(typed))
        return after if typed else before

    monkeypatch.setattr(litellm, "aresponses", fake_responses)
    register_openai_loop()
    callback = Callback()

    async def run():
        handler = CustomComputerHandler({"screenshot": screenshot, "dimensions": (640, 480),
                                         "environment": "linux", "type": typed.append})
        agent = cua.ComputerAgent(model="openai/gpt-6.1-sol", tools=[handler], callbacks=[callback],
                                  telemetry_enabled=False, max_retries=0, screenshot_delay=0)
        return [result async for result in agent.run([{"role": "user", "content": [
            {"type": "input_text", "text": "Recover from invalid input"},
            {"type": "input_image", "image_url": before_url},
        ]}])]

    results = asyncio.run(run())
    assert len(requests) == callback.calls == len(usages) == 3
    assert typed == ["REPAIRED"] and captured == [[], ["REPAIRED"]]
    assert sum(usage["total_tokens"] for usage in usages) == 42
    assert sum(result["usage"]["total_tokens"] for result in results) == 42
    rejected_calls = [item for item in outputs[0] if item.get("type") == "computer_call"]
    assert all(item["action"] == {"type": "rejected"} for item in rejected_calls)
    assert [item["rejected_arguments"] for item in rejected_calls] == [valid_arguments, bad_arguments]
    assert [item["type"] for item in outputs[0][-2:]] == ["computer_call_output", "computer_call_output"]
    assert results[-1]["output"][0]["content"][0]["text"] == "Task complete (success)."
