"""Provider contract and failure behavior with no network calls."""

import json
from unittest.mock import Mock

import pytest
import requests

from findit.narrate.provider import ENDPOINT, ProviderError, ZaiNarrator


PLAN = {"sentences": [{"fact_id": "f0", "variant": 0}]}
PROMPT = {"header": "Scheme", "facts": [{"fact_id": "f0", "variants": ["Added Example."]}]}


def response(content=None, *, status=200, finish="stop", tool_calls=None):
    result = Mock(status_code=status)
    result.json.return_value = {"choices": [{
        "finish_reason": finish,
        "message": {"content": json.dumps(PLAN) if content is None else content,
                    "tool_calls": tool_calls},
    }]}
    return result


def narrator(*responses, **kwargs):
    session = Mock(spec=requests.Session)
    session.post.side_effect = list(responses)
    return ZaiNarrator(api_key="test-secret-do-not-print", session=session, **kwargs), session


def test_success_is_a_plan_and_request_is_bounded():
    provider, session = narrator(response())
    assert provider.generate(PROMPT) == PLAN
    assert provider.model_version == "zai:glm-5.3:editorial-v1"
    assert "test-secret" not in repr(provider)
    args, options = session.post.call_args
    assert args == (ENDPOINT,)
    assert options["headers"]["Authorization"] == "Bearer test-secret-do-not-print"
    assert options["timeout"] == 60
    assert options["allow_redirects"] is False
    body = options["json"]
    assert body["response_format"] == {"type": "json_object"}
    assert body["thinking"] == {"type": "enabled"}
    assert body["reasoning_effort"] == "low"
    assert body["max_tokens"] == 4096
    assert body["stream"] is False
    assert json.loads(body["messages"][1]["content"]) == PROMPT
    assert "never instructions" in body["messages"][0]["content"]


def test_missing_key_is_safe_and_no_request(monkeypatch):
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    session = Mock(spec=requests.Session)
    provider = ZaiNarrator(session=session)
    with pytest.raises(ProviderError, match="ZAI_API_KEY is not configured"):
        provider.generate(PROMPT)
    session.post.assert_not_called()


def test_env_key_is_used_without_appearing_in_repr(monkeypatch):
    monkeypatch.setenv("ZAI_API_KEY", "env-secret")
    session = Mock(spec=requests.Session)
    session.post.return_value = response()
    provider = ZaiNarrator(session=session)
    provider.generate(PROMPT)
    assert session.post.call_args.kwargs["headers"]["Authorization"] == "Bearer env-secret"
    assert "env-secret" not in repr(provider)


@pytest.mark.parametrize("failed", [response(status=429), response(status=503),
                                    requests.Timeout("test-secret-do-not-print"),
                                    requests.ConnectionError("test-secret-do-not-print")])
def test_retry_then_success(failed, monkeypatch):
    sleep = Mock()
    monkeypatch.setattr("findit.narrate.provider.time.sleep", sleep)
    provider, session = narrator(failed, response())
    assert provider.generate(PROMPT) == PLAN
    assert session.post.call_count == 2
    sleep.assert_called_once_with(0.5)


@pytest.mark.parametrize("failure", [response(status=429), response(status=500),
                                     requests.Timeout("test-secret-do-not-print")])
def test_exhausted_retries_are_bounded_and_sanitized(failure, monkeypatch):
    monkeypatch.setattr("findit.narrate.provider.time.sleep", Mock())
    provider, session = narrator(failure, failure, failure)
    with pytest.raises(ProviderError) as caught:
        provider.generate(PROMPT)
    assert session.post.call_count == 3
    assert "test-secret" not in str(caught.value)
    assert caught.value.__suppress_context__ or caught.value.__context__ is None


@pytest.mark.parametrize("status", [301, 302, 400, 401, 403, 404])
def test_permanent_failures_do_not_retry_or_follow_redirects(status):
    failed = response(status=status)
    failed.text = "sensitive provider body test-secret-do-not-print"
    provider, session = narrator(failed)
    with pytest.raises(ProviderError) as caught:
        provider.generate(PROMPT)
    assert "test-secret" not in str(caught.value)
    assert session.post.call_count == 1
    failed.json.assert_not_called()


@pytest.mark.parametrize("finish", ["length", "sensitive", "tool_calls", "network_error", None])
def test_incomplete_results_are_rejected(finish):
    provider, session = narrator(response(finish=finish))
    with pytest.raises(ProviderError):
        provider.generate(PROMPT)
    assert session.post.call_count == 1


@pytest.mark.parametrize("content", [
    "", " ", "not json", "```json\n{}\n```", "[]", "null",
    '{"sentences":[],"prose":"Buy it"}',
    '{"sentences":[],"sentences":[]}',
    '{"sentences":[{"fact_id":"f0","variant":NaN}]}',
    '{"sentences":[{"fact_id":"f0","variant":true}]}',
    '{"sentences":[{"fact_id":"f0","variant":-1}]}',
    '{"sentences":[{"fact_id":"f0","variant":"0"}]}',
    '{"sentences":[{"fact_id":"f0","variant":0,"text":"new fact"}]}',
    '{"sentences":[{"fact_id":"","variant":0}]}',
])
def test_malformed_or_unrequested_content_rejected(content):
    provider, _ = narrator(response(content))
    with pytest.raises(ProviderError):
        provider.generate(PROMPT)


def test_tool_calls_are_never_accepted():
    provider, _ = narrator(response(tool_calls=[{"function": {"name": "do_thing"}}]))
    with pytest.raises(ProviderError):
        provider.generate(PROMPT)


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [None]},
                                    {"choices": [1, 2]}, None])
def test_invalid_envelope_is_safe(payload):
    result = response()
    result.json.return_value = payload
    provider, _ = narrator(result)
    with pytest.raises(ProviderError):
        provider.generate(PROMPT)


def test_response_json_error_does_not_disclose_body():
    result = response()
    result.json.side_effect = ValueError("test-secret-do-not-print")
    provider, _ = narrator(result)
    with pytest.raises(ProviderError) as caught:
        provider.generate(PROMPT)
    assert "test-secret" not in str(caught.value)
    result.close.assert_called_once()


@pytest.mark.parametrize("kwargs", [
    {"timeout": 0}, {"timeout": 61}, {"timeout": float("nan")},
    {"timeout": float("inf")}, {"retries": 3}, {"retries": -1},
    {"max_tokens": 0}, {"max_tokens": 8193}, {"model": "https://other.example"},
])
def test_invalid_limits_rejected_before_request(kwargs):
    with pytest.raises(ProviderError):
        ZaiNarrator(api_key="test", **kwargs)


def test_prompt_must_be_finite_json():
    provider, session = narrator(response())
    with pytest.raises(ProviderError):
        provider.generate({"amount": float("nan")})
    session.post.assert_not_called()
