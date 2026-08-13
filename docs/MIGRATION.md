# Migrating to PaperForge 2.0

PaperForge 2.0 replaces the v1 whole-manuscript repair behavior with a publication-contract,
claim-ledger, pre-draft evidence gate, structured section drafting, and targeted revision workflow.

## 1. Preserve an external backup

Migration is non-destructive, but keep an external copy of an active research project before any
software upgrade.

## 2. Update and reinstall

```powershell
git pull
.venv\Scripts\Activate.ps1
python -m pip install -e ".[documents]"
paperforge --version
```

Expected:

```text
PaperForge 2.0.0
```

## 3. Rebuild generated stages

```powershell
paperforge run projects\uav-fire-paper --rebuild
```

Do not run `paperforge init` against an existing project.

## Automatic preservation

From a PaperForge 1.0/schema-3 project:

| Existing artifact | Preserved copy |
| --- | --- |
| `paperforge.yaml` | `paperforge.v3.yaml` |
| `audit/state.json` | `audit/state.v3.json` |
| `manuscript/current.md` | `manuscript/versions/legacy-v1.0.0.md` |
| response/input files | Original files remain unchanged |

From a v0.2/schema-2 project, the equivalent backups use `paperforge.v2.yaml`,
`audit/state.v2.json`, and `manuscript/versions/legacy-v0.2.1.md`.

The migrated configuration keeps provider endpoints, environment-variable names, custom model
choices, and journal settings. Values that exactly match old defaults are upgraded to the stronger
v2 source, length, and workflow defaults; explicit custom values are preserved.

## Saved responses

No interactive `answer` command is required. Non-empty values under `answers:` in
`inputs/responses.yaml` are imported as author evidence. Compatibility spellings including the
existing `respones.yml` file are also recognized.

The five original UAV answers establish that an original experiment exists, but they do not fully
specify the algorithm parameters, acquisition protocol, annotation protocol, evaluation independence,
raw outcome counts/uncertainty, or calibration. PaperForge 2.0 therefore stops at `evidence_mapping`
before drafting and writes exact prompts to `author-actions/evidence-required.md`. Add authentic
answers to the existing response YAML and rerun; never fill unavailable facts by estimation.

## Changed behavior

- The 17-stage pipeline begins with a checked publication profile and evidence coverage.
- A topic-only project becomes a review article rather than a fictional experiment.
- An incomplete original study stops before literature/drafting instead of producing a shallow draft.
- Source appraisal and discussion review are independent stages.
- The outline locks one authoritative major-section sequence.
- Draft and revision responses are structured section objects.
- Revisions replace only validated target sections; unaffected text is preserved byte-for-byte.
- Citations are canonicalized only when unambiguous, and `REF` suffixes are never read as numbers.
- Numeric claims require exact study evidence or exact numeric content in the cited source record.
- Funding, conflicts, authorship, availability, permissions, and AI-use statements require author
  evidence.
- Unknown target-journal rules prevent a false submission-ready result.
- DOCX section/reference numbering and the audit/submission output package are deterministic.

## Recovery

For model/network failure:

```powershell
paperforge doctor projects\uav-fire-paper --inference
paperforge run projects\uav-fire-paper
```

Completed stages resume. If an input or configuration changed substantially, use `--rebuild`; prior
manuscript versions and all user-controlled files remain preserved.
