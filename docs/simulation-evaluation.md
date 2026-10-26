# Simulation Evaluation

Historical evaluation notes from before the backend cleanup. See the root README for the current structure, model settings, and commands. These observations do not validate the simplified prompts or a deployment.

## Record And Acceptance Criteria

Test record: the supplied ten-page FIR No. 0422/2023. Compare the rendered original, not just OCR or agent summaries.

Critical checks before accepting a run:

- Preserve IPC sections 147, 148, 149, 323, 307 and 286 and the FIR's Cr.P.C. 154 reference, without converting their numbers into BNS/BNSS provisions.
- Keep the eleven named accused distinct. Preserve Obada and Obaid as separate people. The specific firing/bomb allegation is against Mubbashir Haroon and Obada.
- Keep the roommate distinct from the accused list. Room 11 is not an incident time. FIR registration time is not automatically the occurrence time.
- Treat the gun, bomb and assault as allegations. The uploaded FIR does not itself supply a recovery memo, forensic examination, medical report or tested trial testimony.
- Do not turn OCR differences, blank fields or a count of unknown accused into contradictions in the complainant's account.
- Research the actual legal tests, including intent/knowledge for attempt, collective liability and explosive handling. Lack of injury or non-detonation is not automatically dispositive.
- Answer opposing submissions explicitly, with traceable record and authority references.
- Give an investigation-stage preliminary assessment on this record. Do not manufacture a trial, conviction, acquittal, sentence or procedural application.

## Observed Failures And Changes

| Observed failure | Local correction |
| --- | --- |
| Schema echoed, fields omitted, output repeatedly truncated | API schema enforcement, mandatory critical fields, no duplicate schema in prompts, separate reasoning allowance, bounded regeneration; partial output cannot pass as complete |
| OCR mistakes propagated; combined image extraction confused accused | Dedicated image-only Pro transcription before structured interpretation; raw OCR retained |
| Later blank pages overwrote identified parties | Ignore missing-name placeholders during merge |
| Partial page extraction continued downstream | Page coverage gate blocks the next stage |
| Summaries invented an occurrence time and court stage | Original pages supplied to the Case Manager plus a separate factual audit with up to two revisions; each revision receives the rejected draft and review findings |
| Research generated queries without searching | Google Gen AI search grounding with claim/source metadata, source-domain filtering and visible links |
| Legacy-law file received unrelated newer-code sections | Exact Act/section lookup; retrieve statutory material when local coverage is missing |
| Arguments lost report endings and weakly engaged opponents | Full handoffs, structured propositions, page/source IDs and required rebuttal coverage |
| Judge fabricated an acquittal after JSON failure | Remove fallback verdict; stage-aware structured decision and separate review before publication |
| Different cases shared one mutable DB and judge setting | Per-run context, per-case DB and explicit case-scoped report reads |
| Termination could return while workers still wrote reports | Cancellation checks before model calls and report writes; settle active workers before terminal state |
| Reports disappeared after reload or socket loss | Incremental report events plus polling and persisted-case report reads |

## Validation So Far

The earlier image-extraction iteration completed all stages in 281.6 seconds, but failed manual factual acceptance because it confused two accused and introduced an occurrence time. It is not an accepted legal-quality result.

A focused dedicated Pro transcription accurately preserved the critical narrative and identities against the rendered page. The final separated transcription, factual-audit and source-filtered pipeline has not yet completed a fresh live end-to-end run: the resumed environment's model credentials returned HTTP 403. Cloud-account changes and deployment were then deferred at the user's request.

Local automated tests cover structured-output failures, missing-page rejection, unknown references, retained context tails, judicial-review rejection, concurrent-case isolation, parallel stage handoffs, early report events and cancellation. These are orchestration and contract tests, not evidence that a model's legal conclusions are correct.

## Local Evaluation

From the repository root, with working local model credentials:

```sh
cd backend
python -m scripts.evaluate_simulation '/absolute/path/to/the/FIR.pdf'
python -m pytest -q
```

The evaluation writes an isolated case database and `evaluation.json` under `backend/storage/cases/<case_id>/`. The database retains raw OCR, corrected page text, agent reports, source metadata, audits and model-call metrics. Compare these against the original PDF before approving a result.

Models can be selected independently with `GEMINI_MODEL`, `GEMINI_PAGE_TRANSCRIPTION_MODEL`, and `GEMINI_REASONING_MODEL`. Current defaults are Flash for routine work and transcription, and Pro for factual audits and judicial decisions/reviews. Earlier Pro-transcription observations above describe the historical experiment.

## Remaining Limits

- Search grounding is attribution, not independent verification of full judgments or a check that a precedent remains good law. Official-source domains and judgment repositories still require substantive source review.
- OCR and model transcription can both err. The factual audit is a second model check, not a guarantee; the acceptance criteria above remain necessary.
- Confidence is self-assessed record sufficiency, not a calibrated probability of guilt.
- An in-flight provider request cannot be recalled by the stop button. Its result is discarded after cancellation, and downstream stages do not run.
- Durable jobs and storage across server restarts or multiple deployment instances remain a separate deployment concern. Local per-case isolation does not solve distributed persistence.
