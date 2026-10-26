# Golden dataset templates

For completed baseline results, open [REPORT.md](REPORT.md). The detailed run includes actual provider outputs and separate deterministic and assistant-reviewed assessments.

Populated starter datasets are now available as [datasets/retrieval.json](datasets/retrieval.json) and [datasets/cases.json](datasets/cases.json). Read [DATASET_CARD.md](DATASET_CARD.md) for source verification, coverage, splits, and limitations. Validate their integrity with `python3 backend/evaluation/validate_datasets.py` from the repository root. The original templates below remain available for adding examples.

The dataset files do not make AI calls. `run.py` now runs retrieval comparisons and actual simulation stages, and saves outputs in a separate timestamped results directory. `check_outputs.py` checks saved structured outputs without additional AI calls.

## Run evaluation

From the repository root, using a Python environment with the backend dependencies installed:

```sh
python backend/evaluation/run.py --mode retrieval
python backend/evaluation/run.py --mode all
python backend/evaluation/check_outputs.py backend/evaluation/results/<run_id>
```

`--mode all` and `--mode cases` use live configured providers and incur their normal charges. Credentials and network access are required. Runs do not modify user cases or the Case Library. Retrieval timings exclude embedding warmup and index initialization. Each case uses the exact source-only typed narrative, not its expected-answer labels. Individual stage results use actual upstream outputs; this is not independent evaluation with golden upstream fixtures.

`report.md` records measured retrieval results and case execution status. Rankings, outputs, isolated case databases, dataset snapshots, and usage details are saved alongside it. Semantic requirements remain pending until reviewed; a successful stage or an internal review approval is not automatically a factual-quality pass. `agent_review.csv` is the review worksheet, and structural results are kept separately in `automatic_checks.csv`.

After explicitly reviewing outputs and recording annotations in `semantic_review.json`, `summarize.py <results-directory>` generates the combined report, fact-review CSV, and per-agent assessment CSV. It does not invent semantic scores or call another model. The first baseline's annotations identify the assistant as reviewer and distinguish verified source mismatches from questions requiring legal-domain review.

## 1. Retrieval template

Open [datasets/retrieval.template.json](datasets/retrieval.template.json).

Each entry needs:

| Field | What you provide |
| --- | --- |
| `id` | A unique identifier for the question |
| `split` | `development` for tuning, or `held_out` for final testing |
| `question` | The exact question you want the retriever to search |
| `relevant_section_ids` | The statute sections you manually checked as relevant |
| `reference_notes` | Why those sections are relevant, based on their text |
| `tags` | Optional categories for understanding failures |

IDs follow the existing index format: `BNS_303`, `BNSS_173`, `BSA_63`. The three example IDs and descriptions were checked against the local statute CSVs. They illustrate matching your repository's corpus, not a complete or independently verified legal analysis.

Read the full sections before labeling real examples. List all relevant sections you can establish for the question, not just the section that the current retriever returns. More than one ID is allowed. Questions that need statutes outside this index should be tracked separately as coverage limitations.

The planned metric is Recall@8: the number of labeled relevant IDs returned in the top eight, divided by the number of labeled relevant IDs. An empty label list has no defined recall denominator; do not use one in this initial retrieval set.

Copy a whole entry, change its ID, and replace the question and reference labels. Use natural questions as well as straightforward keyword questions. Keep query paraphrases together in the same split.

## 2. Case and individual-agent template

Open [datasets/cases.template.json](datasets/cases.template.json).

All three cases are synthetic. They demonstrate invented times, similar names, and conflicting accounts. Their `.txt` names are logical source labels; those example files do not exist separately.

| Field | What you provide |
| --- | --- |
| `id` | A unique case ID |
| `case_family` | A grouping ID shared by all variants of one source case |
| `split` | `development` or `held_out` |
| `source_documents` | Actual PDFs, if evaluating ingestion or a full PDF workflow |
| `source_pages` | Reviewed source text for agent evaluation, with document names and page numbers |
| `expected_people` | Verified names and roles; phrase roles according to what the source actually supports |
| `expected_act_sections` | Act/section pairs explicitly mentioned in the source |
| `expected_facts` | Individual facts, each with supporting source references |
| `must_not_claim` | Specific unsupported conclusions the output must avoid |
| `expected_decision_type` | Expected assessment scope, based on the supplied record |
| `additional_agent_checks` | Extra checks for particular agents in this case |

For real PDFs, an entry in `source_documents` could be:

```json
{
  "path": "documents/example_fir.pdf",
  "doc_type": "allegation"
}
```

Paths in this proposed format are relative to the dataset JSON file. Document types are `allegation`, `petition`, and `evidence`. The input category does not make the contents established proof.

For structured Act/section expectations, use:

```json
{"act": "Exact Act name from your document", "section": "Exact section label"}
```

The example cases have empty `expected_act_sections` because their source text names no statute. Do not guess sections to fill this field. Proposed applicable law is a different evaluation question from faithful extraction.

`source_refs` use `document:page`, for example `example_fir.pdf:2`. Match each name to `source_pages.document`. These are stable annotation references; a future runner will map them to the application's generated P IDs.

For text-only agent evaluation, supply the complete reviewed text needed for the checks. For PDF transcription or full-record evaluation, include the actual PDF and cover its material content in the annotations. A few excerpts do not establish complete-record quality. Reviewed text should be checked against the original PDF, rather than copied uncritically from the application's own transcription.

The top-level `default_agent_checks` lists shared requirements for all eleven simulation stages. `additional_agent_checks` adds case-specific requirements. The keys match the application's stage keys. Each prosecution/defense stage and argument round can be scored separately.

For isolated agent evaluation, the runner will also need reviewed upstream inputs: the Case Manager report, consultant strategies, research material, or preceding arguments required by that agent. Those fixtures will be prepared during implementation; raw pages alone do not isolate later agents. Research claims need reviewed source passages and links, not just a case narrative. The current templates describe the expectations without pretending to provide all those upstream fixtures.

## How to fill them in

1. Copy each template to `retrieval.json` and `cases.json` in the same directory.
2. Replace the example content with your reviewed examples. Use unique IDs.
3. Keep each expected fact short and independently checkable; add its source reference.
4. Write forbidden claims precisely. They are semantic review instructions, not literal strings to reject: “the assault did not occur at 10 pm” should not fail simply because it contains “10 pm.”
5. Leave genuinely uncertain conclusions uncertain. Do not force an exact final judgment as the golden answer.
6. Set `is_template` to `false` after reviewing your dataset. This is a label for the planned runner, not validation code already implemented.
7. Keep a few examples held out. All examples derived from the same source case must share the same split.

You can begin with two or three real examples. Ten retrieval questions and five case fixtures are a reasonable next target. It is fine to leave `tags` or case-specific checks empty, but factual expectations and their sources need review. Remove private identifying information from any dataset you intend to share publicly.

## Planned results

- Retrieval: Recall@8 and retrieval time for BM25, vector, and hybrid search.
- Agents: exact checks where possible, plus manual Pass / Fail / Needs review labels for meaning-based requirements.
- Fact coverage: correctly preserved expected facts divided by all expected facts in scope for the evaluated stage. A short strategy need not repeat every background fact; select applicable facts when constructing each agent test.
- Complete runs: final fact coverage, unsupported claims, assessment scope, completion status, runtime, model calls, and available tokens.

These templates do not report measured performance. Model-generated judgments or the application's own internal review approval must not be used as verified golden labels without independent checking.
