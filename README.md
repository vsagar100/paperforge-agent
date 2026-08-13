# PaperForge Agent 0.2.1

PaperForge is an evidence-first engineering author-assistance workflow. It turns a research topic,
a short brief, and supplied project files into a progressively reviewed manuscript while preserving
a durable audit trail. It automates structure, drafting, review, safe editing, consistency checks,
and export; it never manufactures experiments, measurements, citations, or implementation facts.

## What changed in 0.2.1

The response handoff is now data-loss safe and requires one less command:

- `responses.yaml` is treated as user-owned input after generation;
- a normal `paperforge run` automatically imports every non-empty response;
- partially completed answers remain in the file and only unanswered items stay open;
- template synchronization merges state without blanking uncommitted user text;
- invalid YAML stops with a precise error and the original file remains unchanged;
- `paperforge answer-all` remains available, but is no longer required for the normal flow.

The 0.2 intake and state design continues to prevent repeated question batches:

The intake and state engine were redesigned to eliminate repeated question batches:

- exactly one consolidated intake question round;
- at most five grouped questions by default and five over the entire workflow;
- IDs are assigned by PaperForge (`Q-001`, `Q-002`, ...), not by the model;
- semantic keys, similarity checks, and a persistent answer ledger prevent reworded duplicates;
- once the batch is answered, intake closes without another intake model call;
- later stages cannot reopen user interaction; attempted questions become visible review findings;
- `needs_input` is repaired automatically when no open question exists;
- v0.1 state is migrated automatically, including the answered-question deadlock;
- old excess questions are closed to the configured interaction budget.

This is a complete release archive, not a source-code patch.

## Implemented workflow

1. Local evidence ingestion
2. Consolidated intake and profile normalization
3. Evidence preparation
4. Outline design
5. Complete initial manuscript
6. Methodology review
7. Engineering-integrity review
8. Citation and claim-link audit
9. Section enhancement
10. Abstract review
11. Originality-risk review
12. Manuscript consistency
13. Journal compliance
14. Independent final audit
15. Markdown, quality-report JSON, and DOCX export

Stages use bounded retries. A retry occurs only when a safe change was actually made; PaperForge
does not spend model calls repeating an unchanged request. Non-blocking quality deficits remain in
the final author-action report. A blocking scientific defect stops the workflow without creating a
fake question or hiding the reason.

## Supported inputs

Files placed in the following project folders are ingested automatically on every run:

| Folder | Typical content |
| --- | --- |
| `inputs/` | Research brief and factual notes |
| `sources/` | PDF, DOCX, TXT, Markdown, BibTeX, RIS, JSON |
| `data/` | CSV, TSV, XLSX, JSON, experiment notes |
| `figures/` | PNG, JPEG, TIFF, SVG and captions |

Each item receives a stable evidence ID, SHA-256 checksum, source path, and locator. Unchanged files
are reused. Text sent to a model is bounded by per-document and total context budgets. Figure files
are registered, but pixel interpretation is not claimed automatically.

Install the `documents` extra for PDF, DOCX, XLSX, and DOCX export support.

## Windows installation

From the extracted application directory:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev,documents]"
```

Create `.env` beside `pyproject.toml`:

```dotenv
OLLAMA_API_KEY=your_actual_key_only
```

`.env` is already excluded from Git. Do not prefix the value with `Bearer`.

## Start a project

```powershell
paperforge init projects\uav-fire-paper `
  --topic "Edge-cloud-enabled UAV thermal fire detection and geo-tagged alerting" `
  --domain "mechanical-engineering" `
  --journal "DJES"
```

Edit the generated file once with facts already known to you:

```text
projects\uav-fire-paper\inputs\research_brief.md
```

Then copy authentic papers, data, and figures into the project folders and run:

```powershell
paperforge run projects\uav-fire-paper
```

PaperForge first extracts the supplied files. If essential facts remain missing, it writes one
response template:

```text
projects\uav-fire-paper\inputs\responses.yaml
```

Fill all answers in that single file, save it, and rerun:

```powershell
paperforge run projects\uav-fire-paper
```

The second run imports the answers transactionally, closes intake, and continues without another
intake discovery call. The explicit command remains available for scripts that prefer it:

```powershell
paperforge answer-all projects\uav-fire-paper
```

## Existing v0.1 or v0.2.0 project

Back up the project directory, replace the application source with this release, and reinstall:

```powershell
pip install -e ".[dev,documents]"
paperforge status projects\uav-fire-paper
paperforge run projects\uav-fire-paper
```

`audit/state.json` is migrated automatically from schema 1 to schema 2. Existing answers are
preserved. If the old state says `intake: needs_input` but has no unanswered question, it is repaired
to `pending`, the intake gate is closed, and the workflow continues.

Do not run `paperforge init` again for an existing project.

## Ollama Cloud configuration

The default endpoint is direct Ollama Cloud:

```yaml
provider:
  active: ollama
  ollama:
    host: https://ollama.com
```

The default role model is `gpt-oss:20b` so the application starts with a comparatively modest Cloud
model. Model visibility from `/api/tags` does not guarantee plan entitlement. Check configuration
and authentication with:

```powershell
paperforge doctor projects\uav-fire-paper
```

If Ollama returns `403`, PaperForge now displays the response reason and does not waste retries on
that non-retryable request. Use only exact model names shown for the account and included in its plan.

It is a Cloud request whenever the host is `https://ollama.com`. A local model is used only when the
host is deliberately changed to a local endpoint such as `http://localhost:11434` or `OLLAMA_HOST`
is set to that value.

For the DJES manuscript, set the actual requirement in the project config:

```yaml
journal:
  abstract_max_words: 230
  citation_style: ieee
  manuscript_type: research_article
```

## Useful commands

```powershell
paperforge status projects\uav-fire-paper
paperforge ingest projects\uav-fire-paper
paperforge validate projects\uav-fire-paper
paperforge export projects\uav-fire-paper
paperforge doctor projects\uav-fire-paper
```

Offline deterministic workflow test:

```powershell
paperforge init projects\demo --topic "Condition monitoring of rotating machinery"
paperforge run projects\demo --provider mock
```

## Project output

```text
project/
├── paperforge.yaml
├── inputs/
│   ├── research_brief.md
│   └── responses.yaml
├── sources/
├── data/
├── figures/
├── evidence/registry.json
├── claims/registry.json
├── manuscript/
│   ├── current.md
│   └── versions/
├── reviews/
├── audit/state.json
└── outputs/
    ├── manuscript.md
    ├── manuscript.docx
    └── quality-report.json
```

`submission_ready=true` is written only when every configured stage passes and no unresolved high or
blocking finding remains. A generated document with `submission_ready=false` is a useful complete
working draft, not a claim that the research is ready for publication.

## Quality and safety contract

- Numeric and engineering claims must trace to supplied evidence.
- Scientific changes are never auto-applied under the default policy.
- Safe style and structure changes may be applied and versioned.
- Missing non-critical facts become explicit markers or review actions, not repeated questions.
- Missing critical facts can trigger the one intake batch, never an endless dialogue.
- Originality review improves synthesis and attribution; it does not conceal plagiarism.
- API keys are read from the environment and never written into project state or review files.

See `docs/ARCHITECTURE.md` for component boundaries and invariants and `docs/MIGRATION.md` for the
v0.1 upgrade checklist.
