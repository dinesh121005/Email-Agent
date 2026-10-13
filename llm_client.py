import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaClientError(Exception):
    """Ollama communication or response error with retry metadata."""

    def __init__(
        self,
        message: str,
        *,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.retryable = retryable


class OllamaClient:
    def __init__(
        self,
        model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 600.0,
    ):
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def generate(self, prompt: str) -> str:
        if not prompt.strip():
            raise ValueError("Prompt must not be empty")

        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }

        request = Request(
            url=f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(
                request, timeout=self.timeout_seconds
            ) as response:
                data = json.loads(
                    response.read().decode("utf-8")
                )

        except HTTPError as exc:
            retryable = exc.code == 429 or 500 <= exc.code <= 599
            raise OllamaClientError(
                f"Ollama returned HTTP {exc.code}",
                retryable=retryable,
            ) from exc

        except (URLError, TimeoutError) as exc:
            raise OllamaClientError(
                "Could not reach Ollama or the request timed out",
                retryable=True,
            ) from exc

        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise OllamaClientError(
                "Ollama returned an invalid JSON response"
            ) from exc

        try:
            content = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise OllamaClientError(
                "Ollama response is missing message.content"
            ) from exc

        if not isinstance(content, str):
            raise OllamaClientError(
                "Ollama message.content must be a string"
            )

        return content
