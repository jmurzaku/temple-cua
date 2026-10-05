"""Check the OS description in actual, locally intercepted Cua requests."""

import asyncio

import pytest


@pytest.fixture
def cua_environment(monkeypatch):
    monkeypatch.setenv("CUA_TELEMETRY", "0")
    monkeypatch.setenv("CUA_TELEMETRY_ENABLED", "false")
    pytest.importorskip("cua_agent")
    import litellm
    from cua_agent.computers import CustomComputerHandler
    from cua_agent.decorators import find_agent_config
    from temple_cua.cua_compat import register_openai_loop

    register_openai_loop()
    requests = []

    async def fake_responses(**kwargs):
        requests.append(kwargs)
        return {"output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": "Done"},
        ]}], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}

    async def unexpected_screenshot():
        raise AssertionError("The environment test must not capture or operate a VM")

    monkeypatch.setattr(litellm, "aresponses", fake_responses)

    def handler(dimensions):
        return CustomComputerHandler({"screenshot": unexpected_screenshot,
                                      "dimensions": dimensions, "environment": "linux"})

    return find_agent_config, handler, requests


@pytest.mark.parametrize("dimensions", [(640, 480), (800, 600)])
def test_function_request_describes_templeos_and_preserves_transport_and_dimensions(cua_environment, dimensions):
    find_config, make_handler, requests = cua_environment
    handler = make_handler(dimensions)
    callback_descriptions = []
    other_tool = {"type": "function", "function": {
        "name": "other", "description": "Environment: linux.",
        "parameters": {"type": "object", "properties": {}},
    }}

    async def on_api_start(kwargs):
        callback_descriptions.append(kwargs["tools"][0]["description"])

    async def run():
        config = find_config("openai/gpt-6.1-sol").agent_class()
        await config.predict_step(messages=[{"role": "user", "content": "Finish"}],
                                  model="openai/gpt-6.1-sol", computer_handler=handler,
                                  tools=[{"type": "computer", "computer": handler}, other_tool],
                                  _on_api_start=on_api_start)
        assert await handler.get_environment() == "linux"

    asyncio.run(run())
    assert len(requests) == 1
    computer, other = requests[0]["tools"]
    assert computer["type"] == "function" and computer["name"] == "computer"
    assert "Environment: TempleOS 5.03." in computer["description"]
    assert "The command line executes HolyC." in computer["description"]
    assert "Environment: linux." not in computer["description"]
    assert f"Screen resolution: {dimensions[0]}x{dimensions[1]} pixels." in computer["description"]
    assert callback_descriptions == [computer["description"]]
    assert computer["parameters"]["properties"]["action"]["enum"]
    assert other["description"] == other_tool["function"]["description"]


def test_native_preview_keeps_stock_route_and_linux_transport_schema(cua_environment):
    find_config, make_handler, requests = cua_environment
    config = find_config("openai/computer-use-preview")
    assert config.agent_class.__name__ == "OpenAIComputerUseConfig"
    handler = make_handler((640, 480))

    asyncio.run(config.agent_class().predict_step(
        messages=[{"role": "user", "content": "Finish"}], model="openai/computer-use-preview",
        tools=[{"type": "computer", "computer": handler}], computer_handler=handler,
    ))

    assert requests[0]["tools"] == [{"type": "computer_use_preview", "display_width": 640,
                                    "display_height": 480, "environment": "linux"}]
