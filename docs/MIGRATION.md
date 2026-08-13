# Migrating from PaperForge 0.2.1 to 1.0

Version 1.0 replaces the old question/patch workflow with a new state model and complete
plan-literature-draft-review-revise pipeline.

## 1. Back up the working directory

The migration is designed to be non-destructive, but preserve an external copy of the project before
changing application versions.

## 2. Update the repository and environment

```powershell
git pull
.venv\Scripts\Activate.ps1
python -m pip install -e ".[documents]"
paperforge --version
```

Expected:

```text
PaperForge 1.0.0
```

## 3. Rebuild generated stages

```powershell
paperforge run projects\uav-fire-paper --rebuild
```

Do not run `paperforge init` for an existing project.

## Automatic preservation

On first access, PaperForge preserves:

| Previous artifact | Preserved copy |
| --- | --- |
| `paperforge.yaml` | `paperforge.v2.yaml` |
| `audit/state.json` | `audit/state.v2.json` |
| `manuscript/current.md` | `manuscript/versions/legacy-v0.2.1.md` |
| `inputs/responses.yaml` | Original file remains unchanged |

The new `paperforge.yaml` keeps the Ollama host, key environment name, legacy model choices mapped to
the new logical roles, and journal settings. New settings receive v1 defaults.

## Model-role mapping

| v0.2 role | v1 role |
| --- | --- |
| `drafting` | `planner` and `drafter` |
| `enhancement` | `reviser` |
| `scientific_review` | `reviewer` |
| `final_audit` | `final_auditor` |

## Saved responses

No `answer` or `answer-all` command is required in v1. All non-empty values in the existing
`inputs/responses.yaml` file are extracted during `prepare` and registered as verified user facts.

For the UAV project, the five responses covering hardware, dataset, results, validation, and
deployment constraints are sufficient for the policy to select `original_research`.

## Changed behavior

- There is no model-generated question round.
- A topic without original evidence becomes a review article.
- Literature discovery and verified citation management are built into the workflow.
- Drafting occurs section-by-section.
- Review stages must revise and recheck the manuscript.
- A model score cannot fail the paper by itself.
- Missing simulation, regulation, or external baselines are not blockers unless explicitly in scope.
- Existing inputs trigger a rebuild only when their fingerprint changes.

## Recovery

If a run stops because of provider or network access:

```powershell
paperforge doctor projects\uav-fire-paper --inference
paperforge run projects\uav-fire-paper
```

Completed stages resume without another model call.

If the intended inputs changed substantially:

```powershell
paperforge run projects\uav-fire-paper --rebuild
```

This preserves user files and prior manuscript versions while regenerating downstream artifacts.
