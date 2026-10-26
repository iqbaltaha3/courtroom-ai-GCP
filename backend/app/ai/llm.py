"""
LLM client helpers using Gemini on Vertex AI with GCP Application Default Credentials.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional, TypeVar

import google.auth
import google.auth.transport.requests
from openai import OpenAI
from pydantic import BaseModel

from app.config import (
    GCP_PROJECT_ID,
    GEMINI_MODEL,
    LLM_TIMEOUT_SECONDS,
    VERTEX_OPENAI_BASE,
    vertex_openai_model,
)
from app.services.run_context import SimulationCancelled, check_cancelled
from app.services.usage import model_request

T = TypeVar("T", bound=BaseModel)
logger = logging.getLogger(__name__)

_client: Optional[OpenAI] = None
_credentials: Optional[google.auth.credentials.Credentials] = None


def _access_token() -> str:
    """Load Application Default Credentials and refresh an expired access token."""
    global _credentials
    if not GCP_PROJECT_ID:
        raise RuntimeError("GCP_PROJECT_ID or GOOGLE_CLOUD_PROJECT is not set")
    if _credentials is None:
        _credentials, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    if not _credentials.valid:
        _credentials.refresh(google.auth.transport.requests.Request())
    return _credentials.token


def get_client() -> OpenAI:
    """Reuse the text-completion client while refreshing its authentication token."""
    global _client
    check_cancelled()
    if _client is None:
        _client = OpenAI(
            api_key=_access_token(), base_url=VERTEX_OPENAI_BASE, timeout=LLM_TIMEOUT_SECONDS, max_retries=0
        )
    else:
        _client.api_key = _access_token()
    return _client


def sanitize_legal_prose(text: str) -> str:
    """Strip common markdown / decorative markers from free-text legal output."""
    if not text:
        return text
    # Remove bold/italic markers
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"__([^_]+)__", r"\1", text)
    text = re.sub(r"(?<!\w)\*([^*]+)\*(?!\w)", r"\1", text)
    # Remove heading hashes at line start
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    # Convert markdown bullets at line start to numbered-style plain lines
    text = re.sub(r"(?m)^[\-\*•]\s+", "", text)
    # Remove horizontal rules
    text = re.sub(r"(?m)^(-{3,}|\*{3,}|_{3,})\s*$", "", text)
    # Collapse excess blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _completion(
    system: str, user: str, budget: int, temperature: float, model: Optional[str] = None, **kwargs
):
    """Call the model, record usage, and reject empty or truncated output."""
    check_cancelled()
    # Gemini's completion allowance includes thinking. Reserve it separately
    # instead of letting reasoning consume the entire visible answer budget.
    thinking = int(os.getenv("LLM_THINKING_BUDGET", "2048"))
    started = time.monotonic()
    client = get_client()
    with model_request(model or GEMINI_MODEL, "completion") as metric:
        resp = client.chat.completions.create(
            model=vertex_openai_model(model or GEMINI_MODEL),
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=min(budget + thinking, 60000),
            temperature=temperature,
            extra_body={"extra_body": {"google": {"thinking_config": {"thinking_budget": thinking}}}},
            **kwargs,
        )
        usage = getattr(resp, "usage", None)
        metric["usage"] = usage.model_dump() if usage else {}
    check_cancelled()
    choice = resp.choices[0]
    reason = getattr(choice, "finish_reason", None)
    logger.info(
        "model_completion seconds=%.2f finish=%s output_budget=%s", time.monotonic() - started, reason, budget
    )
    if reason not in (None, "stop"):
        raise ValueError(f"Model output did not complete: {reason}")
    text = (choice.message.content or "").strip()
    if not text:
        raise ValueError("Model returned an empty answer")
    return text


def _retryable(exc: Exception) -> bool:
    """Allow transient failures and validation retries, but never retry cancellation."""
    if isinstance(exc, SimulationCancelled):
        return False
    status = getattr(exc, "status_code", None)
    return status is None or status in (408, 429) or status >= 500


def call_llm(system: str, user: str, max_tokens: int = 2000, temperature: float = 0.3) -> str:
    """Request a complete prose answer with bounded retries and a larger retry budget."""
    attempts = max(1, int(os.getenv("LLM_MAX_RETRIES", "3")))
    for attempt in range(attempts):
        try:
            raw = _completion(system, user, max(4096, max_tokens) * (2 ** min(attempt, 2)), temperature)
            return sanitize_legal_prose(raw)
        except Exception as exc:
            if attempt == attempts - 1 or not _retryable(exc):
                raise
            # Longer backoff on 429 resource exhausted
            status = getattr(exc, "status_code", None)
            base = 8.0 if status == 429 else 1.5
            time.sleep(base * (attempt + 1))
    raise RuntimeError("LLM retries exhausted")


def call_structured(
    system: str,
    user: str,
    schema: type[T],
    max_tokens: int = 2000,
    temperature: float = 0.2,
    model: Optional[str] = None,
) -> T:
    """Force JSON output matching a Pydantic schema."""
    full_system = (
        system
        + "\n\nReturn the complete answer as JSON using the enforced response schema."
        + "\n\nDo not copy, describe, or return the schema itself."
        + "\nReturn one concrete JSON object containing the requested answer data."
        + "\nDo not wrap in markdown. Output pure JSON."
    )
    last_err = None
    attempts = max(1, int(os.getenv("LLM_MAX_RETRIES", "3")))
    for attempt in range(attempts):
        try:
            retry_note = ""
            if attempt:
                retry_note = (
                    "\n\nPrevious output was invalid. Return ONLY the final answer object, "
                    "not a JSON Schema object. The top-level keys must be the schema field "
                    f"names for {schema.__name__}."
                )
            message = user + retry_note
            text = _completion(
                full_system,
                message,
                max(4096, max_tokens) * (2 ** min(attempt, 2)),
                temperature,
                model=model,
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "schema": schema.model_json_schema(),
                    },
                },
            )
            # Clean markdown fences if present
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text).strip()
            if not text.startswith("{"):
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    text = m.group(0)
            parsed = json.loads(text)
            if isinstance(parsed, dict) and {"properties", "type"}.issubset(parsed.keys()):
                raise ValueError("Model returned the JSON schema instead of an answer object")
            return schema.model_validate(parsed)
        except Exception as e:
            status = getattr(e, "status_code", None)
            # Invalid structured JSON is not fixed by long sleeps — only retry a few times
            retryable = _retryable(e)
            if not retryable and status != 429:
                raise
            last_err = e
            if attempt >= attempts - 1:
                break
            base = 10.0 if status == 429 else 1.5
            time.sleep(base * (attempt + 1))
    raise ValueError(f"Structured output failed: {last_err}")
