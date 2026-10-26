from types import SimpleNamespace as Obj

import pytest

from app.ai import grounding as legal_research


def install_response(monkeypatch, metadata):
    """Install a fake grounded-search response without contacting Google."""

    class Client:
        """Test data or fake provider object for client."""

        def __init__(self, **kwargs):
            """Store the fake response values used by this test."""
            self.models = self

        def __enter__(self):
            """Return the fake client when entering its context."""
            return self

        def __exit__(self, *args):
            """Finish the fake client context without external resources."""
            pass

        def generate_content(self, **kwargs):
            """Return the configured grounded-search response."""
            assert "site:sci.gov.in" in kwargs["contents"]
            return Obj(candidates=[Obj(finish_reason="STOP", grounding_metadata=metadata)])

    monkeypatch.setattr(legal_research.genai, "Client", Client)


def test_research_keeps_source_support_mapping_and_filters_commentary(monkeypatch):
    """Verify research keeps source support mapping and filters commentary."""
    metadata = Obj(
        grounding_chunks=[
            Obj(web=Obj(domain="law-blog.example", uri="https://law-blog.example/post", title="Commentary")),
            Obj(
                web=Obj(domain="api.sci.gov.in", uri="https://api.sci.gov.in/judgment.pdf", title="Judgment")
            ),
        ],
        grounding_supports=[
            Obj(grounding_chunk_indices=[0], segment=Obj(text="Unsupported commentary")),
            Obj(grounding_chunk_indices=[0, 1], segment=Obj(text="A source-supported proposition")),
        ],
        web_search_queries=["A generic legal query"],
        search_entry_point=None,
    )
    install_response(monkeypatch, metadata)
    result = legal_research.grounded_research(["Generic legal issue"], "prosecution")
    assert [source["id"] for source in result["sources"]] == ["RP2"]
    assert result["passages"] == [{"text": "A source-supported proposition", "source_ids": ["RP2"]}]


def test_no_grounding_cannot_be_published_as_research(monkeypatch):
    """Verify no grounding cannot be published as research."""
    install_response(monkeypatch, None)
    with pytest.raises(ValueError, match="no source-supported passages"):
        legal_research.grounded_research(["Generic legal issue"], "defense")
