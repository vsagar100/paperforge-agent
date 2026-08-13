# PaperForge Agent 1.0

PaperForge turns one research topic or synopsis into an evidence-grounded manuscript through a
resumable planning, literature, drafting, review, revision, and export workflow.

The application is designed to produce a credible journal-submission candidate. It cannot guarantee
acceptance, replace missing experiments, or certify plagiarism clearance. It never invents data,
methods, citations, equipment, approvals, or results.

## Minimal workflow

Install once from the repository root:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[documents]"
```

Copy `.env.example` to `.env` and add the Ollama key value:

```dotenv
OLLAMA_API_KEY=your_key_only
PAPERFORGE_CONTACT_EMAIL=you@example.com
```

Create and run a paper with one command:

```powershell
paperforge write "Thermal UAV fire detection using edge-cloud intelligence" `
  --project projects\uav-fire-paper
```

For a detailed synopsis:

```powershell
paperforge write --from-file synopsis.md `
  --project projects\uav-fire-paper `
  --journal "DJES"
```

Subsequent runs resume completed stages:

```powershell
paperforge run projects\uav-fire-paper
```

PaperForge does not create a model-driven questionnaire. For an existing v0.2 project, every
non-empty value already saved in `inputs/responses.yaml` is ingested as authoritative user evidence.

## What the workflow does

| Stage | Outcome |
| --- | --- |
| Prepare | Extract local files, preserve provenance, and derive supported statistics |
| Plan | Select a defensible article type and create objectives, scope, and search queries |
| Literature | Search OpenAlex, deduplicate results, and optionally verify DOI metadata with Crossref |
| Synthesis | Produce a source-level literature matrix, themes, gap, and novelty position |
| Outline | Build an evidence/reference-linked manuscript plan |
| Draft | Write the paper section-by-section using only registered facts and citations |
| Evidence review | Audit citations, literature coverage, and numeric-claim traceability |
| Methodology review | Check reproducibility, validity boundaries, and honest disclosure |
| Results review | Check metric consistency, interpretation, and objective alignment |
| Writing review | Improve cohesion, terminology, academic tone, and structure |
| Journal review | Apply configured abstract, section, declaration, and citation requirements |
| Final review | Run an independent, scope-aware publication review |
| Export | Generate the manuscript, bibliography, matrix, report, DOCX, and revision history |

Every review stage follows the same contract:

1. Run deterministic checks.
2. Ask the configured reviewer model for structured issues.
3. Normalize issue severity using PaperForge's scope policy.
4. Revise the complete manuscript when a safe correction is possible.
5. Reject a revision that introduces an unknown citation, unsupported number, placeholder, or
   truncated document.
6. Review the revised manuscript again before the stage can pass.

Model self-scores are diagnostic. They cannot turn simulation, regulation, or an external baseline
into a blocker when those activities are outside the declared scope. Integrity blockers are limited
to defects such as fabricated citations, unsupported results, contradictory facts, unresolved
placeholders, and missing or empty required sections.

## Topic-only versus original research

`--paper-type auto` is the default:

- A topic without authentic methods and results becomes a review article.
- A synopsis or project evidence containing original methods and results can become an original
  research article.
- `--paper-type original_research` without original evidence falls back to a review article by
  default instead of manufacturing a study.

Override the policy in `paperforge.yaml` only when authentic supporting material exists.

## Supported evidence

PaperForge scans these project folders on every non-resumed build:

| Folder | Formats |
| --- | --- |
| `inputs/` | Markdown, text, YAML, legacy `responses.yaml` |
| `sources/` | PDF, DOCX, TXT, Markdown, BibTeX, RIS, JSON |
| `data/` | CSV, TSV, XLSX, JSON, experiment notes |
| `figures/` | PNG, JPEG, TIFF, SVG and accompanying captions |

Each extracted item receives a stable evidence ID, SHA-256 checksum, source path, and locator.
Images are registered but their pixels are not interpreted automatically.

When authentic TP, TN, FP, and FN counts are present, PaperForge deterministically derives accuracy,
precision, recall, specificity, F1, FPR, FNR, MCC, and Wilson confidence intervals. It does not
derive those values from incomplete counts.

## Verified literature and citations

PaperForge uses:

- [OpenAlex Works API](https://developers.openalex.org/api-reference/works) for indexed scholarly
  discovery and available abstracts;
- [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/) for
  optional DOI metadata cross-checking.

Search queries and bibliographic identifiers are sent to those services. Project evidence is sent
only to the configured language-model provider. OpenAlex may not expose an abstract for every
record; the literature matrix explicitly marks unavailable fields rather than guessing them.

During generation, citations use internal markers such as `[@REF001]`. Export converts them to
numbered IEEE-style citations and creates `references.bib`. Volume, issue, and page metadata are
included when available. Unknown or malformed markers are rejected. PaperForge 1.0 intentionally
rejects unsupported citation-style settings rather than claiming journal compliance it cannot render.

## Ollama Cloud or local Ollama

The default is direct Ollama Cloud:

```yaml
provider:
  active: ollama
  ollama:
    host: https://ollama.com
```

PaperForge runs locally only when the host is deliberately changed:

```yaml
host: http://localhost:11434
```

Ollama Cloud currently does not support JSON-schema structured outputs. PaperForge therefore uses a
schema-grounded prompt plus Pydantic validation and bounded repair attempts for Cloud responses.
Local Ollama can enable native structured output:

```yaml
structured_outputs: true
```

Check endpoint and model visibility:

```powershell
paperforge doctor projects\uav-fire-paper
```

Verify actual inference entitlement as well:

```powershell
paperforge doctor projects\uav-fire-paper --inference
```

Model visibility does not prove that a model is included in the Ollama account plan.

## Outputs

```text
project/
├── inputs/
├── sources/
├── data/
├── figures/
├── evidence/registry.json
├── literature/
│   ├── references.json
│   ├── search-manifest.json
│   └── synthesis.json
├── planning/
│   ├── research-plan.json
│   └── outline.json
├── manuscript/
│   ├── current.md
│   └── versions/
├── reviews/
├── audit/state.json
└── outputs/
    ├── manuscript.md
    ├── manuscript.docx
    ├── references.bib
    ├── literature-matrix.csv
    ├── quality-report.json
    ├── review-report.md
    └── revision-history.md
```

`submission_ready=true` means the implemented evidence, citation, structure, and consistency gates
passed. The author must still verify the manuscript, declarations, target-journal instructions,
authorship, ethics, and all scientific content before submission.

## Existing v0.2.1 projects

Update and reinstall, then rebuild generated stages:

```powershell
git pull
python -m pip install -e ".[documents]"
paperforge --version
paperforge run projects\uav-fire-paper --rebuild
```

Expected version:

```text
PaperForge 1.0.0
```

Migration is non-destructive:

- `paperforge.v2.yaml` preserves the old configuration;
- `audit/state.v2.json` preserves the old state;
- `manuscript/versions/legacy-v0.2.1.md` preserves the prior manuscript;
- `inputs/responses.yaml` remains untouched and is ingested as user evidence.

See [docs/MIGRATION.md](docs/MIGRATION.md) for details.

## Operational commands

```powershell
paperforge status projects\uav-fire-paper
paperforge validate projects\uav-fire-paper
paperforge export projects\uav-fire-paper
paperforge run projects\uav-fire-paper --rebuild
```

## Development

```powershell
python -m pip install -e ".[dev,documents]"
python -m ruff check .
python -m ruff format --check .
python -m pytest
python -m build
```

The test suite includes the UAV regression that previously imported five answers and then failed on
calibration, uncertainty, reproducibility, baseline, Related Work, regulation, and simulation. It
verifies that only real integrity defects block the rebuilt workflow.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the state machine and trust boundaries.
