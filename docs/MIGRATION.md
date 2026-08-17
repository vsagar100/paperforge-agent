# Migrating to PaperForge 2.1

PaperForge 2.1 keeps the publication-contract, claim-ledger, structured section drafting, and
targeted revision safeguards while replacing the default pre-draft interrogation loop with a
research-first, one-pass author-validation workflow.

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
PaperForge 2.1.0
```

## 3. Rebuild generated stages

```powershell
paperforge run projects\uav-fire-paper --rebuild
```

Do not run `paperforge init` against an existing project.

## Automatic preservation

From a PaperForge 2.0/schema-4 project:

| Existing artifact | Preserved copy |
| --- | --- |
| `paperforge.yaml` | `paperforge.v4.yaml` |
| `audit/state.json` | `audit/state.v4.json` |
| `manuscript/current.md` | `manuscript/versions/legacy-v2.0.0.md` |
| response/input files | Original files remain unchanged |

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
raw outcome counts/uncertainty, or calibration. PaperForge 2.1 records those gaps, continues through
literature research, drafting, review, and export, and writes one
`author-actions/validation.yaml` after synthesis.

Review that file once after the run. Change only `decision` and `answer`; use authentic records or
explicitly choose `not_available`/`not_applicable`. A pending generated file does not invalidate the
completed workflow. A later rerun imports only resolved author decisions and never imports its
literature context as study evidence.

## Changed behavior

- The 18-stage pipeline begins with a checked publication profile and evidence coverage.
- A topic-only project becomes a review article rather than a fictional experiment.
- An incomplete original study receives literature-informed reporting context and a bounded draft;
  missing author-only facts remain visible and keep readiness at `author_action_required`.
- One consolidated author-validation stage runs after synthesis, without model-generated interactive
  questions or repeated pre-draft stops.
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

To retain the PaperForge 2.0 stop-before-draft behavior deliberately:

```yaml
workflow:
  evidence_gap_mode: strict_pre_draft
```

## Recovery

For model/network failure:

```powershell
paperforge doctor projects\uav-fire-paper --inference
paperforge run projects\uav-fire-paper
```

Completed stages resume. If an input or configuration changed substantially, use `--rebuild`; prior
manuscript versions and all user-controlled files remain preserved.
