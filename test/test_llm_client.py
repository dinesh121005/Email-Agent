import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

import pytest

from llm_client import OllamaClient, OllamaClientError


def test_generate_returns_message_content():
    fake_response = {
        "message": {
            "role": "assistant",
            "content": '{"category": "hackathon"}',
            "thinking": "This field should be ignored.",
        }
    }

    with patch("llm_client.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps(fake_response).encode("utf-8")
        )

        client = OllamaClient()
        result = client.generate("Classify this email")

    assert result == '{"category": "hackathon"}'


def test_generate_rejects_empty_prompt():
    client = OllamaClient()

    with pytest.raises(ValueError, match="Prompt must not be empty"):
        client.generate("   ")


def test_generate_wraps_connection_errors_as_retryable():
    with patch(
        "llm_client.urlopen",
        side_effect=URLError("connection refused"),
    ):
        client = OllamaClient()

        with pytest.raises(
            OllamaClientError, match="Could not reach Ollama"
        ) as exc_info:
            client.generate("Hello")

        assert exc_info.value.retryable is True


def test_generate_wraps_timeout_errors_as_retryable():
    with patch(
        "llm_client.urlopen",
        side_effect=TimeoutError("Request timed out"),
    ):
        client = OllamaClient()

        with pytest.raises(
            OllamaClientError, match="Could not reach Ollama"
        ) as exc_info:
            client.generate("Hello")

        assert exc_info.value.retryable is True


def test_http_429_is_retryable():
    err = HTTPError("http://localhost:11434/api/chat", 429, "Too Many Requests", {}, None)
    with patch("llm_client.urlopen", side_effect=err):
        client = OllamaClient()

        with pytest.raises(OllamaClientError, match="HTTP 429") as exc_info:
            client.generate("Hello")

        assert exc_info.value.retryable is True


def test_http_500_is_retryable():
    err = HTTPError("http://localhost:11434/api/chat", 500, "Internal Server Error", {}, None)
    with patch("llm_client.urlopen", side_effect=err):
        client = OllamaClient()

        with pytest.raises(OllamaClientError, match="HTTP 500") as exc_info:
            client.generate("Hello")

        assert exc_info.value.retryable is True


def test_http_400_is_not_retryable():
    err = HTTPError("http://localhost:11434/api/chat", 400, "Bad Request", {}, None)
    with patch("llm_client.urlopen", side_effect=err):
        client = OllamaClient()

        with pytest.raises(OllamaClientError, match="HTTP 400") as exc_info:
            client.generate("Hello")

        assert exc_info.value.retryable is False


def test_invalid_json_is_not_retryable():
    with patch("llm_client.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = b"invalid json"

        client = OllamaClient()

        with pytest.raises(
            OllamaClientError, match="invalid JSON response"
        ) as exc_info:
            client.generate("Hello")

        assert exc_info.value.retryable is False


def test_generate_rejects_missing_content():
    fake_response = {"done": True}

    with patch("llm_client.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__.return_value.read.return_value = (
            json.dumps(fake_response).encode("utf-8")
        )

        client = OllamaClient()

        with pytest.raises(
            OllamaClientError, match="missing message.content"
        ) as exc_info:
            client.generate("Hello")

        assert exc_info.value.retryable is False
