# Upgrade from PaperForge 0.1.0 or 0.2.0 to 0.2.1

## 1. Back up the existing project

```powershell
Copy-Item projects\uav-fire-paper projects\uav-fire-paper-backup -Recurse
```

Keep the existing `.env` outside the archive replacement process.

## 2. Replace the application code

Extract the complete 0.2.1 archive into a new application directory. Copy `.env` into that directory,
or recreate it with only `OLLAMA_API_KEY=<key>`.

Do not copy a new `paperforge.yaml` over the one inside an existing research project. Its journal and
model choices are project-specific.

## 3. Reinstall

```powershell
.venv\Scripts\Activate.ps1
pip install -e ".[dev,documents]"
paperforge --version
```

Expected version: `PaperForge 0.2.1`.

## 4. Verify provider configuration

```powershell
paperforge doctor projects\uav-fire-paper
```

An Ollama model appearing in the list does not prove that the current plan can run it. A 403 response
now reports subscription or entitlement information directly and is not retried.

## 5. Trigger automatic state migration

```powershell
paperforge status projects\uav-fire-paper
```

Loading status upgrades schema-1 state safely. Existing answers remain in `audit/state.json`. The old
deadlock—`intake: needs_input` with no open question—is repaired automatically.

## 6. Resume

```powershell
paperforge run projects\uav-fire-paper
```

If up to five questions are still open, complete and save the generated response file once, then
rerun normally:

```powershell
paperforge run projects\uav-fire-paper
```

PaperForge 0.2.1 automatically imports the file, never erases a non-empty response, and will not
generate a second intake batch. `paperforge answer-all projects\uav-fire-paper` is retained as an
optional explicit import command.
