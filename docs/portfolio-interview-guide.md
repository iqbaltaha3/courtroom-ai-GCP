# Courtroom AI: portfolio interview guide

Prepared from the implementation and local verification on 22 October 2026. The first-person passages below are suggested interview wording; adjust claims of personal ownership to match your actual contribution.

## 1. Your opening introduction

“My project is called Courtroom AI. It is an AI application for Indian legal research and courtroom simulation.

A user can upload case documents or type the facts. The application reads the documents, extracts important information, prepares arguments for the prosecution and defense, and produces a judicial assessment based on the supplied record. The user can also search laws, run a separate legal research workflow, ask questions about a completed simulation, and download its report.

My main engineering focus was controlling how information moves between AI steps. I separated document reading from legal reasoning, used structured outputs, added source references, and added factual review before important reports are published.

The frontend uses React and TypeScript. The backend uses FastAPI and Python. Gemini runs through Google Vertex AI. The research workflow combines BM25 keyword search, Chroma vector search, and live web search. The application has separate Docker containers for the frontend and backend, with configuration targeting Google Cloud Run.”

## 2. Explain the complete project in this order

### Start with the problem

“A case can contain many pages, different people, several allegations, and different kinds of evidence. Reading everything and comparing both sides takes time.

I wanted to turn these documents into an organized workflow. The user should be able to see the facts, both sides' arguments, relevant sources, and the final assessment. They should also be able to inspect the intermediate reports.

The difficult part is that an LLM can produce a confident answer even when it has misunderstood the input. So I designed checks around the model, especially before one model's output becomes another model's input.”

### Explain the application structure

“The browser runs a React application. It sends requests to a FastAPI backend. The backend handles uploads, databases, AI calls, research, and report generation.

I separated the backend into routes, services, document processing, simulation, research, and database modules. A route handles an HTTP request. A service coordinates the longer workflow. The AI modules handle model calls, and database modules handle storage.

This separation makes it easier to test the workflow without making real AI calls.”

The actual product areas are Simulation, Statute Library, Legal Research, Case Library, and Case Chat. Drafting currently displays a “Coming Soon” page.

### Follow the user's upload

“When a user starts a case, the backend creates a UUID, which is a unique case identifier. Documents are grouped as allegation or FIR, petition or charge sheet, and supporting evidence. Typed facts are also supported.

The browser sends files in 8 MiB chunks. The backend checks each chunk's offset and file metadata, then assembles the file. A completed file replaces its destination only after the transfer finishes.

This keeps individual upload requests small. It also prevents an incomplete transfer from replacing a previously saved document.”

The configured per-file limit defaults to 200 MB. This is a file-size limit, not evidence of a tested maximum case size. Uploading in chunks does not make the whole AI workflow use constant memory: the run-start route later loads the selected PDF bytes into memory.

### Explain document ingestion

“First, I validate every PDF. I reject unreadable, password-protected, and empty PDFs before clearing previous case records or spending model calls.

Then I use PyMuPDF to render each page as an image at 200 DPI. Gemini transcribes the image. The instruction is to preserve the original Hindi or English text, names, dates, numbers, and section references. It must not translate or start legal analysis during this step.

I chose one page-reading path for both scanned and digital PDFs. This keeps the workflow consistent, but it means even a digital PDF needs a model call for transcription.

Page images stay in memory. I store the original uploaded PDFs and the transcribed text. Every saved page has its document name, page number, and a SHA-256 fingerprint of the source PDF.

The fingerprint helps identify the original source. Page IDs such as P1 and P2 let later reports refer back to a specific source page.”

Typed facts enter the same `pages` table without a transcription call. The transcription pool defaults to four workers, with a bounded queue. One coordinator saves results in source order. A page that still fails after retries stops ingestion; a partial record must not silently become a complete analysis.

### Explain structured extraction

“After transcription, another AI step extracts laws, witnesses, evidence, people, places, and timeline events. This is structured extraction: I turn document text into fields the application can process.

I use JSON Schema in the model request and Pydantic to validate the response in Python. For example, the output must contain lists for laws, witnesses, evidence, entities, and timeline events.

This is more reliable than asking for a paragraph and trying to guess its structure later. However, a valid JSON object can still contain a wrong fact, so schema validation is only the first check.

Extraction works in bounded batches, normally up to two page segments. Very long pages are split while keeping the original page ID. If a batch fails, the system can retry individual page segments. Unresolved failures stop the pipeline.”

One important domain decision is preserving the Act and section exactly as written. The application must not turn an IPC section into a BNS section simply because the numbers look similar. This is a rule in the prompts and retrieval design; it is not a verified legal crosswalk.

### Explain the neutral case report and review loop

“Next, the Case Manager creates a neutral report. It describes the parties, chronology, allegations, evidence, witnesses, procedural stage, and missing information.

It receives the structured extraction and the source record. The source record takes priority if they disagree.

Then a separate factual review call checks the draft. It looks for material errors, such as mixing two people, inventing an incident time, or treating an allegation as proven evidence.

The draft uses the routine Flash model. The factual reviewer uses Pro. If the reviewer finds an error, the next drafting request receives the original input, the rejected draft, and the review findings. Without the rejected draft, the model would be trying to correct an answer it could not see.

There are at most three drafts: the first attempt and two revisions. If material errors remain, the workflow stops. The rejected drafts and reviews are retained for diagnosis, but the report is not published as approved.”

This is a **generate–review–revise loop**. “Separate reviewer” means a separate model call and role. It does not mean independent human verification; model errors can be shared across calls.

The active IDE test, `test_case_manager_review.py`, demonstrates this with an invented “10 pm” occurrence time. It checks that the revision gets the source, the bad draft, and the correction, and that repeated rejection never publishes the report.

### Explain large-document context management

“A large case may be too long to send to every AI step. I do not simply cut off the end of the document.

Small records are supplied directly. Large records are split into bounded batches. Each batch becomes a page-cited digest, and another call reviews that digest against its input.

If all the digests are still too large, I summarize groups of digests again. This is hierarchical summarization. I check the page-reference coverage at each level.

The original transcriptions stay in the database. For large records, important case and judicial reports are also checked against batches of original transcriptions, so their reviews do not depend only on summaries.

I cache reviewed digests using content fingerprints. Unchanged batches can reuse their digests. Changed input causes the full context cache to be rebuilt.”

The default context budget is approximately 100,000 characters, with 24,000-character source batches and digests capped at 6,000 characters. These are character budgets, not exact token budgets. Page coverage confirms which pages were processed; it does not prove every material fact survived compression. Reviews compare transcriptions, so an earlier transcription error can still propagate.

### Explain the specialist roles

“Once the neutral report passes review, I prepare a prosecution strategy and a defense strategy. Both sides receive the same case record and statutory context, but have different roles.

Then each side prepares legal research. Finally, the application runs four argument stages: prosecution opening, defense response, prosecution closing, and defense closing.

These are specialist agents implemented as Python functions with role-specific prompts and typed handoffs. Python decides their order. The system does not let the model choose an unrestricted sequence of actions.

I used a fixed workflow because the dependencies are known. It is easier to inspect which inputs reached each stage and where a failure happened. This implementation uses ordinary Python rather than LangChain or LangGraph.”

There are eleven visible simulation stages after ingestion. An agent is not a separately trained model or a separate deployed service. Some stages make multiple calls for planning, generation, review, or repair.

### Explain sources and legal grounding

“For simulation research, I use Gemini with Google Search grounding. Grounding means the model receives retrieved information and the provider supplies links between passages and sources.

I retain source metadata and supported passages, and filter for preferred government and legal-repository domains. Prosecution sources receive RP identifiers, defense sources receive RD identifiers, and statutory research receives ST identifiers.

The application also retrieves locally available statutes using exact Act and section lookup. When local coverage is missing, it can try grounded statutory research.

If live research fails, the report records that limitation. It does not treat unsupported model text as retrieved legal evidence.”

The simulation research planner is asked for five queries, but its schema permits two to five. By contrast, standalone research enforces exactly five nonempty queries in Python. Search grounding supplies attribution; it does not independently verify full judgments or whether a decision remains valid law.

### Explain the argument checks

“Every structured submission has a proposition, factual basis, legal analysis, source references, and a limitation.

I validate that page and authority IDs exist in the supplied material. I also require a factual page reference for each submission.

For replies, each opposing submission has an identifier. The next side must answer or concede every opposing point using a `responds_to` field. Python checks for missing and unknown IDs. If the handoff is invalid, the model gets bounded repair attempts.

This makes the debate easier to inspect. The defense cannot satisfy the application just by producing an unrelated paragraph.”

These checks validate reference existence and declared reply coverage. They cannot prove that a cited page supports a claim or that a rebuttal is persuasive. Semantic correctness still needs evaluation.

### Explain the judicial assessment

“The judicial stage receives the record, earlier reports, and retrieved source passages. It uses Pro to assess the strongest arguments from both sides.

The output includes findings for alleged offences and accused people or groups, source references, missing evidence, and unresolved questions.

The decision must fit the documented stage. If the record is only an FIR or investigation record, it should produce a preliminary assessment. It should not invent a completed trial or a sentence.

A separate Pro review checks the proposed assessment before publication. If the review still rejects the second attempt, no final assessment is published.

The selected judge name is a displayed presiding identity. The current assessment prompt stays neutral and does not use the profile files to imitate a judge's conviction tendency.

The displayed confidence is a self-assessment of record sufficiency. It is not a calibrated probability that someone is guilty.”

The Case Manager repair loop includes the rejected draft explicitly. The judicial retry currently appends reviewer findings without explicitly appending the rejected decision. That is an implementation difference to improve, not something to describe as already solved uniformly.

### Explain standalone legal research

“The application also has a separate workflow for a typed legal question. It extracts the supplied facts, retrieves statutes, plans web searches, ranks sources, and writes a research memo.

For statute retrieval, I use hybrid search. BM25 finds sections with matching words. Vector search finds sections with similar meaning, using embeddings stored in Chroma.

An embedding is a numerical representation of text. It helps search when the question and the statute use different words. I also expand common legal shorthand into statutory wording, such as adding the formal concepts associated with FIR.

BM25 scores and vector scores use different scales. I combine their result positions using reciprocal rank fusion, or RRF. A section that ranks well in both lists receives a stronger combined score.

The index stores statute sections as retrieval units. The build script uses the same three statute CSVs for the persisted vector collection and the BM25 corpus. It uses Chroma's default embedding function rather than an explicitly selected model in the application code.”

The retrieved core corpus contains BNS, BNSS, and BSA. Uploaded case pages are stored in SQLite and passed through the record-context system; they are not added to this statute vector index. Extra laws shown in the Statute Library are indexed separately for browsing and search; those extra datasets do not automatically enter the simulation or hybrid research workflow.

“The standalone planner must return exactly five queries. Tavily runs those searches concurrently, with up to three results per query. I remove duplicate URLs and limit ranking to fifteen candidates.

The LLM assesses candidates in batches of five. It classifies the source and scores relevance, source quality, judicial authority, and factual similarity.

Then Python caps the authority score by source type. For example, a source classified as a legal blog cannot get more than 20 out of 100 for judicial authority, even if the model initially assigns 100.

Python combines the scores and selects up to five candidates. If model ranking fails, an explicitly labeled source-quality heuristic is available. The final reasoner receives the original question, statute text, candidate metadata, and assessments.”

Authority and relevance each contribute 35% to the normal combined score; source quality and factual similarity each contribute 15%. Source classification itself can be wrong. The fallback heuristic is a rough ordering, not a substitute for verified judgments. The ranker emits `keep_for_reasoner`, but the current selector uses scores and redundancy rather than that boolean.

Research is deliberately bounded: statute context is capped at 1,200 characters per section, search content at 700 characters per candidate, and final candidate content at 500 characters. This reduces context and cost, but can omit exceptions and important judgment details. The workflow researches snippets; it does not systematically read every full judgment.

### Explain persistence, case chat, and reports

“Every case has its own SQLite database. It stores pages, extracted information, stage reports, and JSON artifacts such as reviews, sources, digests, and usage metrics.

A separate library database stores titles, status, notes, tags, and result metadata. This lets the application list cases without opening every case database. Shared statute indexes are reused across cases.

The run carries a `RunContext` containing the database path, selected judge, cancellation event, and metrics. Python context variables carry this into worker threads. This prevents one run from accidentally using another run's database.

Case Chat can use text-to-SQL. The model proposes a SELECT query, the backend executes it in query-only mode, and the model answers using the returned rows. The endpoint supplies schema information and short report previews; it does not send the entire record for every question.

The final simulation PDF is built on demand from a read-only database snapshot. It includes the user-facing reports and citations. It excludes full transcriptions, internal reviews, usage details, and database bookkeeping. PDF generation does not need another AI call.”

Chat currently requires a completed case in the live registry. Saved reports and PDF exports can work without live registry state when the files remain available, but chat cannot currently reopen an archived case after a server restart. Chat requests also do not send earlier conversation turns to the backend. The database fetch happens before the result is sliced to thirty rows, so that slice is not a database-level query limit.

### Explain latency, concurrency, and reliability

“AI calls can take a long time. I run blocking simulation work through `asyncio.to_thread`, so the FastAPI event loop can keep serving progress requests.

Document processing uses a bounded thread pool. The two consultant stages and the two research stages can run in parallel because each pair has independent inputs. The argument stages stay ordered because they respond to earlier submissions.

Sequential stage execution is the default. I chose lower simultaneous provider load over the shortest possible runtime. There are also gaps between stage groups, including a longer gap before the judicial stage, to reduce quota pressure.

The model wrapper rejects empty and truncated completions. Structured responses are parsed and validated. Retries are bounded, with backoff and a larger output allowance for regenerated text. I reserve a separate thinking allowance so reasoning does not consume the entire visible-answer budget.

The user sees page and stage progress through WebSockets. REST polling also refreshes state and saved reports. These updates describe workflow progress; they are not token-by-token answer streaming.

If the user cancels a run, a cancellation event blocks later calls and report writes at defined checkpoints. A provider request already in progress cannot be recalled. The worker must settle, and its canceled result must not become a published report.”

Concurrency bounds are mostly per workflow, not a global admission limit across all users. Several cases can still multiply model load. Nested retries at the request, page, and workflow levels can also increase total calls and latency.

### Explain model routing and observability

“I use different models for different responsibilities. The default page transcription model is Gemini 2.5 Flash. Routine drafting, strategies, simulation research, and arguments also use Flash. Structured simulation extraction, large-record digests, factual audits, and judicial generation and review use Pro.

The intention is to keep repeated routine work affordable while using the stronger reasoning model for important interpretation and review. This is a design choice, not a measured claim that Pro improved legal accuracy in this application.

I record model names, attempted requests, request duration, failure status, and provider-reported tokens for simulation stages. Retries and reviews count toward the stage that made them.

The Agent Details panel uses recorded data and ordinary code. It does not ask an LLM to describe what supposedly happened. Missing usage stays unavailable rather than being estimated.”

The standalone research final reasoner has a configuration subtlety: `REASONING_MODEL` falls back to `GEMINI_MODEL` and then Flash if `GEMINI_REASONING_MODEL` is unset. The example environment sets `GEMINI_REASONING_MODEL` to Pro. Simulation's `GEMINI_REASONING_MODEL` constant defaults directly to Pro. State your actual environment when discussing research model routing. Usage instrumentation is attached to `RunContext`; standalone research does not currently receive the same persisted per-stage usage accounting as simulation.

### Finish with deployment

“I packaged the frontend and backend separately using Docker.

The frontend uses a multi-stage build. Node installs dependencies with `npm ci` and Vite builds the static files. The final container uses Nginx to serve them. Nginx also falls back to `index.html` for React routes.

The backend container uses Python 3.12 and runs FastAPI through Uvicorn. It listens on the `PORT` environment variable, defaulting to 8080.

The frontend's production API URL points to a Cloud Run backend in `asia-south1`. Vite embeds that URL during the frontend build. The backend's Vertex AI location is configured separately and defaults to `us-central1`.

Locally, Vite proxies API and WebSocket requests to the backend. In production, the browser calls the configured backend directly. CORS defines permitted frontend origins.

The backend authenticates to Vertex AI using Application Default Credentials. It uses the OpenAI SDK against Vertex's compatible text endpoint, and Google's Gen AI SDK for image transcription and Google Search grounding. Using the OpenAI SDK here does not mean the application is calling an OpenAI model.

For a stronger production deployment, I would move documents to object storage, case records to a shared database, and long-running workflows to durable workers. The current application keeps active job state in memory and uses local SQLite files. Those choices are simple for a single-process prototype, but they need changes before reliable multi-instance scaling.”

The repo contains Cloud Run URLs and container configuration. It does not establish which revision is live, whether it matches this code, or its current IAM, storage mounts, billing mode, resource limits, or instance settings. No deployment was performed during this analysis.

## 3. AI and system decisions you should be able to defend

| Decision | Simple reason | Tradeoff |
| --- | --- | --- |
| Transcribe first, interpret later | Keep document reading separate from legal reasoning | More calls; transcription errors can still spread |
| Render every PDF page | Consistent path for scanned and digital input | Model cost even when embedded text is usable |
| JSON Schema plus Pydantic | Give downstream code predictable fields | Valid shape does not guarantee true facts |
| Fixed specialist workflow | Explicit stage dependencies and inspectable handoffs | Less flexible for unexpected tasks |
| Generate–review–revise | Catch material problems before publishing | Added latency, cost, and correlated model errors |
| Stop on unresolved page failures | Avoid silently analyzing an incomplete record | A small failure can block the whole case |
| Flash and Pro routing | Match model spend to responsibility | Needs evaluation to justify actual quality gains |
| Reviewed hierarchical digests | Keep large-record requests bounded | Compression can omit detail; reviews cost more |
| BM25 plus vector retrieval | Cover both exact words and similar meaning | More dependencies and index maintenance |
| Reciprocal rank fusion | Combine rankings without comparing raw score scales | Rank agreement does not prove legal relevance |
| Section-level statute units | Preserve the identity of legal provisions | Long sections and context caps can hide exceptions |
| Python source-authority caps | Enforce rules outside an LLM prompt | Caps depend on a potentially wrong classification |
| Source IDs and reply IDs | Check traceability and declared argument coverage | IDs do not prove substantive support |
| SQLite per case | Simple case isolation and local persistence | Unsuitable as-is for distributed writes and job state |
| Sequential stages by default | Reduce simultaneous provider pressure | Slower completion |
| WebSockets plus polling | Fast updates plus another path to refresh state | Extra traffic; shared state still needed across instances |
| Persist reports as stages finish | Preserve inspectable completed work | Not durable checkpoint-and-resume execution |
| Provider-reported usage | Measure actual reported calls and tokens | Partial metadata; no measured financial savings |

## 4. Deployment points for deeper questions

The deployment changes below are proposed future work. They are not features already implemented.

1. **Durable files:** store original PDFs and exports in object storage. Cloud Run's writable container filesystem is in memory and disappears when the instance stops. Existing files in an image are not the same as durable newly written case data. See [Cloud Run's container contract](https://cloud.google.com/run/docs/container-contract#file_system).
2. **Durable jobs:** separate HTTP handling from execution. `asyncio.create_task` and FastAPI background tasks currently run in the application process. A process restart can lose work. Use durable job state and a worker execution model, with retries and idempotency designed explicitly.
3. **Background CPU:** the simulation returns a run ID before its work finishes. Cloud Run CPU allocation outside request handling depends on its billing configuration; a live WebSocket is not a durable job guarantee. Verify the deployment settings or use a suitable worker design. See [Cloud Run billing settings](https://cloud.google.com/run/docs/configuring/billing-settings).
4. **Shared state:** replace in-memory registries before adding workers or instances. Route affinity is only best effort; requests and reconnects can reach different instances. Use shared persistence and event delivery. See [Cloud Run WebSockets guidance](https://cloud.google.com/run/docs/triggering/websockets).
5. **Runtime identity:** use an appropriately scoped service identity for ADC and supply the Tavily key through runtime secret configuration. The repo excludes local credential files from container builds. The local checkout contains a credential JSON file; keep it out of any portfolio source package.
6. **Build configuration:** changing `VITE_API_URL` requires rebuilding the frontend under the current Dockerfile. Nginx is serving static assets and does not proxy production API calls. Backend CORS must allow the deployed frontend origin.
7. **Indexes and startup:** the research collection must exist before retrieval can work. Its embedding assets must also be available. AI clients and research imports load lazily, reducing initial startup work but shifting work into the first relevant request. The BM25 corpus is currently rebuilt when `load_index()` is called for each research run.
8. **Capacity:** set a global limit on active AI workflows, and budget memory for uploaded PDF bytes, rendered pages, transcription results, indexes, and local file writes. Per-case thread limits do not provide a global quota controller.
9. **Health:** `/health` checks configuration and local statute availability without making a model call. It does not prove that cloud credentials work or that research indexing is ready. Nginx `/healthz` only confirms that the frontend server responds.
10. **Access and operations:** authentication, case ownership authorization, durable recovery, automated deployment, and shared production monitoring are not established by this source tree. Per-case database isolation is not user access control. No CI/CD workflow or infrastructure-as-code deployment was found.

## 5. Likely interviewer questions and simple answers

**Why not send the complete PDF to one model and ask for the final answer?**

“That would make errors difficult to locate. I separate transcription, extraction, research, arguments, and assessment. Each stage has a clear input and output, and important handoffs have checks.”

**Is this a multi-agent system?**

“It is a fixed workflow with multiple specialist AI roles. The roles share models but use different prompts and inputs. Python controls the sequence; the agents do not run an unrestricted autonomous loop.”

**Did you train or fine-tune a model?**

“This implementation uses pretrained Gemini models. The engineering work is in prompts, retrieval, context management, orchestration, validation, evaluation, and deployment.”

**Where exactly is RAG used?**

“The standalone research feature retrieves local statute sections using BM25 and Chroma, then supplies their text to the planner and final reasoner. Simulation separately uses local exact statute lookup, page-cited case context, and Google Search grounding.”

**Why both BM25 and vector search?**

“BM25 helps when the user uses exact legal words. Vector search helps when the question uses different words with similar meaning. I fuse the rankings using RRF because their raw scores are not directly comparable.”

**Does every document become a vector embedding?**

“No. The Chroma research index contains shared statute sections. Uploaded case pages go into the case's SQLite database and are supplied directly or through reviewed digests.”

**How do you prevent hallucinations?**

“I reduce the risk using supplied records, source retrieval, structured outputs, reference checks, factual review, and refusal to publish some failed stages. These checks do not eliminate hallucinations, and important outputs still need comparison with original sources.”

**Why use an LLM reviewer if it can also make mistakes?**

“It is an additional check that can identify some inconsistencies. I treat it as a useful filter, not a guarantee. The reviewer must receive source material, and meaningful evaluation still needs human-reviewed examples.”

**Why not use LangChain or LangGraph?**

“The current workflow has a small, known set of dependencies, so ordinary Python makes the control flow explicit. A framework could become useful for more complex state transitions or checkpointing, but this implementation does not need one to express its current sequence.”

**How do you measure quality?**

“I distinguish software tests from model-quality evaluation. Tests check things like missing-page rejection, case isolation, citation IDs, reviewer failures, and cancellation. Model quality needs live evaluation against original records with predefined acceptance criteria.”

**What is your biggest current limitation?**

“The workflow has useful local checks, but live output quality still needs a fresh evaluation of the current pipeline. For deployment, durable jobs and shared persistence are the biggest architectural next steps.”

**How would you scale it?**

“I would first move documents, case data, and job state outside individual instances. Then I would use durable workers, enforce global provider concurrency, and share progress events. Increasing the number of web instances alone would not fix the current state model.”

**How do you manage cost?**

“I route different stages to Flash or Pro, bound search candidates and context, cache reviewed digests, and limit concurrency. Simulation records provider token usage. I have not established a benchmark showing a particular cost reduction.”

**What happens when the user clicks stop?**

“The application sets a cancellation event. It blocks later stages and checks before publishing reports. An already running provider request still has to finish; cancellation cannot retrieve spent tokens or instantly recall that request.”

**Is case chat a normal conversational RAG chatbot?**

“It currently uses database schema information, report previews, and optional read-only SQL. Previous chat turns are displayed in the browser but are not included in the backend request.”

## 6. A concrete engineering story to tell

“One failure I designed around was an invented incident time. A model may confuse a registration time or a room number with the time of the alleged event.

I addressed this at several levels. The extraction prompt says to keep those details separate. The Case Manager reads the source record, and a separate factual reviewer checks its draft. If rejected, the revision receives the original source, the rejected draft, and the exact correction. After two unsuccessful revisions, the workflow stops and retains the findings.

I added a test where a fake draft invents 10 pm. The test confirms that the repair request contains all three inputs and that the approved report removes the invented time. A second test confirms that repeated rejection never publishes the bad report.

This taught me that an AI pipeline needs explicit failure handling and useful repair inputs, not just a stronger instruction to be accurate.”

This story is supported by the code and tests. Do not present the synthetic test as a measured improvement on real legal records.

## 7. Evaluation and claims you can defend

Local verification performed for this guide:

- `pytest -q` from `backend/`: **73 passed**, with dependency deprecation warnings, using the available Python 3.12 environment.
- `npm run build` from `frontend/`: **passed**.
- `frontend/node_modules/.bin/tsc --noEmit -p frontend/tsconfig.json`: **passed**.
- The backend's existing `.venv` does not contain pytest; its test command failed before collection. The successful run used the available pytest installation, so this is not validation of a freshly installed environment or the Docker image.
- No live AI calls, index rebuild, Docker build, or cloud deployment was performed during this analysis.

The tests cover API behavior, safe upload assembly, explicit ingestion destinations, failure gates, large-record processing, digest caching and coverage, rejected reviews, invalid references, argument handoffs, concurrent-case isolation, cancellation, source filtering, research orchestration, usage accounting, and PDF export.

`docs/simulation-evaluation.md` records an older live iteration that finished but failed factual acceptance. A focused transcription experiment was useful, but the notes explicitly say the later pipeline still needed a fresh live end-to-end evaluation. Do not quote the historical runtime as a benchmark for this implementation.

A sensible next evaluation would use several human-reviewed records and questions, checking transcription fidelity, person/role preservation, evidence versus allegation, correct Act/section handling, retrieval relevance, source support, opposing-point coverage, procedural scope, latency, and tokens per completed case. Establish the expected answers and acceptance criteria before running comparisons.

Avoid unsupported claims such as “predicts court outcomes,” “95% legally accurate,” “hallucination-free,” “production-ready autoscaling,” or “reduced cost by X%.” Judge profiles are not being used for behavioral prediction, Drafting is a placeholder, and case isolation does not establish authenticated multi-user privacy.

## 8. Resume wording

**Courtroom AI — AI Engineering Portfolio Project**

- Built a React and FastAPI application for Indian legal research and courtroom simulation, processing Hindi/English PDFs into page-cited facts, opposing submissions, and reviewed judicial assessments.
- Implemented structured Gemini workflows on Vertex AI with Pydantic validation, factual review and revision loops, bounded context processing, case isolation, cancellation, and per-stage usage tracking.
- Combined BM25 and Chroma retrieval with reciprocal rank fusion for statute research; added web-source ranking, Python-enforced authority caps, and separate Docker configurations targeting Cloud Run.

Use “deployed on Cloud Run” only when you can confirm a working deployment of the relevant version and explain its actual runtime configuration. The source tree establishes deployment preparation and configured URLs.

## 9. Source map for your preparation

| Topic | Main implementation |
| --- | --- |
| Application setup | `backend/app/main.py`, `config.py`, `schemas.py` |
| User flow and API client | `frontend/src/App.tsx`, `pages/`, `api/client.ts` |
| Uploads, progress, reports | `backend/app/routes/cases.py` |
| Transcription and source storage | `backend/app/documents/ingestion.py`, `transcription.py` |
| Large-record context | `backend/app/documents/context.py` |
| Simulation sequence | `backend/app/simulation/pipeline.py` |
| Extraction and review loop | `backend/app/simulation/extraction.py`, `case_manager.py` |
| Strategies and arguments | `backend/app/simulation/consultants.py`, `advocacy.py` |
| Assessment and schemas | `backend/app/simulation/judgment.py`, `models.py`, `prompts.py` |
| Model calls and search attribution | `backend/app/ai/llm.py`, `grounding.py` |
| Hybrid retrieval and ranking | `backend/app/research/`, `scripts/build_research_index.py` |
| Background execution and run isolation | `backend/app/services/runner.py`, `run_context.py`, `concurrency.py`, `task_registry.py` |
| Standalone research orchestration | `backend/app/services/research_runner.py`, `scripts/research.py` |
| Persistence and statute browsing | `backend/app/database/` |
| Case chat | `backend/app/routes/chat.py`, `database/cases.py` |
| Measured agent details | `backend/app/services/usage.py`, `agent_details.py`, `frontend/src/components/AgentDetails.tsx` |
| Export | `backend/app/services/pdf_export.py` |
| Deployment | Both Dockerfiles, `frontend/nginx.conf`, `frontend/vite.config.ts`, production API URL, ignore files |
| Evidence and verification | `backend/tests/`, `scripts/evaluate_simulation.py`, `docs/simulation-evaluation.md` |

The review followed application source, tests, scripts, configuration, and deployment files. Historical databases, generated output files, judge-profile prose, dependency internals, and private credentials were not treated as proof of current application behavior or live deployment.
