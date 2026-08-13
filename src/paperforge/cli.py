from __future__ import annotations

from importlib.resources import as_file, files
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv
from filelock import Timeout as FileLockTimeout
from rich.console import Console
from rich.table import Table

from paperforge import __version__
from paperforge.config import AppConfig, load_config
from paperforge.domain import ResearchProfile, Severity, StageStatus
from paperforge.exporters import OutputExporter
from paperforge.ingestion import DocumentIngestor
from paperforge.providers import MockProvider, OllamaProvider, ProviderError
from paperforge.providers.base import ModelProvider
from paperforge.stages import StageExecutor
from paperforge.storage import ProjectStore
from paperforge.validators import validate_engineering_manuscript, validate_manuscript_structure
from paperforge.workflow import WorkflowEngine

dotenv_path = find_dotenv(usecwd=True)
if dotenv_path:
    load_dotenv(dotenv_path)

app = typer.Typer(
    help="Evidence-first engineering research paper workflow agent.", no_args_is_help=True
)
console = Console()


def _default_config() -> Path:
    packaged = files("paperforge").joinpath("default.yaml")
    if packaged.is_file():
        with as_file(packaged) as path:
            return Path(path)
    return Path(__file__).resolve().parents[2] / "config" / "default.yaml"


def _create_provider(config: AppConfig, selected: str | None = None) -> ModelProvider:
    provider_name = selected or config.provider.active
    if provider_name == "mock":
        return MockProvider()
    if provider_name == "ollama":
        return OllamaProvider(config)
    raise ValueError("Provider must be 'ollama' or 'mock'.")


def _create_engine(
    store: ProjectStore, config: AppConfig, provider: ModelProvider
) -> WorkflowEngine:
    return WorkflowEngine(store, config, StageExecutor(store, config, provider))


@app.callback()
def main(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show the installed version and exit.",
        is_eager=True,
        callback=lambda value: _show_version(value),
    ),
) -> None:
    del version


@app.command()
def init(
    project: Path = typer.Argument(..., help="New project directory"),
    topic: str = typer.Option(..., prompt=True, help="Research topic or concise work description"),
    domain: str = typer.Option("engineering", help="Engineering discipline"),
    journal: str | None = typer.Option(None, help="Target journal, if known"),
) -> None:
    """Create a project from the minimum required information."""
    try:
        store = ProjectStore(project)
        profile = ResearchProfile(topic=topic, domain=domain, target_journal=journal)
        state = store.initialize(profile, _default_config())
    except (FileExistsError, OSError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"[green]Created[/green] {store.root} ({state.project_id})")
    console.print("Optionally add files to sources/, data/, and figures/, then run PaperForge.")


@app.command()
def run(
    project: Path = typer.Argument(..., exists=True, file_okay=False),
    provider: str | None = typer.Option(None, help="Override provider: ollama or mock"),
) -> None:
    """Resume automatically until completion or the single consolidated input gate."""
    store = ProjectStore(project)
    model_provider: ModelProvider | None = None
    try:
        config = load_config(store.config_path)
        model_provider = _create_provider(config, provider)
        engine = _create_engine(store, config, model_provider)
        results = engine.run()
    except (ProviderError, FileLockTimeout, OSError, ValueError, KeyError) as exc:
        _fail(str(exc))
    finally:
        if model_provider:
            model_provider.close()

    ingestion = engine.last_ingestion_report
    if engine.last_imported_answers:
        console.print(
            f"[green]Imported[/green] {engine.last_imported_answers} answer(s) from "
            f"{store.response_template_path}."
        )
    if ingestion.extracted or ingestion.warnings:
        console.print(
            f"Evidence refresh: {ingestion.extracted} extracted, "
            f"{ingestion.unchanged} unchanged, {ingestion.skipped} skipped."
        )
        for warning in ingestion.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")

    for result in results:
        console.print(f"{result.stage}: {result.status.value} (score={result.score:.2f})")

    state = store.load_state()
    open_questions = state.open_questions()
    if open_questions:
        _print_questions(store, open_questions)
        return
    if state.workflow_completed:
        readiness = (
            "submission-ready" if state.submission_ready else "completed with author actions"
        )
        console.print(f"[green]Workflow {readiness}.[/green]")
        for path in engine.last_export_report.files:
            console.print(f"  {path}")
        for warning in engine.last_export_report.warnings:
            console.print(f"[yellow]Warning:[/yellow] {warning}")
        return
    if results and results[-1].status == StageStatus.FAILED:
        console.print("[red]Stopped at a blocking quality defect.[/red]")
        for finding in results[-1].findings:
            if not finding.resolved:
                console.print(f"  {finding.id}: {finding.problem}")
    elif not results:
        console.print("No pending stages.")


@app.command()
def status(project: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Show progress, readiness, and every open question in one view."""
    store = ProjectStore(project)
    try:
        config = load_config(store.config_path)
        state = store.load_state()
    except (OSError, ValueError) as exc:
        _fail(str(exc))
    table = Table("Stage", "Status", "Latest score")
    latest = {}
    for result in state.stage_runs:
        latest[result.stage] = result
    for stage in config.workflow.stages:
        stage_status = state.stage_status.get(stage, StageStatus.PENDING)
        score = f"{latest[stage].score:.2f}" if stage in latest else "-"
        table.add_row(stage, stage_status.value, score)
    console.print(table)
    console.print(
        f"Questions: {len(state.open_questions())} open, "
        f"{sum(bool(question.answer) for question in state.pending_questions)} answered; "
        f"intake round closed: {'yes' if state.intake_closed else 'no'}"
    )
    if state.workflow_completed:
        console.print(
            "Overall: "
            + (
                "[green]submission-ready[/green]"
                if state.submission_ready
                else "[yellow]author review required[/yellow]"
            )
        )
    if state.open_questions():
        _print_questions(store, state.open_questions())


@app.command()
def answer(
    project: Path = typer.Argument(..., exists=True, file_okay=False),
    question_id: str = typer.Argument(...),
    response: str = typer.Option(..., prompt=True),
) -> None:
    """Record one scientific answer in the persistent question ledger."""
    store = ProjectStore(project)
    try:
        config = load_config(store.config_path)
        _create_engine(store, config, MockProvider()).answer(question_id, response)
    except (FileLockTimeout, KeyError, OSError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"[green]Recorded[/green] {question_id}")


@app.command("answer-all")
def answer_all(
    project: Path = typer.Argument(..., exists=True, file_okay=False),
    response_file: Path | None = typer.Option(
        None,
        "--file",
        help="YAML answers file (defaults to the project's inputs/responses.yaml)",
    ),
) -> None:
    """Answer the complete consolidated batch in one command."""
    store = ProjectStore(project)
    try:
        config = load_config(store.config_path)
        state = store.load_state()
        open_questions = state.open_questions()
        if not open_questions:
            console.print("No open questions.")
            return
        selected_file = response_file or store.response_template_path
        answers = {
            identifier: value
            for identifier, value in store.read_response_answers(selected_file).items()
            if value
        }
        if not answers:
            raise ValueError(f"No non-empty answers were found in {selected_file}")
        _create_engine(store, config, MockProvider()).answer_many(answers)
    except (FileLockTimeout, KeyError, OSError, ValueError) as exc:
        _fail(str(exc))
    remaining = store.load_state().open_questions()
    console.print(f"[green]Recorded[/green] {len(answers)} answer(s).")
    if remaining:
        console.print(f"{len(remaining)} question(s) still require an answer.")
    else:
        console.print("The intake gate is closed. Run PaperForge again to continue automatically.")


@app.command()
def ingest(project: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Refresh PDF, DOCX, text, JSON, CSV, XLSX, and figure evidence locally."""
    store = ProjectStore(project)
    try:
        config = load_config(store.config_path)
        report = DocumentIngestor(store, config.ingestion).refresh()
    except (OSError, ValueError) as exc:
        _fail(str(exc))
    console.print(
        f"Discovered {report.discovered}; extracted {report.extracted}; "
        f"unchanged {report.unchanged}; skipped {report.skipped}."
    )
    for warning in report.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


@app.command()
def export(project: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Export Markdown, a quality report, and DOCX when document support is installed."""
    store = ProjectStore(project)
    try:
        report = OutputExporter(store).export(store.load_state())
    except (OSError, ValueError) as exc:
        _fail(str(exc))
    for path in report.files:
        console.print(f"[green]Created[/green] {path}")
    for warning in report.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


@app.command()
def doctor(
    project: Path = typer.Argument(..., exists=True, file_okay=False),
) -> None:
    """Check configuration, authentication, endpoint, and configured model visibility."""
    store = ProjectStore(project)
    provider: ModelProvider | None = None
    try:
        config = load_config(store.config_path)
        provider = _create_provider(config)
        healthy, message = provider.healthcheck()
        console.print(("[green]OK:[/green] " if healthy else "[red]Failed:[/red] ") + message)
        if isinstance(provider, OllamaProvider) and healthy:
            available = set(provider.available_models())
            configured = {role.model for role in config.models.values()}
            for model in sorted(configured):
                label = "visible" if model in available else "not listed"
                console.print(f"  {model}: {label}")
            console.print(
                "Model visibility does not guarantee plan entitlement; inference is checked on run."
            )
        if not healthy:
            raise typer.Exit(code=1)
    except (OSError, ProviderError, ValueError) as exc:
        _fail(str(exc))
    finally:
        if provider:
            provider.close()


@app.command()
def validate(project: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Run deterministic checks without sending content to a model."""
    store = ProjectStore(project)
    try:
        config = load_config(store.config_path)
        text = store.read_manuscript()
    except (OSError, ValueError) as exc:
        _fail(str(exc))
    findings = validate_engineering_manuscript(text).findings
    findings += validate_manuscript_structure(
        text, int(config.journal.get("abstract_max_words", 250))
    ).findings
    if not findings:
        console.print("[green]All implemented deterministic checks passed.[/green]")
        return
    for finding in findings:
        console.print(f"[{finding.severity.value}] {finding.id}: {finding.problem}")
    if any(finding.severity in {Severity.HIGH, Severity.BLOCKING} for finding in findings):
        raise typer.Exit(code=1)


def _print_questions(store: ProjectStore, questions) -> None:
    console.print("\n[bold yellow]One consolidated input batch is required[/bold yellow]")
    for question in questions:
        console.print(f"{question.id}: {question.text}\n  Why: {question.reason}")
    console.print(f"\nResponse template: {store.response_template_path}")
    console.print(f'Fill and save it, then rerun: paperforge run "{store.root}"')
    console.print("PaperForge imports the non-empty answers automatically.")


def _show_version(value: bool) -> bool:
    if value:
        console.print(f"PaperForge {__version__}")
        raise typer.Exit()
    return value


def _fail(message: str) -> None:
    console.print(f"[red]Error:[/red] {message}")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
