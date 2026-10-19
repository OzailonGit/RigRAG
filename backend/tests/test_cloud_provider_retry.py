from unittest.mock import patch

import httpx
import pytest

from app.llm.providers.cloud_provider import CloudProvider, _parse_duration


def test_parse_duration_seconds_only():
    assert _parse_duration("28.822s") == pytest.approx(28.822)


def test_parse_duration_minutes_and_seconds():
    assert _parse_duration("7m12s") == pytest.approx(7 * 60 + 12)


def test_parse_duration_milliseconds():
    assert _parse_duration("500ms") == pytest.approx(0.5)


def _mock_response(status_code: int, headers: dict | None = None, json_body: dict | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        headers=headers or {},
        json=json_body or {"choices": [{"message": {"content": "ok"}}]},
        request=httpx.Request("POST", "https://example.com/chat/completions"),
    )


def test_retries_once_on_429_then_succeeds():
    provider = CloudProvider(base_url="https://example.com", api_key="key", model="test-model")
    responses = [
        _mock_response(429, headers={"retry-after": "0.01"}),
        _mock_response(200, json_body={"choices": [{"message": {"content": "recovered"}}]}),
    ]

    with patch("httpx.post", side_effect=responses) as mock_post:
        result = provider.chat([{"role": "user", "content": "hi"}])

    assert result.content == "recovered"
    assert mock_post.call_count == 2


def test_raises_after_exhausting_retries_on_429():
    provider = CloudProvider(base_url="https://example.com", api_key="key", model="test-model")
    responses = [
        _mock_response(429, headers={"retry-after": "0.01"}),
        _mock_response(429, headers={"retry-after": "0.01"}),
    ]

    with patch("httpx.post", side_effect=responses):
        with pytest.raises(httpx.HTTPStatusError):
            provider.chat([{"role": "user", "content": "hi"}])


def test_does_not_retry_on_non_429_error():
    provider = CloudProvider(base_url="https://example.com", api_key="key", model="test-model")
    responses = [_mock_response(404, json_body={"error": "not found"})]

    with patch("httpx.post", side_effect=responses) as mock_post:
        with pytest.raises(httpx.HTTPStatusError):
            provider.chat([{"role": "user", "content": "hi"}])

    assert mock_post.call_count == 1
