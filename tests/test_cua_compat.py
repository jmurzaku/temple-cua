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
    {"action": "scroll", "x": 2, "y": 2, "scroll_y": 100000},
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


def test_scroll_pixels_match_runner_notch_conversion():
    assert _action({"action": "scroll", "x": 0, "y": 0, "scroll_y": 2400}, 640, 480) == {
        "type": "scroll", "x": 0, "y": 0, "scroll_x": 0, "scroll_y": 2400}
    for arguments in ({"action": "scroll", "x": 0, "y": 0},
                      {"action": "scroll", "x": 0, "y": 0, "scroll_x": 120, "scroll_y": 120}):
        with pytest.raises(ValueError, match="vertical"):
            _action(arguments, 640, 480)


def test_unknown_function_and_mixed_termination_are_rejected_before_dispatch():
    with pytest.raises(ValueError, match="Unexpected OpenAI function"):
        _cua_output([{"type": "function_call", "name": "host_shell"}], 640, 480)
    calls = [{"type": "function_call", "name": "computer", "call_id": "c1",
              "arguments": '{"action":"type","text":"HELLO"}'},
             {"type": "function_call", "name": "computer", "call_id": "c2",
              "arguments": '{"action":"terminate","status":"success"}'}]
    with pytest.raises(ValueError, match="separately"):
        _cua_output(calls, 640, 480)


@pytest.mark.parametrize("raw", [None, 1, "not-json", "[]", "null"])
def test_malformed_function_arguments_are_rejected(raw):
    with pytest.raises(ValueError):
        _cua_output([{"type": "function_call", "name": "computer", "call_id": "c1", "arguments": raw}], 640, 480)


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
