# PaperForge 0.2.1 Architecture

## Product boundary

PaperForge assists an author in expressing and validating authentic engineering research. It is
not an autonomous scientist. It cannot create missing experiments, measurements, ethics approvals,
novelty, standards, sources, or citations. Its autonomy boundary is based on scientific risk.

## Component model

| Component | Responsibility | Must not do |
| --- | --- | --- |
| `ProjectStore` | Atomic state, manuscript versions, locks, migration | Interpret research |
| `DocumentIngestor` | Extraction, checksums, stable evidence IDs | Claim external scholarly verification |
| `QuestionManager` | One-round budget, IDs, deduplication, answer ledger | Let a provider control persisted IDs |
| `StageExecutor` | Typed model contract, context budgets, deterministic checks | Mutate state outside safe-change rules |
| `WorkflowEngine` | Ordered transitions, bounded retries, recovery, completion | Enter `needs_input` without an open question |
| `ModelProvider` | Authentication, HTTP behavior, retry policy, model mapping | Leak provider concerns into workflow logic |
| `OutputExporter` | Markdown, quality JSON, DOCX | Mark unsupported work submission-ready |

## State-machine invariants

1. `needs_input` implies at least one persisted open question for the same stage.
2. Only the configured consolidated intake stage may open a question round by default.
3. Intake can open at most one round and at most the configured total question budget.
4. Answered or dismissed semantic keys cannot be reopened automatically.
5. A closed intake round is resumed through a synthetic pass; the intake model is not called again.
6. Later model question proposals are converted to high-severity author-review findings.
7. `running` left by an interrupted process is recovered to `pending` before execution.
8. A passed stage is idempotent and is not rerun on a normal resume.
9. Retries are bounded and stop early when no safe change was made.
10. `submission_ready` requires every stage to pass and no unresolved high/blocking finding.
11. Synchronizing `responses.yaml` cannot replace non-empty user input with an empty state value.

These invariants directly prevent the former sequence of question batches 1–5, 6–10, 11–18 and
the deadlock `intake: needs_input` with no question displayed.

## Interaction lifecycle

### First run

1. Extract changed files and refresh evidence records.
2. Build a bounded prompt from the profile, evidence, manuscript, prior answers, and journal rules.
3. Run intake once.
4. Persist no more than the question budget under PaperForge-assigned IDs.
5. Write `inputs/responses.yaml` and stop.

### Resume after answers

1. Read non-empty values from `responses.yaml` at the start of the next normal run.
2. Record all supplied answers transactionally in the question ledger.
3. Preserve partial answers and stop only for the still-empty items.
4. Close the completed question round and set `intake_closed=true`.
5. Resume intake through a local synthetic pass.
6. Continue all remaining stages without another intake call.

The explicit `answer` and `answer-all` commands use the same ledger, but are compatibility and
automation interfaces rather than mandatory interactive steps.

### Later missing information

Later stages receive `questions_allowed=false`. If a provider violates the contract, PaperForge
does not expose another input round. The proposal becomes an `INPUT-DEFERRED-*` finding in the stage
review and final quality report. The draft can continue, while submission readiness remains false
when the unresolved item is serious.

## Persistence and migration

All JSON writes use a temporary sibling followed by atomic replacement. Response-template writes
also use atomic replacement, merge existing non-empty values, and skip rewriting when no semantic
change is needed. A cross-platform file lock prevents two `run` or answer operations from updating
one project simultaneously.

Schema-1 state is upgraded on load:

- legacy IDs such as `Q6` become `Q-006`;
- semantic keys and statuses are derived;
- existing answers are preserved;
- legacy questions are represented as one intake round;
- an answered `needs_input` deadlock becomes `pending` with `intake_closed=true`;
- excess unanswered legacy questions are dismissed above the configured budget.

The original state remains recoverable from the user's normal project backup. Migration does not
reinitialize or delete the project.

## Evidence ingestion

Evidence IDs are derived from normalized relative paths, so an edited file retains its identity.
SHA-256 detects content changes. Extraction supports text/Markdown/BibTeX/RIS, JSON, CSV/TSV, PDF,
DOCX, and XLSX. Images are registered with checksum and provenance but are not interpreted as data.

`verified=true` for auto-ingested evidence means local checksum and locator integrity, not peer-
review validation. External DOI metadata, retraction checks, and licensed full-text scholarly search
remain separate future provider boundaries.

## Model contract

The application requests a typed `LLMStagePayload` containing:

- score and findings;
- optional intake question proposals with semantic keys;
- exact manuscript patches;
- a full replacement document only for an empty initial draft;
- evidence-supported profile updates;
- audit notes.

Unstructured output receives a bounded schema-repair attempt. Model-generated question IDs are not
accepted. Context includes answered questions and explicit interaction policy on every stage.

## Safe mutation

PaperForge applies a manuscript change only when:

- it is the initial full document on a blank manuscript; or
- an exact `before` string occurs once; and
- every referenced evidence ID exists; and
- the patch is not a scientific change under the default human-review policy.

All applied manuscript changes create immutable numbered versions. Unsupported or ambiguous patches
remain in stage reviews.

## Provider reliability

Ollama transport handles Cloud and intentionally local endpoints. Authentication is required only
for the Cloud hostname. HTTP 401, 403, and 404 are non-retryable and report the server's safe error
detail. Rate limits, timeouts, connection failures, and selected server errors use bounded backoff.
Model discovery and inference entitlement are reported as distinct facts.

## Current limitations

- Citation metadata/DOI verification and retraction checks are not yet externally automated.
- PDF extraction is text-layer based; scanned documents need a future OCR adapter.
- Figure pixels are not interpreted automatically.
- The DOCX exporter is a clean generic research format, not a journal-specific template engine.
- Similarity scoring requires an authorized external service; PaperForge performs qualitative
  originality review only.
- Deterministic statistical calculators are still metric-family extensions rather than a universal
  equation engine.

These limitations produce explicit findings or author actions; they do not justify fabricated text.
