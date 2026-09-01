"""Ollama HTTP client: chat completions and embeddings.

A thin wrapper over two endpoints. It does not decide what to say, what to
retrieve, or what to remember — it moves requests and responses, and it fails
loudly when it cannot.

**Failure is explicit, never silent and never indefinite.** Every request
carries a timeout, and every failure mode maps to a distinct exception:

* ``OllamaUnreachable``   — nothing is listening. Ollama is not running.
* ``OllamaTimeout``       — reachable but did not answer in time.
* ``OllamaModelNotFound`` — the model is not pulled on this machine.
* ``OllamaResponseError`` — answered, but not with what was asked for.

That granularity is the point. "Something went wrong talking to the model" is
not a useful thing for a caller to receive when the actual situation is "you
have not pulled this model" — and a caller that cannot distinguish a timeout
from an empty answer cannot report honestly to the user which one happened.

**``num_ctx`` is pinned, not left to the server's default.** See
``config/defaults.toml`` for the value and the task 1.2 changelog for the
measurements behind it.

**Embeddings are dimension-checked on every call.** A model swap that silently
changed the vector width would corrupt the store one row at a time, and the
first symptom would be bad retrieval months later rather than an error now.
"""

from __future__ import annotations

import json
from typing import Any, Iterator

import requests

from program import config


class OllamaError(RuntimeError):
    """Base for every failure talking to Ollama."""


class OllamaUnreachable(OllamaError):
    """Nothing is listening at the configured host."""


class OllamaTimeout(OllamaError):
    """Reachable, but did not respond within the timeout."""


class OllamaModelNotFound(OllamaError):
    """The requested model is not available on this machine."""


class OllamaResponseError(OllamaError):
    """Responded, but the payload was not usable."""


class EmbeddingDimensionError(OllamaError):
    """An embedding came back the wrong width for the configured store."""


def _url(path: str) -> str:
    return f"{config.ollama_host().rstrip('/')}{path}"


def _split_options(overrides: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    """Return (options, think).

    Ollama takes ``think`` at the top level of the payload, not inside
    ``options``. Sending it in the wrong place is silently ignored rather than
    rejected, which is the kind of mistake that survives for months.
    """
    options = dict(config.model_options())
    if overrides:
        options.update(overrides)
    think = bool(options.pop("think", False))
    options.pop("timeout_seconds", None)
    return options, think


def _post(path: str, payload: dict[str, Any], *, timeout: int, stream: bool = False):
    try:
        response = requests.post(_url(path), json=payload, timeout=timeout, stream=stream)
    except requests.exceptions.ConnectTimeout as exc:
        raise OllamaTimeout(
            f"Ollama did not accept a connection at {config.ollama_host()} "
            f"within {timeout}s."
        ) from exc
    except requests.exceptions.ReadTimeout as exc:
        raise OllamaTimeout(
            f"Ollama accepted the request but did not respond within {timeout}s. "
            f"A cold model load can exceed this; raise ollama.timeout_seconds if "
            f"that is expected."
        ) from exc
    except requests.exceptions.ConnectionError as exc:
        raise OllamaUnreachable(
            f"Cannot reach Ollama at {config.ollama_host()}. Is it running? "
            f"Check with `ollama ps`."
        ) from exc

    if response.status_code == 404:
        # Ollama returns 404 both for an unknown route and an unpulled model;
        # the body distinguishes them.
        body = response.text.lower()
        if "model" in body:
            raise OllamaModelNotFound(
                f"Ollama does not have this model: {payload.get('model')!r}. "
                f"Pull it with `ollama pull {payload.get('model')}`."
            )
        raise OllamaResponseError(f"Ollama returned 404 for {path}: {response.text[:200]}")

    if response.status_code >= 400:
        raise OllamaResponseError(
            f"Ollama returned HTTP {response.status_code} for {path}: "
            f"{response.text[:300]}"
        )

    return response


def chat(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    options: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    timeout: int | None = None,
) -> dict[str, Any]:
    """One non-streaming chat completion.

    Returns the parsed response dict, which carries ``message`` plus Ollama's
    own counters — ``prompt_eval_count`` and ``eval_count`` — which the history
    windowing in task 1.10 uses to calibrate its token estimate.
    """
    model = model or config.chat_model()
    opts, think = _split_options(options)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": False,
        "think": think,
        "options": opts,
    }
    if tools:
        payload["tools"] = tools

    response = _post(
        "/api/chat",
        payload,
        timeout=timeout if timeout is not None else config.ollama_timeout_seconds(),
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise OllamaResponseError(
            f"Ollama returned a non-JSON body: {response.text[:200]}"
        ) from exc

    if "message" not in data:
        raise OllamaResponseError(
            f"Ollama response had no 'message' field. Keys: {sorted(data)}"
        )
    return data


def chat_text(messages: list[dict[str, Any]], **kwargs: Any) -> str:
    """``chat`` reduced to the assistant's text. Convenience for simple callers."""
    return chat(messages, **kwargs).get("message", {}).get("content", "")


def chat_stream(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    options: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
    timeout: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Stream a chat completion, yielding each decoded chunk.

    The agent loop in task 2.2 consumes this. Malformed lines raise rather than
    being skipped: a dropped chunk is missing output the caller would never know
    it was missing.
    """
    model = model or config.chat_model()
    opts, think = _split_options(options)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "think": think,
        "options": opts,
    }
    if tools:
        payload["tools"] = tools

    response = _post(
        "/api/chat",
        payload,
        timeout=timeout if timeout is not None else config.ollama_timeout_seconds(),
        stream=True,
    )

    try:
        for line in response.iter_lines():
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError as exc:
                raise OllamaResponseError(
                    f"Ollama streamed an undecodable line: {line[:200]!r}"
                ) from exc
    except requests.exceptions.ReadTimeout as exc:
        raise OllamaTimeout("Ollama stopped sending mid-stream.") from exc
    finally:
        response.close()


def embed(text: str, *, model: str | None = None, timeout: int | None = None) -> list[float]:
    """Embed one string, checking the width of what comes back.

    Does **not** truncate over-length input. The embedding model rejects input
    past its context with an HTTP error, and Ollama's own ``truncate`` flag does
    not reliably prevent that — so sizing the input is the application's job, and
    silently shortening it here would hide the fact that a chunk was too big.
    The splitter that enforces the budget is task 1.3.
    """
    model = model or config.embed_model()
    response = _post(
        "/api/embed",
        {"model": model, "input": text},
        timeout=timeout if timeout is not None else config.ollama_timeout_seconds(),
    )

    try:
        data = response.json()
    except ValueError as exc:
        raise OllamaResponseError(
            f"Ollama returned a non-JSON body for /api/embed: {response.text[:200]}"
        ) from exc

    embeddings = data.get("embeddings")
    if not embeddings or not isinstance(embeddings, list):
        raise OllamaResponseError(
            f"Ollama returned no embeddings. Keys: {sorted(data)}"
        )

    vector = embeddings[0]
    expected = config.expected_embedding_dimension()
    if len(vector) != expected:
        raise EmbeddingDimensionError(
            f"Embedding model {model!r} returned {len(vector)} dimensions, "
            f"expected {expected}. The vector store cannot hold both widths — "
            f"either the model changed or embedding.expected_dimension is wrong."
        )
    return vector


def is_available() -> bool:
    """Whether Ollama answers at all. For health reporting, not for control flow.

    Callers that need a model call should just make it and handle the exception:
    checking first and then calling leaves a window where the answer changes,
    and turns one round trip into two.
    """
    try:
        requests.get(_url("/api/version"), timeout=5).raise_for_status()
        return True
    except requests.exceptions.RequestException:
        return False


def loaded_models() -> list[dict[str, Any]]:
    """What Ollama currently holds in memory — the API behind ``ollama ps``."""
    try:
        response = requests.get(_url("/api/ps"), timeout=10)
        response.raise_for_status()
    except requests.exceptions.ConnectionError as exc:
        raise OllamaUnreachable(
            f"Cannot reach Ollama at {config.ollama_host()}."
        ) from exc
    except requests.exceptions.Timeout as exc:
        raise OllamaTimeout("Ollama did not respond to /api/ps.") from exc
    return response.json().get("models", [])
