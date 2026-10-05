import json

import httpx
from PIL import Image
import pytest

from temple_cua.providers import AnthropicProvider, OpenAIProvider, ProviderError, ScriptedProvider


@pytest.fixture
def screenshot(tmp_path):
    path = tmp_path / "screen.png"
    Image.new("RGB", (640, 480), "black").save(path)
    return path


def decide(provider, screenshot, history=None):
    return provider.decide(task="Print HELLO", screenshot=screenshot, history=history or [], step=1)


def test_openai_uses_responses_with_screenshot_and_common_tool(screenshot):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "response-1",
                "model": "test-openai-model",
                "status": "completed",
                "output": [
                    {
                        "type": "function_call",
                        "name": "act",
                        "arguments": json.dumps({"actions": [{"kind": "key", "keys": ["enter"]}], "note": "Submit"}),
                    }
                ],
                "usage": {"input_tokens": 30, "output_tokens": 10},
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAIProvider("test-openai-model", api_key="test-key", client=client)
        result = decide(provider, screenshot, [{"step": 0, "actions": [{"kind": "wait", "seconds": 1}]}])
    assert result.actions[0].keys == ["enter"]
    assert result.usage["tokens"]["input_tokens"] == 30
    assert result.usage["input_tokens"] == 30
    assert result.usage["output_tokens"] == 10
    assert result.usage["total_tokens"] == 40
    request = requests[0]
    assert str(request.url) == "https://api.openai.com/v1/responses"
    assert request.headers["authorization"] == "Bearer test-key"
    body = json.loads(request.content)
    assert body["model"] == "test-openai-model"
    assert body["store"] is False
    content = body["input"][0]["content"]
    assert content[1]["image_url"].startswith("data:image/png;base64,")
    assert '"step": 0' in content[0]["text"]
    assert body["tools"][0]["name"] == "act"
    assert body["tools"][0]["strict"] is True
    assert body["parallel_tool_calls"] is False


def test_action_schema_has_strict_objects_for_every_kind():
    from temple_cua.protocol import action_json_schema

    schema = action_json_schema()
    variants = schema["anyOf"]
    assert len(variants) == 7
    for variant in variants:
        assert set(variant["properties"]) == set(variant["required"])
        assert variant["additionalProperties"] is False
        assert len(variant["properties"]["kind"]["enum"]) == 1


def test_anthropic_uses_messages_and_forces_same_action_tool(screenshot):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "message-1",
                "content": [{"type": "tool_use", "id": "tool-1", "name": "act", "input": {"actions": [{"kind": "done", "text": "Complete"}], "note": "Verified"}}],
                "usage": {"input_tokens": 20, "output_tokens": 12, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 5},
                "stop_reason": "tool_use",
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = decide(AnthropicProvider("test-claude-model", api_key="test-key", client=client), screenshot)
    assert result.actions[0].kind == "done"
    assert result.usage["provider"] == "anthropic"
    assert result.usage["input_tokens"] == 35
    assert result.usage["output_tokens"] == 12
    assert result.usage["total_tokens"] == 47
    request = requests[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "test-key"
    body = json.loads(request.content)
    assert body["tool_choice"]["disable_parallel_tool_use"] is True
    assert body["messages"][0]["content"][1]["source"]["media_type"] == "image/png"


@pytest.mark.parametrize(
    "arguments",
    [
        "not-json",
        {"actions": [], "note": ""},
        {"actions": [{"kind": "click", "x": 999, "y": 2}], "note": ""},
        {"actions": [{"kind": "done", "text": "Done"}, {"kind": "wait"}], "note": ""},
        {"actions": [{"kind": "wait"}], "note": 12},
        {"actions": [{"kind": "wait"}], "note": "", "unexpected": "field"},
    ],
)
def test_invalid_actions_never_reach_runner(screenshot, arguments):
    def handler(request):
        return httpx.Response(200, json={"output": [{"type": "function_call", "name": "act", "arguments": arguments}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = OpenAIProvider("test", api_key="test-key", client=client)
        with pytest.raises(ProviderError):
            decide(provider, screenshot)


def test_http_errors_are_sanitized_and_do_not_fallback(screenshot):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(404, json={"error": "SECRET reflected credential"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="HTTP 404") as exc:
            decide(OpenAIProvider("requested-model", api_key="SECRET", client=client), screenshot)
    assert "SECRET" not in str(exc.value)
    assert len(requests) == 1


def test_rate_limit_retries_are_bounded(screenshot, monkeypatch):
    requests = []
    sleeps = []
    monkeypatch.setattr("temple_cua.providers.time.sleep", sleeps.append)

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"retry-after": "900"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="HTTP 429"):
            decide(OpenAIProvider("test", api_key="test-key", client=client, retries=2), screenshot)
    assert len(requests) == 3
    assert sleeps == [4, 4]


def test_retries_share_one_timeout_budget(screenshot, monkeypatch):
    now = [100.0]
    budgets = []
    monkeypatch.setattr("temple_cua.providers.time.monotonic", lambda: now[0])
    monkeypatch.setattr("temple_cua.providers.time.sleep", lambda delay: now.__setitem__(0, now[0] + delay))

    def handler(request):
        budgets.append(request.extensions["timeout"]["read"])
        now[0] += 3
        return httpx.Response(429, headers={"retry-after": "1"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderError, match="timeout including retries"):
            decide(OpenAIProvider("test", api_key="test-key", client=client, timeout=5), screenshot)
    assert budgets == [5, 1]
    assert now[0] == 107


def test_backoff_cannot_extend_shared_timeout(screenshot, monkeypatch):
    now = [100.0]
    sleeps = []
    monkeypatch.setattr("temple_cua.providers.time.monotonic", lambda: now[0])

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay

    monkeypatch.setattr("temple_cua.providers.time.sleep", sleep)
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, headers={"retry-after": "4"}))) as client:
        with pytest.raises(ProviderError, match="timeout including retries"):
            decide(OpenAIProvider("test", api_key="test-key", client=client, timeout=2), screenshot)
    assert sleeps == [2]


def test_missing_credentials_error_is_explicit(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="OPENAI_API_KEY"):
        OpenAIProvider("requested-model")


def test_wrong_screenshot_size_rejected(tmp_path):
    path = tmp_path / "bad.png"
    Image.new("RGB", (800, 600)).save(path)
    with pytest.raises(ProviderError, match="640x480"):
        decide(OpenAIProvider("test", api_key="test-key"), path)


def test_scripted_provider_loads_turns_and_exhaustion_is_not_success(tmp_path, screenshot):
    path = tmp_path / "script.json"
    path.write_text(json.dumps({"steps": [
        {"actions": [{"kind": "wait", "seconds": 0}], "note": "Observe"},
        {"actions": [{"kind": "done", "text": "Verified"}], "note": ""},
    ]}))
    provider = ScriptedProvider(path)
    assert decide(provider, screenshot).actions[0].kind == "wait"
    assert decide(provider, screenshot).actions[0].kind == "done"
    with pytest.raises(ProviderError, match="exhausted"):
        decide(provider, screenshot)


def test_scripted_validates_all_turns_before_input():
    with pytest.raises(ProviderError):
        ScriptedProvider([
            {"actions": [{"kind": "wait"}], "note": ""},
            {"actions": [{"kind": "click", "x": -1, "y": 0}], "note": ""},
        ])
