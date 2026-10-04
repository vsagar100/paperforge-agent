# End-to-end paper writing in Windows PowerShell

This guide uses PowerShell syntax, including quoted Windows paths. Commands run from the
repository root. Keys are saved once in .env; models/routes are saved in each project's
paperforge.yaml. Neither needs to be supplied for every run.

## 1. Update and activate

For your existing checkout:

~~~powershell
Set-Location 'D:\Sagar\Study\acad proj\paper_writing\PaperForge-Agent'
git switch feat/enhanced_writing
git pull --ff-only origin feat/enhanced_writing

# Only create .venv if it does not already exist:
if (-not (Test-Path .\.venv\Scripts\python.exe)) { python -m venv .venv }
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[documents,scientific]"
paperforge version
~~~

If PowerShell blocks Activate.ps1, use the process-only setting below **if your organization's
policy permits it**, then activate again. It ends when this PowerShell window closes. If a
managed policy blocks it, use the virtual-environment interpreter directly instead.

~~~powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\.venv\Scripts\Activate.ps1
# Alternative requiring no activation:
.\.venv\Scripts\python.exe -m paperforge --help
~~~

## 2. Save keys once

Any key shared in chat/logs should be revoked and replaced in its provider console.
Do not paste the replacement in chat or commit it.

Create a shared key file without overwriting an existing one:

~~~powershell
if (-not (Test-Path .\.env)) { Copy-Item .\.env.example .\.env }
notepad .\.env
~~~

Edit only the providers you intend to use, for example:

~~~dotenv
GEMINI_API_KEY="YOUR_REPLACEMENT_GEMINI_KEY"
GROQ_API_KEY="YOUR_GROQ_KEY"
OPENAI_API_KEY="YOUR_OPENAI_KEY"
~~~

Unused keys can stay blank. Do not copy PowerShell's $env: prefix or the word Bearer into
this file. Save as UTF-8; a Windows UTF-8 BOM is supported. Values containing # or spaces
should be quoted. Dotenv interpolation is disabled: values are literal.

| Provider configured in PaperForge | Default key name in .env | Obtain/manage key |
|---|---|---|
| gemini | GEMINI_API_KEY | [Google AI Studio](https://aistudio.google.com/api-keys) |
| groq | GROQ_API_KEY | [Groq Console](https://console.groq.com/keys) |
| openai | OPENAI_API_KEY | [OpenAI platform](https://platform.openai.com/api-keys) |
| anthropic | ANTHROPIC_API_KEY | [Anthropic Console](https://console.anthropic.com/settings/keys) |
| openrouter | OPENROUTER_API_KEY | [OpenRouter](https://openrouter.ai/settings/keys) |
| deepseek | DEEPSEEK_API_KEY | [DeepSeek platform](https://platform.deepseek.com/api_keys) |
| mistral | MISTRAL_API_KEY | [Mistral Console](https://console.mistral.ai/) |
| ollama | OLLAMA_API_KEY | Not needed for your local Ollama server; hosted access differs |
| compatible | LLM_API_KEY | Your compatible server's provider |

OPENALEX_API_KEY and PAPERFORGE_CONTACT_EMAIL are optional scholarly discovery settings,
not LLM keys. Each provider has its own key/account; a Gemini key cannot authenticate Groq.
API billing is separate from a browser chat subscription.

**Automatic loading:** each project command and the Workflow Python API reads saved files.
Priority is nonblank process environment > PROJECT/.env > repository .env.
Blank placeholders do not override shared keys. doctor prints file paths, never key values.
.env files and projects are git-ignored; keep them local.

For project-specific keys, create projects/thermal/.env after initialization. Otherwise
the shared root .env is enough for all projects in this checkout.

If you already set a stale key with $env:GEMINI_API_KEY, clear it so saved files can win:

~~~powershell
Remove-Item Env:GEMINI_API_KEY -ErrorAction SilentlyContinue
~~~

Editing a file does not replace an already-set process value automatically. Remove that value,
restart PowerShell, or use -ReloadEnv in the next section.

## 3. One startup command in each new PowerShell window

The application loads .env even without this helper. To also activate .venv and populate
the PowerShell session, **dot-source** the script (the leading dot and space matter):

~~~powershell
. .\scripts\Enter-PaperForge.ps1
# After creating/selecting a project:
. .\scripts\Enter-PaperForge.ps1 -Project projects/thermal
# After editing/rotating saved keys, explicitly refresh file-defined session values:
. .\scripts\Enter-PaperForge.ps1 -Project projects/thermal -ReloadEnv
~~~

The helper preserves existing process values by default. -ReloadEnv explicitly replaces
nonblank file-defined values; it does not clear unrelated variables. Activating a normal
Python .venv alone does not load .env. Bash's source command is not used in PowerShell.
Do not add secret values to your PowerShell profile or modify generated Activate.ps1.

## 4. Create or reuse a manuscript project

Your existing projects/thermal already has saved progress: **do not initialize it again**.
For a new paper, choose a new directory and initialize once:

~~~powershell
paperforge init projects/thermal --topic "Robust thermal monitoring of electrical equipment" --domain engineering --paper-type original_research
~~~

Use original_research when you want experimental-study sections with explicit missing-data
placeholders. Use review for a literature review, or auto to select review if empirical inputs
are absent. A topic alone does not create performed experiments or authentic measured results.

## 5. Fix Gemini 404 and verify the actual connection

The previous Gemini default, gemini-2.5-flash, has restricted access for new accounts.
Google recommends current models for new projects. New PaperForge Gemini routes default
to gemini-3.5-flash-lite, a low-cost starting point; existing YAML is never silently migrated.
Source: [Google model/access documentation](https://ai.google.dev/gemini-api/docs/models).

Update your existing route explicitly:

~~~powershell
paperforge model projects/thermal google gemini --model gemini-3.5-flash-lite --billing free --select
paperforge policy projects/thermal --mode free_only --budget-inr 0
paperforge doctor projects/thermal
paperforge models projects/thermal google
paperforge probe projects/thermal google
~~~

Choose billing free only if the account/model actually has free entitlement. The application
cannot make paid provider requests free by labeling them. Check account quotas and
[Google's current pricing](https://ai.google.dev/gemini-api/docs/pricing).

models requests metadata without generating text, shows normalized IDs and supported
methods, and does not modify your route. Select a text model supporting generateContent.
**Listing alone is insufficient:** a model can appear in metadata while generation returns 404.
probe makes exactly one new generation request using the saved key and billing/cap policy;
no automatic retries/fallback and no workflow stage changes. Paid probes can incur a small cost.
doctor's eligible=true means credential/policy eligibility, not tested generation availability.

If a model returns 404: verify the route's saved base_url/version, choose an accessible
model ID, and probe again. If the ID came from models/... output, PaperForge normalizes
the prefix. If 401/403 occurs, check replacement key/API permissions. For 429, check provider
quota/rate limits before resuming; --billing free does not bypass them.

After a successful probe, recover your existing failed run:

~~~powershell
paperforge resume projects/thermal --until plan
~~~

Do not delete the project or audit history to fix a provider failure.

## 6. Attach research information

Useful inputs: problem/objectives/proposed method in a text file; dataset and observed
results; actual test conditions; parameters/environment; baseline results; accessible sources;
figures/captions; journal instructions; declarations. Supply only real observations as results.

~~~powershell
paperforge attach projects/thermal 'D:\Research Inputs\protocol.txt' --role author_note
paperforge attach projects/thermal 'D:\Research Inputs\measurements.xlsx' --role dataset
paperforge attach projects/thermal 'D:\Research Inputs\study.pdf' --role source
paperforge attach projects/thermal 'D:\Research Inputs\architecture.svg' --role figure
paperforge attach projects/thermal 'D:\Research Inputs\simulation.m' --role code
~~~

These paths are examples: replace them with files that exist. No attachment overwrites an
already-attached filename. Edit the retained original intentionally, or attach a renamed file.
Changed inputs invalidate dependent stages on resume, preserving prior audit versions.

To associate an attached paper with its own real DOI, edit inputs/source-dois.json:

~~~powershell
notepad .\projects\thermal\inputs\source-dois.json
~~~

~~~json
{"study.pdf": "10.xxxx/actual-registered-doi"}
~~~

The example DOI is a placeholder; use the paper's real registered DOI. Confirm that the
attached paper matches it. Native CorelDRAW, MATLAB FIG/SLX/MLX and similar formats
require supported exports; uploaded code is read, not executed. Images yield metadata/optional
OCR, not complete scientific image understanding. See [input support](INPUTS.md).

## 7. Run the writing stages and inspect progress

~~~powershell
paperforge run projects/thermal --until intake
paperforge resume projects/thermal --until literature
paperforge resume projects/thermal --until plan
paperforge status projects/thermal
paperforge resume projects/thermal --until analysis
paperforge resume projects/thermal --until draft
paperforge resume projects/thermal --until review
paperforge resume projects/thermal
~~~

Alternatively paperforge run projects/thermal runs until completion or an author blocker.
--until is a stop point, not a request to regenerate completed stages. The pipeline is:
intake → literature → plan → analysis → draft → review → export.
Review revises within a configured limit; missing real evidence pauses for the author.

The plan and reviewer issues are retained in workflow.sqlite3 and timestamped audit/stages
JSON files. Open the newest relevant file to inspect them:

~~~powershell
Get-ChildItem .\projects\thermal\audit\stages\plan-*.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | ForEach-Object { notepad $_.FullName }
Get-ChildItem .\projects\thermal\audit\stages\review-*.json | Sort-Object LastWriteTime -Descending | Select-Object -First 1 | ForEach-Object { notepad $_.FullName }
~~~

analysis runs only built-in deterministic calculations (currently genuine confusion-matrix
counts) and search inventory. Dataset candidates are not automatically downloaded/validated;
native simulations/training/physical experiments remain author/runtime work.

## 8. Continue, revise, defer or recover

After network/key/model/disk failures, correct the cause and resume. Accepted work is reused:

~~~powershell
paperforge resume projects/thermal
~~~

If literature fails with a source appraisal quote error, update the enhanced-writing branch
and resume; evidence validation now participates in the bounded repair loop:

~~~powershell
git pull --ff-only origin feat/enhanced_writing
python -m pip install -e ".[documents,scientific]"
paperforge resume projects/thermal --until literature
paperforge resume projects/thermal
~~~

Appraisals require character-for-character quotes from the retrieved text. A repair must
copy a real quote or mark an unsupported fact null; the workflow does not accept invented
evidence. The project's max_schema_repairs setting bounds the shared schema/evidence repair
loop (default 1, maximum 3). If all repairs fail, the error identifies the source and fields.
Select an accessible alternative extractor model, then resume. Repeating an unchanged
exhausted request can reuse its cached failed validation; changing the model changes the
request cache key. Accepted source appraisals remain checkpointed. Supplying additional
source text changes inputs and triggers scientific revalidation.

At awaiting_author, provide the required genuine evidence or change the scope:

~~~powershell
paperforge decide projects/thermal continue --note "Use the attached validated protocol." --continue-run
paperforge decide projects/thermal revise --stage draft --note "Narrow the claims to the observed test conditions."
paperforge resume projects/thermal
~~~

Notes become author inputs and trigger dependent revalidation; decisions do not override
scientific integrity checks. To continue without introducing a new note, omit --note.

~~~powershell
paperforge decide projects/thermal defer
paperforge decide projects/thermal continue --continue-run
# Terminal action; use only when abandoning this project:
paperforge decide projects/thermal cancel
~~~

To explicitly regenerate accepted writing under a newly selected model:

~~~powershell
paperforge retry projects/thermal draft
paperforge resume projects/thermal
~~~

This invalidates draft/review/export and keeps earlier audit history. To regenerate just the
review use retry projects/thermal review. Never manually delete the database/WAL during a run.

## 9. Switch model/provider and keep the choice

Route names such as google, local and paid-review are your labels; provider names identify
the API. model writes YAML once. --select sets the default route; existing stage/role overrides
still take precedence. doctor prints all overrides so a switch is not hidden.

~~~powershell
# Same provider, another model (confirm account access and billing first):
paperforge model projects/thermal google gemini --model gemini-3.8-flash --billing free --select

# Another provider with its own saved GROQ_API_KEY:
paperforge model projects/thermal fast groq --model openai/gpt-oss-120b --billing free --select
paperforge probe projects/thermal fast

# Local alternative, no API key; install/start Ollama separately:
ollama pull gpt-oss:20b
paperforge model projects/thermal local ollama --model gpt-oss:20b --select

# Explicit writer/reviewer/fallback choices:
paperforge route projects/thermal google --role writer
paperforge route projects/thermal fast --stage review
paperforge route projects/thermal google local
~~~

Stage overrides > role overrides > default. free_first reorders listed eligible routes so
free precedes paid. free_only never invokes paid routes. To switch everything to one provider,
set its default and clear obsolete stage_routes/role_routes in paperforge.yaml:

~~~powershell
notepad .\projects\thermal\paperforge.yaml
~~~

~~~yaml
default_routes: [google]
stage_routes: {}
role_routes: {}
~~~

Other settings remain in that file. Do not replace the whole file with this three-line excerpt.
An API key existing in .env does not select its provider automatically.

For a snapshot, --model-version is the complete provider-native ID, not a suffix/date that
PaperForge guesses. Example pattern (replace BOTH placeholders with supported IDs):

~~~powershell
paperforge model projects/thermal pinned openai --model YOUR_BASE_ID --model-version YOUR_COMPLETE_SNAPSHOT_ID --billing paid --input-rate YOUR_INPUT_CEILING --output-rate YOUR_OUTPUT_CEILING
~~~

For custom key names use --api-key-env NAME_FROM_ENV_FILE; compatible servers also require
--base-url https://your-compatible-server/v1 and an explicit --model.

## 10. Paid routes and budgets

Paid input/output ceilings are **INR per million tokens**. Enter current provider prices,
converted conservatively to INR; do not use made-up example rates. Saved settings persist:

~~~powershell
$inputCeiling = [double](Read-Host 'Current input ceiling in INR per million tokens')
$outputCeiling = [double](Read-Host 'Current output ceiling in INR per million tokens')
$modelId = Read-Host 'Exact supported OpenAI model ID'
paperforge model projects/thermal paid-review openai --model $modelId --billing paid --input-rate $inputCeiling --output-rate $outputCeiling
paperforge route projects/thermal google paid-review --stage review
paperforge policy projects/thermal --mode free_first --budget-inr 500
~~~

Reservations before each request prevent the next estimated paid request from exceeding the
configured cap. Uncertain timeouts retain reservations, since the provider might have charged.
Provider-side limits remain the authority; pricing/taxes/quota can differ. Switching to
free_only with budget 0 still allows zero-cost requests after historical paid spend.

## 11. Open and verify the packet

~~~powershell
Invoke-Item .\projects\thermal\outputs
notepad .\projects\thermal\outputs\manuscript.md
notepad .\projects\thermal\outputs\quality-control.json
Invoke-Item .\projects\thermal\outputs\manuscript.docx
~~~

DOCX requires the documents extra. Other outputs include source/claim registers, literature
matrix, genuine computed results and proposed SVG diagrams. Author actions list missing
evidence and verification. Citation/source existence is not claim entailment. Tables/figures
and complex equations need journal integration/technical review. Never assume the packet is
submission-ready just because the run completed.

## A normal session after initial setup

~~~powershell
Set-Location 'D:\Sagar\Study\acad proj\paper_writing\PaperForge-Agent'
. .\scripts\Enter-PaperForge.ps1 -Project projects/thermal
paperforge doctor projects/thermal
paperforge resume projects/thermal
~~~

No key assignment, provider-selection command or project initialization is repeated.
