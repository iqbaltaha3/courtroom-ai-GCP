# Courtroom AI starter evaluation datasets

The first live baseline evaluation is complete. See [REPORT.md](REPORT.md) for the short summary and [the detailed run report](results/20261023T160346Z/report.md) for measurements, stage audits, source evidence, and limitations.

## Contents

- `datasets/retrieval.json`: 15 targeted provision-lookup questions, five each for BNS, BNSS, and BSA. Ten development questions and five reserved questions.
- `datasets/cases.json`: five fictional case records, with 20 source-backed expected facts. Three development cases and two reserved cases.
- `datasets/documents/`: seven readable UTF-8 source files. These are the complete synthetic records, not excerpts of real cases.
- `validate_datasets.py`: local integrity checks; no model calls or writes to application databases.

The two `.template.json` files remain unchanged as editing examples.

## Accuracy and provenance

Retrieval labels were selected from the repository's local statute CSVs, rather than from a model's answer or retriever rankings. Every target has a heading, source URL/page from the CSV, supporting text, and a hash of its complete section text. Corpus-file hashes identify the version used to create the labels. These hashes are integrity checks, not authentication of the original law.

Questions intentionally ask for a primary provision. Recall@8 here measures recovery of that labeled provision; it does not measure discovery of every section relevant to a real legal dispute. No claim is made about amendments, commencement, precedent validity, or exhaustive current-law coverage. Fetches of the three original India Code PDF URLs timed out during preparation, so external verification of those PDFs was not completed. Source verification for the labels is against the frozen local corpus.

Case facts are grounded in the included fictional records. Each expected fact has literal supporting quotes and document/page references. Uncertain allegations remain allegations. The records deliberately state an investigation stage, and the assessment-scope label follows the application's existing investigation-only behavior. It is not a legal opinion about a real proceeding. There are no invented judicial decisions or labels declaring anyone guilty or innocent.

The assistant checked the annotations against these sources. They have not received independent human or legal-expert review. Call this an **assistant-prepared, source-grounded synthetic starter set**, not an expert-certified legal benchmark.

## Case coverage

| Case | Tests |
| --- | --- |
| `case_001` | Missing occurrence time versus recorded registration time |
| `case_002` | Similar names and distinct described actions |
| `case_003` | Different accounts, alleged weapon versus supplied recovery evidence |
| `case_004` | Hindi names/text, room number versus time, explicitly cited BNS provision |
| `case_005` | Legacy Act/section preservation, signing officer role, medical referral versus examination result |

Shared checklists cover each of the eleven simulation stages. Case-specific checks add focused requirements. Extraction checks identify what the record cites; they do not require later agents to conclude that the cited offences are made out.

## How these inputs will be used

`source_pages` are the expected source text for factual evaluation. Matching standalone files are included for inspection. `simulation_input.narrative` contains only source records and labels; expected facts and forbidden claims are kept outside the model input.

These sources can exercise the application's existing typed-narrative path when the evaluation runner is implemented. `source_documents` is empty because there are no PDFs in this set. Consequently, the set does not evaluate rendering, scanned text, OCR/transcription accuracy, document-category preservation through PDF ingestion, or long-record summarization.

For isolated downstream-agent evaluation, supply reviewed upstream strategies, reports, source passages, and earlier submissions appropriate to that agent. Those agent-input fixtures are not yet included. The completed baseline uses full typed-record runs and audits their actual upstream handoffs, rather than isolated downstream-agent fixtures.

## Splits and scoring

Keep reserved examples out of prompt tuning, including their source-case variants. Since the labels are visible in the checkout and were discussed during dataset creation, these are locally reserved examples, not a blind external test set. Once used to tune prompts, move them to development and create new reserved cases.

Use automatic exact checks only where appropriate. Forbidden claims are semantic assertions: a report saying “10 pm is not established as the occurrence time” must not fail a substring filter. Similarly, a correct transliteration should not fail merely because it differs from one spelling; original-script names are available for checking.

Apply fact coverage only to facts material to the stage. A consultant strategy does not need to repeat every background fact. Report unsupported-claim counts, blocked runs, and technical failures separately; do not hide them in a single average score.

Live research needs a separate source-support review. These datasets include no frozen precedent passages or expert-reviewed holdings. Do not grade an agent as wrong just because it acknowledges that live authority could not be verified.

## Validate integrity

From the repository root:

```sh
python3 backend/evaluation/validate_datasets.py
```

This checks JSON data, uniqueness, family splits, corpus fingerprints, target-section text, available persisted index IDs, source-file fingerprints, fact support quotes, expected names/provisions, and the source-only simulation input. It does not run retrieval or the simulation, certify legal accuracy, or prove that an LLM will satisfy the labels.
