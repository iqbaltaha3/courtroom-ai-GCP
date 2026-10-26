"""Search Tavily, trim snippets, and remove repeated URLs."""

import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from tavily import TavilyClient

from app.config import MAX_RESULT_CONTENT_CHARS, TAVILY_API_KEY, TAVILY_RESULTS_PER_QUERY

logger = logging.getLogger(__name__)


def normalize_whitespace(text):
    """Remove null characters and repeated whitespace without interpreting content."""
    return " ".join((text or "").replace("\x00", " ").split())


def tavily_search(item):
    """Fetch a query's snippets; assign stable IDs based on query order."""
    index, query = item
    try:
        response = TavilyClient(api_key=TAVILY_API_KEY).search(
            query=query,
            search_depth="basic",
            max_results=TAVILY_RESULTS_PER_QUERY,
            include_raw_content=False,
            include_images=False,
        )
    except Exception:
        logger.exception("Precedent search failed")
        return []
    candidates = []
    for offset, result in enumerate(response.get("results", [])):
        url = (result.get("url") or "").strip()
        candidates.append(
            {
                "candidate_id": index * TAVILY_RESULTS_PER_QUERY + offset + 1,
                "search_query": query,
                "title": normalize_whitespace(result.get("title")),
                "url": url,
                "domain": (urlparse(url).hostname or "").lower(),
                "tavily_score": result.get("score"),
                "content": normalize_whitespace(result.get("content") or result.get("snippet"))[
                    :MAX_RESULT_CONTENT_CHARS
                ],
            }
        )
    return candidates


def search_tavily_parallel(queries):
    """Run independent searches concurrently while preserving input order."""
    if not queries:
        return []
    with ThreadPoolExecutor(max_workers=min(5, len(queries))) as pool:
        return [candidate for batch in pool.map(tavily_search, enumerate(queries)) for candidate in batch]


def deduplicate_results(results):
    """Keep the first result for each URL; semantic redundancy is handled by ranking."""
    unique = {}
    for result in results:
        url = result["url"].rstrip("/").lower()
        if url:
            unique.setdefault(url, result)
    return list(unique.values())
