# Courtroom AI · Google Cloud

Courtroom AI is an Indian legal research and courtroom simulation project. A React frontend calls a FastAPI backend. The backend processes case documents, prepares arguments for both sides, reviews a judicial assessment, searches statutes, and answers questions about completed cases.

[Repository](https://github.com/iqbaltaha3/courtroom-ai-GCP) · [Issues](https://github.com/iqbaltaha3/courtroom-ai-GCP/issues) · [Evaluation guide](backend/evaluation/README.md) · [Architecture and interview guide](docs/portfolio-interview-guide.md)

## Features

- Upload PDFs or enter case facts, with page-level source references.
- Follow a reviewed simulation through extraction, case management, opposing strategies, arguments, and judicial assessment.
- Research Indian legal questions with hybrid statute retrieval and attributed web sources.
- Browse BNS, BNSS, BSA, and imported laws; save cases and ask follow-up questions.
- Download a complete simulation PDF with Hindi and English support.
- Inspect per-stage progress, model usage, and factual review findings.

**Stack:** React 18, TypeScript, Vite, FastAPI, Python, SQLite, Chroma, Google Vertex AI / Gemini, Tavily, and Docker. Container configuration is included for Google Cloud Run.

## Project links

| Resource | Link |
| --- | --- |
| Source code | [iqbaltaha3/courtroom-ai-GCP](https://github.com/iqbaltaha3/courtroom-ai-GCP) |
| Frontend deployment | [Courtroom AI on Cloud Run](https://courtroom-ai-frontend-558535784594.asia-south1.run.app) |
| Backend API documentation | [Swagger UI](https://courtroom-ai-backend-558535784594.asia-south1.run.app/docs) |
| Backend health | [Health endpoint](https://courtroom-ai-backend-558535784594.asia-south1.run.app/health) |
| Simulation evaluation | [Methodology](docs/simulation-evaluation.md) · [Dataset card](backend/evaluation/DATASET_CARD.md) · [Report](backend/evaluation/REPORT.md) |
| Configuration | [Backend example](backend/.env.example) · [Frontend example](frontend/.env.example) |

Deployment URLs come from the project's Cloud Run configuration. Availability depends on the deployed services and their access settings.

## Backend structure

All application code lives in `backend/app`. Folders describe what the code does.

```text
backend/
├── app/
│   ├── main.py                 # FastAPI setup, startup, and route registration
│   ├── config.py               # Paths, environment variables, and model limits
│   ├── schemas.py              # HTTP request and response models
│   ├── routes/                 # Cases, research, statutes, library, chat, health, judges
│   ├── services/               # Background runs, job state, cancellation, worker limits
│   ├── database/               # SQLite case, library, and statute queries
│   ├── ai/                     # Shared text client and attributed Google Search
│   ├── documents/              # PDF ingestion, page transcription, record context
│   ├── simulation/             # Extraction, case management, strategies, arguments, judgment
│   └── research/               # Statute retrieval, planning, web search, ranking, final memo
├── scripts/                    # Terminal research, index building, and simulation evaluation
├── tests/                      # API, document, workflow, and validation tests
├── data/                       # Statute CSVs, imported laws, and local database indexes
├── judges/                     # Judge profile text files
├── storage/cases/              # Uploaded files and per-case databases
├── .env.example                # Configuration template without credentials
├── requirements.txt            # Runtime dependencies
├── requirements-dev.txt        # Test and formatting dependencies
├── pyproject.toml              # Test and lint settings
└── Dockerfile
```

The frontend remains in `frontend/`. Its API routes and response fields are preserved.

## Run locally

Use Python 3.12 or newer. From the project root:

```sh
git clone https://github.com/iqbaltaha3/courtroom-ai-GCP.git
cd courtroom-ai-GCP
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp .env.example .env
```

Edit `.env` with your Google Cloud project and Tavily key. The project needs Vertex AI access. Authenticate local development through Application Default Credentials:

```sh
gcloud auth application-default login
uvicorn app.main:app --reload --port 8000
```

Open `http://127.0.0.1:8000/docs` for the API documentation. `/health` checks local configuration and statute availability; it does not verify that cloud credentials can make a model call.

Start the frontend in another terminal:

```sh
cd frontend
npm install
npm run dev
```

The development frontend proxies backend requests to port 8000. `VITE_API_URL` selects an explicit backend URL when needed.

The repository includes the shared `backend/data/statutes.sqlite3` index and statute source files. Rebuild the semantic research index before using hybrid research on a fresh clone:

```sh
cd backend
python -m scripts.build_research_index
```

The first build may download the embedding model. Local case databases, uploaded documents, credentials, generated evaluation runs, and exported documents are excluded from Git. Production frontend builds can set `VITE_API_URL` using `frontend/.env.example` as a template.

## Follow a simulation through the code

1. `routes/cases.py` creates a case ID and accepts typed facts or PDF uploads. Large files use 8 MiB chunk requests; direct uploads stream to a temporary file before replacing the destination.
2. `services/runner.py` selects the case database, tracks progress, and runs synchronous work in worker threads so HTTP requests remain responsive.
3. `documents/ingestion.py` receives the case database path explicitly, validates all PDFs, then renders and transcribes their pages. Images exist only in memory. Transcribed text and source references become rows in the case database. Typed facts use the same table without a model call.
4. `simulation/extraction.py` extracts laws, witnesses, evidence, entities, and timeline events into SQLite tables. Unresolved page failures stop downstream analysis.
5. `simulation/case_manager.py` builds a neutral report and checks it against the source record. Rejected reports receive up to two revision attempts with the original record, rejected draft, and reviewer findings. If factual problems remain, analysis stops and the error shows the findings.
6. `simulation/consultants.py` prepares separate strategies. `simulation/advocacy.py` researches each side, then runs prosecution and defense submissions across two rounds.
7. `simulation/judgment.py` evaluates both sides and checks its proposed assessment before publishing it. An investigation-only record supports a preliminary assessment.
8. Reports are saved as stages finish. REST polling and the case WebSocket expose progress. `database/library.py` stores searchable case metadata.

`simulation/pipeline.py` contains the ordered stage list. Independent consultant and research pairs can run concurrently with `STAGE_SERIAL=0`; sequential execution is the default to reduce simultaneous provider requests.

Each run has a `RunContext` containing its database path, judge selection, cancellation signal, and usage metrics. Python context variables carry this state into worker threads. Cancellation prevents subsequent stages and report writes; an already running provider request must finish before its worker can settle.

Large records use bounded, page-cited digests in `documents/context.py`. Every source page is processed, digests receive factual reviews, and large case-manager and judicial reports are also checked against original page batches. Full transcriptions remain in the case database’s `pages` table.

The selected judge is a displayed presiding identity. The assessment prompt stays neutral and does not inject a profile-based conviction bias.

## Understand ingestion as a data pipeline

Think of a PDF as the input dataset, one page as one observation, and the `pages` table as the output dataset. SQLite is simply a local file that stores tables and lets you query them with SQL.

```text
Original PDFs + optional typed facts
                 ↓
validate_pdfs: check that every PDF can be read and count its pages
                 ↓
transcribe_page: render a page temporarily and ask Gemini for its text
                 ↓
ingest_documents: save each text row into this case's case.db
                 ↓
Structured extraction and legal analysis begin
```

The three functions are in `app/documents/ingestion.py`. The destination `db_path` is a required input to `ingest_documents`; ingestion does not guess where to save results.

| Input/output | What it means |
| --- | --- |
| Input PDFs | Original source files, retained in `uploads/` for checking model errors |
| Temporary image | An in-memory representation of one PDF page sent to Gemini; never saved |
| `pages` row | Document category, filename, page number, transcription, and source fingerprint |
| Source fingerprint | SHA-256 of the original PDF bytes, recorded with each page to identify its source |
| `P1`, `P2`, etc. | Database row IDs used to trace extracted claims back to a particular document/page |
| Return value | Success flag, saved-page count, retry count, and validation errors |

For a two-page PDF, the output is two rows. A typed narrative adds one row named `typed_narrative.txt`; it does not require Gemini transcription. Query a case directly with SQL, for example:

```sql
SELECT doc_name, page_number, text
FROM pages
ORDER BY doc_name, page_number;
```

The pipeline validates all PDFs before making transcription requests or clearing previous case records. A failed transcription raises an error and stops analysis. Completed pages may remain saved for inspection. A small worker pool limits simultaneous requests; one coordinator saves the results in source order and updates progress. The page count includes typed facts when present.

## Understand the different databases

There are two kinds of data: user case data and shared reference law. They have different lifetimes, so they use separate storage.

| Storage | Why it exists |
| --- | --- |
| `storage/cases/<case_id>/case.db` | The dataset and analysis for one case: pages, extracted facts, reports, and supporting results |
| `data/library.db` | A small case catalogue: titles, status, tags, notes, and result metadata; lists cases without opening every case database |
| Shared law indexes | Reusable reference data; uploading a PDF does not rebuild the statute databases |

The application no longer writes duplicate `case.txt` or `meta.json` files. It also has no shared fallback case database: code must supply a case path or belong to an active simulation run. Older generated files may still exist on disk, but the current application does not use those exports.

Inside a case database, `case_meta` stores extracted summary information such as parties and item counts. It differs from the case catalogue in `library.db`, which contains display/editing metadata such as titles and tags.

The `artifacts` table is just a place for named JSON results inside `case.db`; it is not another database or folder. It retains page-coverage checks, legal-source references, quality reviews, run status, usage metrics, and reusable large-record digests. Ingestion itself creates no artifacts: its output is the `pages` table. Duplicate text-export and image-path artifacts are gone.

An interview explanation:

> “I treat ingestion as a data preparation step. I preserve the original PDFs, transcribe each page, and store one row per page with source references. Every case has an explicit output database. A separate catalogue lists the cases, and shared law indexes are reused. Failed pages stop the analysis so incomplete data cannot silently become a legal conclusion.”

## Follow a research question

`routes/research.py` creates a polling record and schedules `services/research_runner.py`. The service runs one workflow shared with the terminal command:

```text
Question → factual extraction → statute retrieval → search plan
         → Tavily search → source ranking → legal research memo
```

`research/search.py` uses `rank_bm25` for keyword matching and Chroma for semantic matching. A short reciprocal-rank-fusion function combines positions in the two result lists because their raw scores use different scales.

The planner returns five queries. The ranker assesses sources in batches, and Python limits authority scores according to source type. A blog cannot receive the same authority score as a judgment. If model ranking fails, an explicitly labeled source-quality heuristic selects candidates. The final reasoner receives the original question, statute text, source metadata, and selected assessments.

Simulation research uses Google's search grounding separately. `ai/grounding.py` retains preferred legal sources and provider-linked passages. A failed search is recorded as a limitation; unattributed model prose is not published as supported research.

## Storage and libraries

| Location | Purpose |
| --- | --- |
| `data/statutes.sqlite3` | Core-law browsing and local full-text statute search |
| `data/research_index/` | Persisted Chroma index used by hybrid research retrieval |
| `data/*_sections.csv` | One shared source copy of BNS, BNSS, and BSA sections |
| `data/extra_laws/` and `data/extra_statutes.db` | Imported laws and their SQLite FTS index |
| `data/library.db` | Saved case titles, status, tags, notes, and result metadata |
| `storage/cases/<id>/case.db` | That case's pages, extracted facts, reports, sources, and reviews |

FastAPI handles routing and background HTTP tasks. Pydantic defines and validates data shapes. The OpenAI SDK calls Vertex's compatible text endpoint; Google Gen AI handles page images and search grounding. PyMuPDF renders PDFs, Tavily searches precedents, and SQLite stores local records. No LangChain or separate agent framework is required.

Existing case data and older generated indexes were retained. The active indexes are the paths listed above. Local files need durable storage for deployment, and live job registries belong to one server process. Multiple workers or instances require shared job state and persistence.

## Configuration

Start with `backend/.env.example`. Important settings:

| Variable | Purpose |
| --- | --- |
| `GCP_PROJECT_ID`, `GCP_LOCATION` | Vertex AI project and location |
| `GEMINI_MODEL` | Routine text and grounded-search model |
| `GEMINI_REASONING_MODEL` | Extraction, record digests, factual audits and judicial reasoning; defaults to Gemini 2.5 Pro |
| `GEMINI_PAGE_TRANSCRIPTION_MODEL` | Page transcription model; defaults to Gemini Flash |
| `TAVILY_API_KEY` | Live precedent search |
| `CORS_ORIGINS` | Comma-separated permitted frontend origins |
| `MAX_UPLOAD_MB` | Maximum size of one uploaded document; defaults to 200 |
| `LLM_TIMEOUT_SECONDS`, `LLM_MAX_RETRIES` | Model timeout and bounded attempt count |
| `STAGE_SERIAL`, `STAGE_GAP_SECONDS`, `JUDGE_GAP_SECONDS` | Simulation concurrency and request pacing |
| `RECORD_CONTEXT_CHARS`, `RECORD_BATCH_CHARS` | Record-context and source-batch budgets |
| `STATUTE_DB_PATH` | Optional override for the core SQLite statute index |

Keep credentials in environment variables, local credential tooling, or your deployment's secret manager. Credential files and `.env` are excluded from container builds.

## Download the complete simulation PDF

After a simulation completes, click **Download Simulation PDF** below the result summary. The browser downloads `simulation-<case_id>.pdf`. Saved completed cases also support the endpoint `GET /api/v1/cases/{case_id}/export.pdf`.

The PDF is a client-facing simulation report, from Clarify & Extract through the final judicial assessment. It includes readable extraction findings, the neutral case report, both strategies and research reports, all four argument rounds, and the final outcome and reasoning. Page citations and legal sources are retained. A designed cover, linked contents, serif headings, restrained teal accents, generous margins, and numbered pages keep long reports readable in Hindi and English.

It excludes full transcriptions, raw JSON, database labels, internal IDs, fingerprints, timestamps, audit drafts, review logs, caches, run state, and usage metrics. The database retains those supporting records for diagnosis; they are not part of the customer download.

The export is generated on demand in memory from a read-only database snapshot. It creates no saved PDF or page-image files. Downloads wait until the simulation's final database save finishes. Original attachments and shared law databases are not included.

## Commands and verification

Run these from `backend/`:

```sh
python -m pytest -q
ruff check app scripts tests
ruff format --check app scripts tests

# Uses live providers and their associated costs:
python -m scripts.research "Your Indian legal research question"
python -m scripts.evaluate_simulation /absolute/path/to/case.pdf

# Rebuilds the research collection from the statute CSVs:
python -m scripts.build_research_index
```

Automated tests use fake provider responses. They cover API contracts, upload assembly, large-document coverage, cancellation, case isolation, argument citations, judicial review, research orchestration, and both sequential and parallel handoffs. They do not establish the correctness of live model output. Prompt simplification still needs a fresh live evaluation against source documents before relying on its legal analysis.

Build the backend container from the project root with `docker build -t courtroom-backend backend`. Its command runs `uvicorn app.main:app` on `PORT`, defaulting to 8080. Supply runtime credentials and durable storage separately.

## Reading order for an interview

Start with `main.py`, one route, and its service. Then follow the database writes and the AI call. Explain the simulation stage list before discussing individual prompts. For research, explain keyword versus semantic retrieval, why ranks are fused, and why source authority is capped. Finish with per-case isolation, cancellation, and the limits of model-generated analysis.

This project provides research and simulation support. Source attribution and model review help trace an answer; they do not replace checking original documents and legal authorities.

### Optional agent details

Each simulation agent has a collapsed **Agent details** panel, also available in Case Library. It shows API request counts, models, input/output tokens, provider totals and reasoning tokens where reported. Retries and review requests belong to the stage that made them; document transcription appears separately. Missing usage is shown as unavailable or partial, never estimated. Token accounting differs between provider endpoints, so reasoning counts must not be added again to provider totals.

`services/usage.py` records requests and `services/agent_details.py` describes observed workflow steps without another model call. Live details come from the current run; completed-run usage is saved in the case database. Older cases without stage labels cannot provide per-agent totals. These details are excluded from the customer PDF.

Simulation extraction uses Pro. Case Manager drafting and revisions use the routine Flash model, while its factual reviewer uses Pro. Consultants, research and arguments use Flash; judicial assessment and review use Pro. This routing does not itself establish improved legal accuracy; compare outputs against source documents.
