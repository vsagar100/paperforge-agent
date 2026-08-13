# PaperForge Agent 2.0

PaperForge turns one topic or synopsis into an evidence-constrained manuscript through a resumable,
multi-stage publication workflow. It searches and appraises literature, drafts exact journal
sections, reviews them from several scientific perspectives, and applies only validated targeted
revisions.

PaperForge does not guarantee acceptance, replace missing experiments, certify originality, or
turn an indexing label into a manuscript standard. It must not invent data, methods, citations,
equipment, parameters, approvals, declarations, or results.

## Install and run

From the repository root on Windows PowerShell:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[documents]"
```

Copy `.env.example` to `.env` and set the configured Ollama credential:

```dotenv
OLLAMA_API_KEY=your_key_only
PAPERFORGE_CONTACT_EMAIL=you@example.com
```

Start from a topic:

```powershell
paperforge write "Thermal UAV fire detection using edge-cloud intelligence" `
  --project projects\uav-fire-paper
```

Or supply a complete synopsis and target journal:

```powershell
paperforge write --from-file synopsis.md `
  --project projects\uav-fire-paper `
  --journal "DJES"
```

Subsequent runs resume completed stages:

```powershell
paperforge run projects\uav-fire-paper
```

## What happens with limited input

`--paper-type auto` is the default.

- A topic without authentic study methods and results becomes a review article.
- A synopsis or project evidence containing original methods and results becomes an original
  research candidate.
- An original study that lacks reproducibility-critical facts stops **before drafting**. PaperForge
  writes `author-actions/evidence-required.md` with exact missing details, preserves all inputs, and
  waits for author evidence.
- An unsupported request for `original_research` falls back to a review article by default instead
  of manufacturing an experiment.

Save each author response once under `answers:` in `inputs/responses.yaml` and rerun. The aliases
`responses.yml`, `respones.yaml`, and `respones.yml` are also imported for compatibility, but
`responses.yaml` is recommended.

For an original engineering experiment, the pre-draft gate checks at least:

- system/material specifications and the configuration actually tested;
- exact algorithm and decision parameters;
- acquisition design, independent runs/sites, and leakage controls;
- ground-truth definitions, annotators, and adjudication;
- evaluation independence and parameter-selection procedure;
- raw outcomes, denominators, and uncertainty support;
- calibration or measurement-validity procedures when relevant;
- primary results and their exact evaluation boundary.

Submission declarations, repository availability, permissions, and recommended comparisons are
tracked separately. PaperForge never fills them by assumption.

## Publication workflow

| Stage | Contract |
| --- | --- |
| Prepare | Extract files, preserve provenance, and derive only supported statistics |
| Journal profile | Resolve article type and a checked publication contract |
| Evidence mapping | Build an exact claim ledger and block an indefensible original draft |
| Plan | Define the question, objectives, scope, contribution, and search strategy |
| Literature | Search OpenAlex, deduplicate records, and verify DOI metadata where possible |
| Source appraisal | Record verification, abstract coverage, recency, diversity, and retractions |
| Synthesis | Build source-level notes, themes, gap, and bounded novelty position |
| Outline | Lock one ordered section set with evidence, claim, reference, and display-item links |
| Draft | Generate schema-validated sections in bounded batches |
| Evidence review | Check citations, numeric provenance, literature grounding, and source coverage |
| Methodology review | Check reproducibility, validity boundaries, and honest disclosure |
| Results review | Check dataset accounting, denominators, uncertainty, tables, and consistency |
| Discussion review | Check interpretation, comparison, generalisability, limitations, and implications |
| Writing review | Check depth, cohesion, terminology, structure, and internal markers |
| Journal review | Check the active profile, abstract, keywords, declarations, and format contract |
| Final review | Re-run deterministic gates plus an independent scope-aware audit |
| Export | Produce the manuscript and complete audit/submission package |

Drafting and revision use structured section objects. PaperForge owns the title and level-2 heading
assembly, so a model cannot inject duplicate major sections or a second manuscript. Revision is
targeted: only named sections may be returned, all unaffected sections remain byte-for-byte
unchanged, and an unsafe revision is rejected without replacing the prior version.

## Publication standards and indexing claims

Scopus and Web of Science/SCIE evaluate journals and editorial practice; neither is a universal
manuscript-acceptance checklist. PaperForge therefore applies:

1. current, journal-specific author instructions when a supported profile exists;
2. reproducibility and research-integrity gates;
3. peer-review dimensions such as contribution, literature coverage, method soundness, results,
   discussion, clarity, references, and declarations.

Version 2.0 contains a checked DJES research/review profile. An unknown journal uses a conservative
engineering profile and remains `author_action_required` until its live instructions and submission
files are verified. See [Publication Standard](docs/PUBLICATION_STANDARD.md) for the official
sources, checked rules, and the comparable UAV/thermal-paper benchmark used in the redesign.

## Evidence and provenance

PaperForge scans these folders on each non-resumed build:

| Folder | Formats |
| --- | --- |
| `inputs/` | Markdown, text, YAML, and saved response YAML |
| `sources/` | PDF, DOCX, TXT, Markdown, BibTeX, RIS, and JSON |
| `data/` | CSV, TSV, XLSX, JSON, and experiment notes |
| `figures/` | PNG, JPEG, TIFF, SVG, and captions |

Each extracted item receives a stable evidence ID, checksum, source path, and locator. Original-study
statements are split into stable claim atoms used to constrain section drafting. Images are
registered, but their pixels are not interpreted automatically.

When authentic TP, TN, FP, and FN counts are present, PaperForge deterministically derives accuracy,
precision, recall, specificity, F1, FPR, FNR, MCC, and Wilson confidence intervals. It does not
derive those values from percentages or incomplete counts.

## Literature and citations

PaperForge uses the [OpenAlex Works API](https://developers.openalex.org/api-reference/works) for
scholarly discovery and the [Crossref REST API](https://www.crossref.org/documentation/retrieve-metadata/rest-api/)
for optional DOI metadata checks. Queries and bibliographic identifiers are sent to those services;
project evidence is sent only to the configured language-model provider.

Models may cite only catalogue IDs such as `[@REF001]`. Unambiguous comma-separated model output is
normalized, but descriptive or malformed markers remain a hard defect. Export numbers citations by
first appearance and builds both the reference list and `references.bib`. A numeric claim from a
cited paper is allowed only when that exact number occurs in the cited record's available title,
metadata, or abstract; a citation cannot legitimize an invented study result.

## Outputs

```text
project/
├── author-actions/evidence-required.md
├── evidence/
│   ├── registry.json
│   ├── claim-ledger.json
│   └── coverage.json
├── literature/
│   ├── references.json
│   ├── search-manifest.json
│   ├── source-appraisal.json
│   └── synthesis.json
├── planning/
│   ├── publication-profile.json
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
    ├── publication-profile.md
    ├── evidence-coverage.md
    ├── display-item-plan.md
    ├── submission-checklist.md
    ├── quality-report.json
    ├── review-report.md
    └── revision-history.md
```

The DOCX export uses A4 pages, numbered sections when required, editable tables, and literal
hanging-indent references so bibliography numbering always restarts at 1. Figure source files,
title-page metadata, authorship, permissions, declarations, and the journal's current template still
require author confirmation.

`submission_ready=true` means the implemented evidence, citation, publication-contract, structure,
and consistency gates passed. It is a submission candidate—not a guarantee of peer-review outcome.

## Ollama Cloud or local Ollama

The default endpoint is Ollama Cloud:

```yaml
provider:
  active: ollama
  ollama:
    host: https://ollama.com
```

Use local Ollama only by deliberately changing the host to `http://localhost:11434`. Cloud responses
use schema-grounded prompts plus Pydantic validation and bounded repair attempts. Local Ollama may
enable native structured output with `structured_outputs: true`.

Check connectivity and actual inference entitlement:

```powershell
paperforge doctor projects\uav-fire-paper
paperforge doctor projects\uav-fire-paper --inference
```

## Upgrade an existing project

```powershell
git pull
python -m pip install -e ".[documents]"
paperforge --version
paperforge run projects\uav-fire-paper --rebuild
```

Expected version:

```text
PaperForge 2.0.0
```

Migration is non-destructive: legacy configuration/state files and the prior generated manuscript
are preserved, while user inputs and response YAML remain untouched. See
[Migration](docs/MIGRATION.md) for exact backup names and behavior.

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

The regression suite includes the supplied five-answer UAV case, malformed citation variants,
fabricated declarations, duplicate sections, unsupported numeric claims, section injection,
revision preservation, migration, export numbering, and a complete-protocol pre-draft pass case.

See [Architecture](docs/ARCHITECTURE.md) for the state machine and trust boundaries.
