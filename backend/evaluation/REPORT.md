# Courtroom AI baseline evaluation

Completed on 23 October 2026. [Read the detailed report and evidence](results/20261023T160346Z/report.md).

## What ran

- 15 source-labeled statute questions, each evaluated with BM25, vector, and hybrid retrieval: 45 measurements.
- Five synthetic case records through all eleven actual simulation stages: 55 completed stage executions.
- 91 deterministic structural checks, all passing.
- Assistant review of stage outputs against the included source records, with 55 per-agent assessments and 59 applicable fact checks. These are actual-upstream workflow audits, not isolated golden-input agent experiments.

## Retrieval results

| Method | Recall@8 | Top-1 target hits | Mean warm search time |
| --- | --- | --- | --- |
| BM25 | 15/15 (100%) | 13/15 | 2.44 ms |
| Vector | 14/15 (93.3%) | 9/15 | 106.69 ms |
| Hybrid with RRF | 15/15 (100%) | 12/15 | 107.14 ms |

Each question labels one primary provision. These are small targeted lookup results, not exhaustive legal-research recall. Timings are single warm measurements, not a performance benchmark. This set does not establish that hybrid is better than BM25.

## Factual fidelity and coverage

| Stage | Applicable fact checks passed |
| --- | --- |
| Extraction | 19/19 |
| Case Manager | 19/20 |
| Judicial assessment | 15/20 |

These are assistant-reviewed results on this dataset, not legal-accuracy percentages. A fact does not pass if correct wording is later contradicted or strengthened beyond the source. Two judicial checks fail because details were omitted, rather than invented. All five judicial outputs remained preliminary assessments.

## Main findings

1. The simulation missed available BNS 303 text when lookup received the full extracted Act name. The same lookup with `BNS` succeeded.
2. Research planning produced incorrect BNS section numbers in the weapon-threat case; a consultant also used an incorrect BNSS section number in the Hindi case. These mismatches were checked against the local corpus.
3. Missing supplied recovery documents became assertions that a weapon or phone had not been recovered. Some witness non-observation was strengthened into denial of a weapon's presence.
4. Specific unsupported procedural references entered consultant strategies and propagated into later arguments.
5. All 91 structural checks passed despite these source-support problems. Existing citation IDs and internal reviewer approval did not guarantee faithful claims.

The detailed report keeps clear source mismatches separate from legal conclusions requiring domain review. No application prompts, models, or retrieval settings were changed during this baseline.

## Runtime and usage

- Sum of case execution times: **32.6 minutes**.
- **97 model requests**, including five failed attempts; all five workflows completed.
- **729,583 provider-reported total tokens**; five requests lacked total-token reporting. This is a partial reported total, not a complete cost estimate.
- Existing mocked-provider backend suite: **73 tests passed**.

## Evidence and limits

The [run directory](results/20261023T160346Z/) contains rankings, dataset snapshots, outputs, isolated case databases, factual-review notes, per-agent assessments, and token usage.

Cases are synthetic text records; no PDF transcription, long-record evaluation, independent legal-expert review, standalone Tavily memo evaluation, deployment test, or before/after improvement experiment was performed. No final verdict was treated as ground truth.
