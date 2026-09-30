# PaperForge Agent 3

A fresh Python implementation of a staged, resumable manuscript workflow. It develops a
topic, accessible literature and optional author evidence into a manuscript review packet.
Accepted writing survives failures, and author decisions explicitly continue the workflow.
The [supplied manuscript prompt](docs/MANUSCRIPT_REQUIREMENTS.md) is retained for reference.
The earlier implementation is replaced; old projects are not migrated automatically.

## Install and start

Python 3.11+:

~~~bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[documents,scientific]'
ollama pull gpt-oss:20b
# Start Ollama separately at http://localhost:11434.
paperforge init projects/thermal --topic "Robust thermal monitoring of electrical equipment"
paperforge doctor projects/thermal
paperforge run projects/thermal --until plan
paperforge status projects/thermal
paperforge resume projects/thermal
~~~

Use python -m paperforge if the executable is not on PATH. Ollama is external: the app does
not install/start it or download models. Choose a smaller local model for limited hardware.
There is no claim that one model is universally best.

Defaults: local inference, free_first, ₹500 cap, five accessible sources minimum,
35 discovery candidates, five-year recency window and two automatic review revisions.
Five distinct supporting citations are required. Discovery targets are not a guarantee of
relevance or citation count. QC flags a recent-citation fraction below the prompt's 70–80% target.

## Provider, model and cost choices

Adapters support Ollama, Gemini, Anthropic, OpenAI, Groq, OpenRouter, DeepSeek, Mistral
and custom compatible HTTPS endpoints. [Configuration details](docs/MODELS.md).

~~~bash
# Confirm hosted account free entitlement before choosing billing free.
paperforge model projects/thermal google gemini --billing free --select
paperforge policy projects/thermal --mode free_only --budget-inr 0
paperforge route projects/thermal local google --role writer
paperforge route projects/thermal google local --stage review
~~~

Put keys in projects/thermal/.env or exported variables. Keys never enter YAML/call records.
Provider and complete native model/snapshot IDs can change globally, by role or by stage.
Fallback uses only listed routes. Hosted unknown billing routes are excluded; paid routes
require current INR-per-million input/output ceiling rates. Changing routes/budget preserves
accepted work; paperforge retry projects/thermal draft deliberately regenerates writing.

## Inputs and topic-only work

~~~bash
paperforge attach projects/thermal notes.txt --role author_note
paperforge attach projects/thermal measurements.xlsx --role dataset
paperforge attach projects/thermal paper.pdf --role source
paperforge attach projects/thermal diagram.cdr --role figure
~~~

Text, CSV, JSON, XLSX, text PDFs, DOCX, PPTX, images, SVG and numeric MAT/NPY have adapters.
Hashes, locations and omissions are recorded. Images yield metadata and optional OCR,
not scientific visual interpretation. Native CDR, Simulink, MATLAB live scripts/FIG, DWG and
legacy Office inputs are retained with explicit conversion requests. See [file handling](docs/INPUTS.md).

Topic-only projects search scholarly metadata/abstracts, plan the research and discover
candidate datasets where appropriate. auto selects review without empirical inputs.
Initialize with --paper-type original_research to preserve that structure with missing-result
placeholders. The app does not automatically validate/download datasets, train arbitrary
models, run MATLAB/CorelDRAW, execute uploaded code or perform physical experiments.
Only documented built-in analysis runs on genuine author-supplied observations.

## Recovery and author continuation

~~~bash
# Correct network/credentials/configuration/disk issues, then:
paperforge resume projects/thermal
paperforge decide projects/thermal continue --note "Use the attached protocol." --continue-run
paperforge decide projects/thermal revise --stage draft --note "Correct the research scope."
paperforge resume projects/thermal
paperforge decide projects/thermal defer
paperforge decide projects/thermal continue --continue-run
paperforge decide projects/thermal cancel
~~~

Failed stages retry on run/resume. Completed stages and accepted sections survive.
continue/revise invalidates selected dependents and preserves audit history.
Approval cannot bypass evidence checks. Changed input bytes trigger dependent revalidation.
Cancelled projects cannot resume. See [architecture and recovery](docs/ARCHITECTURE.md).

## Outputs and limits

The project's outputs contains manuscript.md, optional manuscript.docx, source/claim registers,
literature matrix, computed-results CSV, proposed SVG diagrams and quality-control.json.
audit retains prior versions. Blocked drafts export accepted portions and outstanding actions.

IEEE-style references are assembled in first-appearance order from retrieved bibliographic
metadata. Exact support excerpts and paragraph provenance remain in separate registers.
Source existence and quote matching do not prove entailment; model review and author
verification are required. Every packet has submission_ready=false.

DOCX body text uses Times New Roman 12 pt. Tables/diagrams are separate assets for author
integration; complex equation typesetting, journal templates, visual review and final table/
figure numbering are not certified. This is an author-review packet, not automatic journal submission.

## Developer checks

~~~bash
python -m pip install -e '.[dev,documents,scientific]'
python -m ruff check .
python -m ruff format --check .
python -m pytest --cov=paperforge --cov-report=term-missing
python -m build
~~~

Tests use deterministic fixtures, mocked HTTP and injected failures. They do not certify
live provider availability or manuscript quality. Store, Settings and Workflow are usable
Python interfaces; this release offers a CLI, not a hosted web UI.

Docker: docker compose run --rm paperforge --help. Projects mount under /projects.
Give the container user write access to the host directory. localhost inside Docker refers
to that container; configure an accessible endpoint or shared network namespace for Ollama.
