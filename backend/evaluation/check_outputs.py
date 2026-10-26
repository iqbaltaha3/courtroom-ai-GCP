"""Score structural checks on saved outputs; do not infer semantic correctness."""

import argparse
import csv
import json
import re
import sqlite3
from pathlib import Path


def canonical_act(value):
    clean = re.sub(r"[^a-z]", "", value.lower())
    for code, words in (
        ("IPC", ("indianpenalcode", "ipc")),
        ("CRPC", ("codeofcriminalprocedure", "crpc")),
        ("BNSS", ("bharatiyanagariksuraksha", "bnss")),
        ("BNS", ("bharatiyanyaya", "bns")),
        ("BSA", ("bharatiyasakshya", "bsa")),
    ):
        if any(word in clean for word in words):
            return code
    return clean


def inspect_run(root):
    dataset = json.loads((root / "cases.dataset.json").read_text(encoding="utf-8"))
    checks, snapshots, usage, report_text = [], {}, [], []
    for case in dataset["cases"]:
        directory = root / "cases" / case["id"]
        state_path = directory / "outputs.json"
        if not state_path.exists():
            continue
        state = json.loads(state_path.read_text(encoding="utf-8"))
        path = directory / "case.db"
        if not path.exists():
            continue
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            snapshot = {}
            for table in ("pages", "laws", "witnesses", "evidence", "entities", "timeline", "reports", "artifacts"):
                snapshot[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
        snapshots[case["id"]] = snapshot
        stages = {s["key"]: s["status"] for s in state["stages"]}
        reports = {row["report_type"]: row for row in snapshot["reports"]}
        artifacts = {row["key"]: json.loads(row["value_json"]) for row in snapshot["artifacts"]}

        def add(stage, check, passed, evidence):
            checks.append({"case_id": case["id"], "stage": stage, "check": check,
                           "assessment": "pass" if passed else "fail", "evidence": evidence,
                           "assessment_type": "deterministic_structural"})

        if stages.get("clarify_extract") == "completed":
            entity_text = json.dumps({key: snapshot[key] for key in ("entities", "witnesses")}, ensure_ascii=False)
            for person in case["expected_people"]:
                add("clarify_extract", f"Expected original name present: {person['name']}",
                    person["name"] in entity_text, "Name presence only; role attribution requires semantic review.")
            expected = {(canonical_act(law["act"]), law["section"]) for law in case["expected_act_sections"]}
            actual = {(canonical_act(law["act"] or ""), law["section"]) for law in snapshot["laws"]}
            add("clarify_extract", "Expected Act/section pairs preserved", expected <= actual,
                f"expected={sorted(expected)}; extracted={sorted(actual)}")
            if not expected:
                add("clarify_extract", "No sections invented during extraction", not actual, f"extracted={sorted(actual)}")

        valid_pages = {f"P{p['id']}" for p in snapshot["pages"]}
        valid_authorities = {source["id"] for side in ("prosecution", "defense", "statutes")
                             for source in artifacts.get(f"{side}_sources", {}).get("sources", [])}

        def invalid_refs(value):
            errors = []
            if isinstance(value, dict):
                for key, entry in value.items():
                    if key in ("record_refs", "authority_refs"):
                        allowed = valid_pages if key == "record_refs" else valid_authorities
                        errors.extend(f"{key}: {ref}" for ref in entry if ref not in allowed)
                    errors.extend(invalid_refs(entry))
            elif isinstance(value, list):
                for entry in value:
                    errors.extend(invalid_refs(entry))
            return errors

        opponents = {"def_r1": "pros_r1", "pros_r2": "def_r1", "def_r2": "pros_r2"}
        for key, row in reports.items():
            report_text.extend([f"# {case['id']} / {key}", "", row["content"], ""])
            if key not in ("pros_r1", "def_r1", "pros_r2", "def_r2", "judge"):
                continue
            data = json.loads(row["structured_json"] or "{}")
            errors = invalid_refs(data)
            add(key, "Only existing page and authority IDs cited", not errors, "; ".join(errors) or "All cited IDs exist; this does not verify claim support.")
            if key == "judge":
                add(key, "Expected decision type", data.get("decision_type") == case["expected_decision_type"],
                    f"expected={case['expected_decision_type']}; actual={data.get('decision_type')}")
            else:
                submissions = data.get("submissions", [])
                add(key, "Every submission has a factual page reference", bool(submissions) and all(s.get("record_refs") for s in submissions), f"{len(submissions)} submissions")
                opponent = opponents.get(key)
                required = {f"{opponent}.{i}" for i in range(1, len(artifacts.get(opponent, {}).get("submissions", [])) + 1)} if opponent else set()
                answered = {ref for s in submissions for ref in s.get("responds_to", [])}
                add(key, "Declared opposing-point coverage", answered == required,
                    f"missing={sorted(required - answered)}; unknown={sorted(answered - required)}; meaningful engagement requires semantic review.")
        for metric in state.get("metrics", []):
            raw = metric.get("usage") or {}
            usage.append({"case_id": case["id"], "stage": metric["stage"], "model": metric["model"],
                          "status": metric["status"], "seconds": metric.get("seconds"),
                          "input_tokens": raw.get("prompt_tokens", raw.get("prompt_token_count")),
                          "output_tokens": raw.get("completion_tokens", raw.get("candidates_token_count")),
                          "total_tokens": raw.get("total_tokens", raw.get("total_token_count")),
                          "reasoning_tokens": (raw.get("completion_tokens_details") or {}).get("reasoning_tokens", raw.get("thoughts_token_count"))})
    for name, value in (("automatic_checks", checks), ("database_snapshots", snapshots)):
        (root / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for name, rows, fields in (
        ("automatic_checks", checks, ["case_id", "stage", "check", "assessment", "evidence", "assessment_type"]),
        ("usage", usage, ["case_id", "stage", "model", "status", "seconds", "input_tokens", "output_tokens", "total_tokens", "reasoning_tokens"]),
    ):
        with (root / f"{name}.csv").open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    (root / "stage_reports.md").write_text("\n".join(report_text), encoding="utf-8")
    print(f"Saved {len(checks)} structural checks and {len(usage)} request records.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("results", type=Path)
    inspect_run(parser.parse_args().results)
