"""Check dataset integrity without models, network calls, or database writes."""

import csv
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "datasets"


def digest(value):
    return hashlib.sha256(value).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate():
    retrieval = json.loads((DATASETS / "retrieval.json").read_text(encoding="utf-8"))
    cases = json.loads((DATASETS / "cases.json").read_text(encoding="utf-8"))
    corpus = {}
    for entry in retrieval["corpus_manifest"]:
        path = (DATASETS / entry["path"]).resolve()
        require(digest(path.read_bytes()) == entry["sha256"], f"Changed corpus: {path.name}")
        with path.open(newline="", encoding="utf-8") as source:
            for row in csv.DictReader(source):
                corpus[f"{row['act']}_{row['section_number']}"] = row

    for records in (retrieval["examples"], cases["cases"]):
        require(len({r["id"] for r in records}) == len(records), "Duplicate example IDs")
        splits = {}
        for record in records:
            family, split = record["case_family"], record["split"]
            require(split in ("development", "held_out"), f"Unknown split: {record['id']}")
            require(splits.setdefault(family, split) == split, f"Family crosses splits: {family}")

    for item in retrieval["examples"]:
        ids = item["relevant_section_ids"]
        require(ids and len(ids) == len(set(ids)), f"Empty or repeated labels: {item['id']}")
        require({s["section_id"] for s in item["support"]} == set(ids), "Missing statute support")
        for support in item["support"]:
            row = corpus[support["section_id"]]
            require(row["text"].startswith(support["heading"]), "Statute heading mismatch")
            require(support["excerpt"] in row["text"], "Statute excerpt mismatch")
            require(digest(row["text"].encode()) == support["section_text_sha256"], "Section changed")
            require(row["source_url"] == support["source_url"], "Source URL mismatch")
            require(row["page_number"] == support["source_page"], "Source page mismatch")

    # Read the persisted Chroma storage directly: do not load/download embedding models.
    index = ROOT.parent / "data" / "research_index" / "chroma.sqlite3"
    if index.is_file():
        with sqlite3.connect(index.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            indexed = {row[0] for row in conn.execute("SELECT embedding_id FROM embeddings")}
        for item in retrieval["examples"]:
            require(set(item["relevant_section_ids"]) <= indexed, f"Target missing from index: {item['id']}")

    files, facts = 0, 0
    for case in cases["cases"]:
        pages = {f"{p['document']}:{p['page']}": p["text"] for p in case["source_pages"]}
        require(len(pages) == len(case["source_pages"]), f"Duplicate source page: {case['id']}")
        require(len({f["id"] for f in case["expected_facts"]}) == len(case["expected_facts"]), "Duplicate fact ID")
        require(len(case["source_files"]) == len(pages), "Source inventory mismatch")
        for source in case["source_files"]:
            path = DATASETS / source["path"]
            require(digest(path.read_bytes()) == source["sha256"], f"Changed source: {path}")
            ref = f"{source['document']}:{source['page']}"
            require(path.read_text(encoding="utf-8") == pages[ref] + "\n", "Inline/file text mismatch")
            files += 1
        for fact in case["expected_facts"]:
            require(fact["source_refs"] and set(fact["source_refs"]) <= pages.keys(), "Unknown fact source")
            require(fact["support"], "Fact has no source support")
            require({s["source_ref"] for s in fact["support"]} == set(fact["source_refs"]), "Incomplete support")
            for support in fact["support"]:
                require(support["quote"] in pages[support["source_ref"]], "Fact quote mismatch")
            facts += 1
        all_text = "\n".join(pages.values())
        for person in case["expected_people"]:
            require(person["name"] in all_text, f"Expected person absent: {person['name']}")
        for law in case["expected_act_sections"]:
            require(law["act"] in all_text and law["section"] in all_text, "Expected law absent")
        require(set(case["additional_agent_checks"]) <= cases["default_agent_checks"].keys(), "Unknown agent")
        require(case["simulation_input"]["narrative"] == "\n\n".join(
            f"SOURCE {p['document']} PAGE {p['page']} ({p['doc_type']})\n{p['text']}"
            for p in case["source_pages"]
        ), "Simulation input differs from sources")

    print(f"PASS: {len(retrieval['examples'])} retrieval items, {len(cases['cases'])} cases, {facts} facts, {files} source files.")
    print("Checked corpus hashes, statute labels, available index IDs, source quotes/files, splits, and agent keys.")
    print("Integrity checks do not certify legal correctness or model performance.")


if __name__ == "__main__":
    validate()
