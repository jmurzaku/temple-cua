"""Bridge cua-agent 0.9.0 computer actions to OpenAI function tools.

The upstream OpenAI loop already supplies the model call, tool schema, and
usage hooks. This adapter only translates its Responses wire items into
Cua's computer handler items, and translates the next turn back again.
Imports of Cua remain lazy so the caller can disable telemetry first.
"""

from copy import deepcopy
import json
import math


_registered = False


class CuaProtocolError(ValueError):
    """A controlled, credential-free error validating Cua protocol items."""


def _call_id(item: dict) -> str:
    value = item.get("call_id")
    if not isinstance(value, str) or not value:
        raise CuaProtocolError("Computer call requires a nonempty call_id")
    return value


def _coordinates(arguments: dict, width: int, height: int, x="x", y="y") -> tuple[int, int]:
    px, py = arguments.get(x), arguments.get(y)
    if type(px) is not int or type(py) is not int or not (0 <= px < width and 0 <= py < height):
        raise CuaProtocolError("Computer coordinates must be integer screenshot pixels within the display")
    return px, py


def _action(arguments: dict, width: int, height: int) -> dict:
    if not isinstance(arguments, dict):
        raise CuaProtocolError("Computer arguments must be an object")
    name = arguments.get("action")
    allowed = {
        "click": {"x", "y", "button"},
        "right_click": {"x", "y"},
        "double_click": {"x", "y"},
        "move": {"x", "y"},
        "type": {"text"},
        "keypress": {"keys"},
        "scroll": {"x", "y", "scroll_x", "scroll_y"},
        "drag": {"start_x", "start_y", "end_x", "end_y"},
        "screenshot": set(),
        "wait": set(),
        "terminate": {"status"},
    }
    if not isinstance(name, str) or name not in allowed:
        raise CuaProtocolError("Unknown computer action")
    # The upstream flat function schema has every action's fields. Real
    # Responses calls can populate inactive fields with non-null defaults.
    # Only documented fields may be projected away; active fields remain
    # subject to the same validation below.
    documented = {"action"}.union(*allowed.values())
    if set(arguments) - documented:
        raise CuaProtocolError("Unexpected fields for computer action")
    arguments = {key: value for key, value in arguments.items() if key in ({"action"} | allowed[name])}
    result = {"type": name}
    if name in {"click", "right_click", "double_click", "move", "scroll"}:
        x, y = _coordinates(arguments, width, height)
        result.update(x=x, y=y)
    if name in {"click", "right_click"}:
        button = "right" if name == "right_click" else arguments.get("button", "left")
        if not isinstance(button, str) or button not in {"left", "middle", "right"}:
            raise CuaProtocolError("Unknown computer mouse button")
        result.update(type="click", button=button)
    elif name == "type":
        text = arguments.get("text")
        if not isinstance(text, str) or not 1 <= len(text) <= 4096:
            raise CuaProtocolError("Computer typing requires 1..4096 characters")
        if any(ord(c) > 126 or (ord(c) < 32 and c not in "\n\t") for c in text):
            raise CuaProtocolError("TempleOS typing accepts printable US ASCII, newline and tab")
        result["text"] = text
    elif name == "keypress":
        keys = arguments.get("keys")
        if not isinstance(keys, list) or not 1 <= len(keys) <= 5 or any(
            not isinstance(k, str) or not k or len(k) > 24 for k in keys
        ):
            raise CuaProtocolError("Computer keypress requires 1..5 key names")
        result["keys"] = list(keys)
    elif name == "scroll":
        for key in ("scroll_x", "scroll_y"):
            value = arguments.get(key, 0)
            if type(value) is not int or abs(value) > 2400:
                raise CuaProtocolError("Computer scroll amount must be an integer within -2400..2400 pixels")
            result[key] = value
        if result["scroll_x"] != 0 or result["scroll_y"] == 0:
            raise CuaProtocolError("TempleOS supports nonzero vertical scrolling only")
    elif name == "drag":
        start = _coordinates(arguments, width, height, "start_x", "start_y")
        end = _coordinates(arguments, width, height, "end_x", "end_y")
        result["path"] = [{"x": start[0], "y": start[1]}, {"x": end[0], "y": end[1]}]
    elif name == "terminate":
        status = arguments.get("status", "success")
        if not isinstance(status, str) or status not in {"success", "failure"}:
            raise CuaProtocolError("Unknown computer termination status")
        result["status"] = status
    return result


def _wire_history(messages: list[dict]) -> list[dict]:
    """Keep function outputs and screenshots as separate Responses items."""
    result = []
    for original in messages:
        item = deepcopy(original)
        kind = item.get("type")
        if kind == "computer_call":
            action = item.get("action")
            if not isinstance(action, dict) or not isinstance(action.get("type"), str):
                raise CuaProtocolError("Malformed Cua computer action in history")
            arguments = {k: v for k, v in action.items() if k != "type"}
            arguments["action"] = action["type"]
            if arguments["action"] == "drag":
                path = arguments.pop("path", None)
                if not isinstance(path, list) or len(path) < 2:
                    raise CuaProtocolError("Malformed Cua drag in history")
                arguments.update(start_x=path[0]["x"], start_y=path[0]["y"],
                                 end_x=path[-1]["x"], end_y=path[-1]["y"])
            result.append({"type": "function_call", "name": "computer",
                           "call_id": _call_id(item), "arguments": json.dumps(arguments)})
        elif kind == "computer_call_output":
            output = item.get("output")
            if not isinstance(output, dict) or output.get("type") != "input_image":
                raise CuaProtocolError("Computer output requires an input_image screenshot")
            image_url = output.get("image_url")
            if not isinstance(image_url, str) or not image_url.startswith("data:image/"):
                raise CuaProtocolError("Computer screenshot requires an image data URL")
            result.append({"type": "function_call_output", "call_id": _call_id(item),
                           "output": json.dumps({"ok": True, "screenshot": "attached"})})
            result.append({"role": "user", "content": [{"type": "input_image", "image_url": image_url}]})
        else:
            result.append(item)
    return result


def _cua_output(items: list[dict], width: int, height: int) -> list[dict]:
    if not isinstance(items, list):
        raise CuaProtocolError("OpenAI response output must be an array")
    result = []
    terminated = False
    actions = 0
    for original in items:
        if not isinstance(original, dict):
            raise CuaProtocolError("OpenAI response output item must be an object")
        item = deepcopy(original)
        if item.get("type") == "function_call":
            if item.get("name") != "computer":
                raise CuaProtocolError("Unexpected OpenAI function call")
            call_id = _call_id(item)
            raw = item.get("arguments")
            if not isinstance(raw, str):
                raise CuaProtocolError("OpenAI computer arguments must be a JSON string")
            try:
                arguments = json.loads(raw)
            except (ValueError, TypeError) as exc:
                raise CuaProtocolError("Malformed OpenAI computer arguments") from exc
            action = _action(arguments, width, height)
            if action["type"] == "terminate":
                if terminated:
                    raise CuaProtocolError("Duplicate computer termination")
                terminated = True
                result.append({"type": "message", "role": "assistant", "status": "completed",
                               "content": [{"type": "output_text", "annotations": [],
                                            "text": f"Task complete ({action['status']})."}]})
            else:
                actions += 1
                result.append({"type": "computer_call", "call_id": call_id,
                               "action": action, "pending_safety_checks": [], "status": "completed"})
        elif item.get("type") in {"message", "reasoning"}:
            result.append(item)
        else:
            raise CuaProtocolError("Unexpected OpenAI response output type")
    if terminated and actions:
        raise CuaProtocolError("Computer terminate must be issued separately from input actions")
    return result


def _known_cost(usage: dict) -> None:
    # Cua adds zero when LiteLLM has no price entry for an exact model alias.
    cost = usage.get("response_cost")
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not math.isfinite(cost) or cost <= 0:
        usage.pop("response_cost", None)


def register_openai_loop() -> None:
    """Register the function-tool bridge; retain Cua's native preview route."""
    global _registered
    if _registered:
        return
    from cua_agent.decorators import register_agent
    from cua_agent.loops.openai import OpenAIComputerUseConfig

    @register_agent(models=r"^openai/(?!.*computer-use-preview).+$", priority=1000)
    class TempleOpenAIComputerUseConfig(OpenAIComputerUseConfig):
        async def predict_step(self, messages, model, tools=None, computer_handler=None, **kwargs):
            # Native computer-use-preview already speaks Cua's computer protocol.
            native = "computer-use-preview" in model.lower()
            width, height = await computer_handler.get_dimensions()
            if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
                raise CuaProtocolError("Invalid computer display dimensions")
            usage_hook = kwargs.pop("_on_usage", None)

            async def report_usage(usage):
                _known_cost(usage)
                if usage_hook is not None:
                    await usage_hook(usage)

            response = await super().predict_step(
                messages=messages if native else _wire_history(messages), model=model,
                tools=tools, computer_handler=computer_handler, _on_usage=report_usage, **kwargs,
            )
            if not native:
                response["output"] = _cua_output(response.get("output", []), width, height)
            # The upstream loop inserts zero when this model has no price entry.
            # An absent price is unknown, not a measured zero-dollar charge.
            usage = response.get("usage")
            if isinstance(usage, dict):
                _known_cost(usage)
            return response

    _registered = True
