"""Combine BM25 keyword search and Chroma vector results by rank."""

import csv
import re
import sys
from pathlib import Path

import chromadb
from chromadb.utils import embedding_functions
from rank_bm25 import BM25Okapi

from app.config import DATA_DIR

csv.field_size_limit(sys.maxsize)


# The same three CSVs feed keyword search and the persisted vector index.
DB_PATH = str(DATA_DIR / "research_index")
COLLECTION_NAME = "indian_criminal_law"
CSV_FILES = [DATA_DIR / f"{act}_sections.csv" for act in ("BNS", "BNSS", "BSA")]

RRF_K = 60
CANDIDATES_PER_METHOD = 20  # how many results each method contributes before fusion
TOKEN_RE = re.compile(r"[a-zA-Z]+")

# Common Indian criminal-law shorthand that never appears verbatim in the
# statute text itself (the Acts use formal phrasing instead), so neither
# BM25 nor vector search can bridge the gap on their own. Expanding the
# query with the statute's actual phrasing before search closes that gap.
# Extend this list as you find more real query failures.
LEGAL_ALIASES = {
    "fir": "information cognizable offence police station",
    "chargesheet": "police report completion investigation forwarded magistrate",
    "charge sheet": "police report completion investigation forwarded magistrate",
    "bail": "release on bail bond surety",
    "anticipatory bail": "direction for grant of bail to person apprehending arrest",
    "remand": "custody magistrate detention",
    "cognizance": "magistrate taking cognizance offence",
    "pil": "public interest",
    "plea bargaining": "application for plea bargaining accused",
    "cross examination": "cross-examination witness",
    "hearsay": "statements persons who cannot be called as witnesses",
    "search warrant": "search-warrant issued by court",
    "acquittal": "finding of acquittal accused",
    "cognizable offence": "cognizable case police station",
}


def expand_query(query: str) -> str:
    """Append statutory phrasing for any known colloquial/legal shorthand
    found in the query, so search isn't limited to exact statute wording."""
    lower_query = query.lower()
    additions = []
    for alias, expansion in LEGAL_ALIASES.items():
        if re.search(r"\b" + re.escape(alias) + r"\b", lower_query):
            additions.append(expansion)
    if additions:
        return query + " " + " ".join(additions)
    return query


def tokenize(text: str):
    """Split statute text into lowercase words for BM25 matching."""
    return TOKEN_RE.findall(text.lower())


def load_corpus():
    """Load the same rows used to build the Chroma DB, so BM25 stays in sync."""
    ids, docs = [], []
    for fname in CSV_FILES:
        path = Path(fname)
        if not path.exists():
            print(f"WARNING: {fname} not found, skipping.")
            continue
        with path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ids.append(f"{row['act']}_{row['section_number']}")
                docs.append(row["text"])
    return ids, docs


def reciprocal_rank_fusion(ranked_lists, k=RRF_K):
    """
    ranked_lists: list of lists of doc_ids, each already ordered best-first.
    Returns dict {doc_id: fused_score}.
    """
    scores = {}
    for ranked_list in ranked_lists:
        for rank, doc_id in enumerate(ranked_list, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return scores


def hybrid_search(query, collection, bm25, corpus_ids, n_results=5, where=None):
    """Merge semantic and keyword rankings, then return the highest ranked sections."""
    expanded_query = expand_query(query)

    # --- Vector search ---
    vector_results = collection.query(
        query_texts=[expanded_query], n_results=CANDIDATES_PER_METHOD, where=where
    )
    vector_ranked_ids = vector_results["ids"][0]

    # --- BM25 keyword search ---
    tokenized_query = tokenize(expanded_query)
    bm25_scores = bm25.get_scores(tokenized_query)
    bm25_ranked = sorted(zip(corpus_ids, bm25_scores), key=lambda x: x[1], reverse=True)
    bm25_ranked_ids = [doc_id for doc_id, score in bm25_ranked if score > 0][:CANDIDATES_PER_METHOD]

    # Apply the same metadata filter to BM25 results if one was given, so a
    # BM25 hit outside the filtered act doesn't leak into fused results.
    if where and "act" in where:
        allowed_prefix = where["act"] + "_"
        bm25_ranked_ids = [i for i in bm25_ranked_ids if i.startswith(allowed_prefix)]

    # --- Fuse ---
    fused_scores = reciprocal_rank_fusion([vector_ranked_ids, bm25_ranked_ids])
    fused_sorted = sorted(fused_scores.items(), key=lambda x: x[1], reverse=True)

    top_ids = [doc_id for doc_id, _ in fused_sorted[:n_results]]
    if not top_ids:
        return []

    fetched = collection.get(ids=top_ids)
    id_to_record = {
        doc_id: (doc, meta)
        for doc_id, doc, meta in zip(fetched["ids"], fetched["documents"], fetched["metadatas"])
    }

    results = []
    for doc_id, score in fused_sorted[:n_results]:
        doc, meta = id_to_record[doc_id]
        results.append(
            {
                "id": doc_id,
                "score": score,
                "in_vector_top": doc_id in vector_ranked_ids,
                "in_bm25_top": doc_id in bm25_ranked_ids,
                "metadata": meta,
                "snippet": doc[:120].replace("\n", " "),
            }
        )
    return results


def load_index():
    """Load the existing vector collection and build BM25 from the matching CSV rows."""
    corpus_ids, corpus_docs = load_corpus()
    if not corpus_docs:
        raise ValueError("No statute rows found; add the core law CSV files")
    bm25 = BM25Okapi([tokenize(doc) for doc in corpus_docs])
    client = chromadb.PersistentClient(path=DB_PATH)
    collection = client.get_collection(
        COLLECTION_NAME, embedding_function=embedding_functions.DefaultEmbeddingFunction()
    )
    return collection, bm25, corpus_ids
