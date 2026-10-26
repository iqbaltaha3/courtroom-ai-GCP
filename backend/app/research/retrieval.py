"""Retrieve statute sections and prepare their source text for research prompts."""

from app.config import MAX_STATUTE_TEXT_CHARS, N_STATUTE_RESULTS
from app.research.search import hybrid_search


def retrieve_statutes(offence_text, allegation_text, collection, bm25, corpus_ids):
    """Search the combined allegations and offences using semantic and keyword rankings."""
    query = f"OFFENCES:\n{offence_text}\nALLEGATIONS:\n{allegation_text}"
    return hybrid_search(query, collection, bm25, corpus_ids, n_results=N_STATUTE_RESULTS)


def build_statute_context(results, collection):
    """Fetch original section text rather than relying on short search snippets."""
    if not results:
        return "No matching statutes were retrieved."
    fetched = collection.get(ids=[result["id"] for result in results])
    texts = dict(zip(fetched["ids"], fetched["documents"]))
    blocks = []
    for result in results:
        meta = result["metadata"]
        text = texts.get(result["id"], result.get("snippet", ""))[:MAX_STATUTE_TEXT_CHARS]
        blocks.append(
            f"ACT: {meta.get('act', '')}\nSECTION: {meta.get('section_number', '')}\n"
            f"CHAPTER: {meta.get('chapter_title', '')}\nSTATUTORY TEXT:\n{text}"
        )
    return "\n\n".join(blocks)
