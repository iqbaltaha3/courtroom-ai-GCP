from pydantic import BaseModel

from app.ai import llm


class SimpleStructuredOut(BaseModel):
    """Test data or fake provider object for simple structured out."""

    answer: str


def test_call_structured_retries_schema_echo(monkeypatch):
    """Verify call structured retries schema echo."""
    responses = iter(
        [
            '{"type":"object","properties":{"answer":{"type":"string"}}}',
            '{"answer":"ok"}',
        ]
    )

    class FakeMessage:
        """Test data or fake provider object for fake message."""

        def __init__(self, content):
            """Store the fake response values used by this test."""
            self.content = content

    class FakeChoice:
        """Test data or fake provider object for fake choice."""

        def __init__(self, content):
            """Store the fake response values used by this test."""
            self.message = FakeMessage(content)

    class FakeResponse:
        """Test data or fake provider object for fake response."""

        def __init__(self, content):
            """Store the fake response values used by this test."""
            self.choices = [FakeChoice(content)]

    class FakeCompletions:
        """Test data or fake provider object for fake completions."""

        def create(self, **kwargs):
            """Return the next fake completion response and capture its request."""
            assert kwargs["response_format"]["type"] == "json_schema"
            assert "answer" in kwargs["response_format"]["json_schema"]["schema"]["properties"]
            return FakeResponse(next(responses))

    class FakeChat:
        """Test data or fake provider object for fake chat."""

        completions = FakeCompletions()

    class FakeClient:
        """Test data or fake provider object for fake client."""

        chat = FakeChat()

    monkeypatch.setattr(llm, "get_client", lambda: FakeClient())

    result = llm.call_structured("system", "user", SimpleStructuredOut)

    assert result.answer == "ok"
