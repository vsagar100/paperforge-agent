from __future__ import annotations

import re
from importlib.resources import as_file, files
from pathlib import Path

import typer
from dotenv import find_dotenv, load_dotenv
from filelock import Timeout as FileLockTimeout
from rich.console import Console
from rich.table import Table

from paperforge import __version__
from paperforge.config import AppConfig, load_config
from paperforge.domain import IssueDisposition, PaperType, ResearchProfile, StageStatus
from paperforge.exporters import OutputExporter
from paperforge.literature import LiteratureError, LiteratureService
from paperforge.llm import LLMClient
from paperforge.providers import MockProvider, OllamaProvider, ProviderError
from paperforge.providers.base import ModelProvider, ModelRequest
from paperforge.stages import StageRunner
from paperforge.storage import ProjectStore
from paperforge.validators import ValidationContext, validate_manuscript
from paperforge.workflow import WorkflowEngine, WorkflowReport

dotenv_path = find_dotenv(usecwd=True)
if dotenv_path:
    load_dotenv(dotenv_path)

app = typer.Typer(
    help="Evidence-grounded, publication-oriented research paper workflow.",
    no_args_is_help=True,
)
console = Console()


def _default_config() -> Path:
    packaged = files("paperforge").joinpath("default.yaml")
    if packaged.is_file():
        with as_file(packaged) as path:
            return Path(path)
    return Path(__file__).resolve().parents[2] / "config" / "default.yaml"


def _provider(config: AppConfig, selected: str | None = None) -> ModelProvider:
    name = selected or config.provider.active
    if name == "mock":
        return MockProvider()
    if name == "ollama":
        return OllamaProvider(config)
    raise ValueError("Provider must be 'ollama' or 'mock'.")


def _engine(
    store: ProjectStore,
    config: AppConfig,
    provider: ModelProvider,
) -> tuple[WorkflowEngine, LiteratureService]:
    literature = LiteratureService(config.literature)
    runner = StageRunner(store, config, LLMClient(config, provider), literature)
    return WorkflowEngine(store, config, runner), literature


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
def write(
    research_input: str | None = typer.Argument(
        None,
        help="Research topic or complete synopsis. Omit to enter it once at the prompt.",
    ),
    project: Path | None = typer.Option(
        None,
        "--project",
        "-p",
        help="Project directory; defaults to projects/<topic-slug>.",
    ),
    from_file: Path | None = typer.Option(
        None,
        "--from-file",
        exists=True,
        dir_okay=False,
        help="Read a synopsis from a UTF-8 text/Markdown file.",
    ),
    domain: str = typer.Option("engineering", help="Research discipline."),
    journal: str | None = typer.Option(None, help="Target journal, if known."),
    paper_type: PaperType = typer.Option(
        PaperType.AUTO, help="auto, original_research, or review_article"
    ),
    provider: str | None = typer.Option(None, help="Override provider: ollama or mock."),
) -> None:
    """Create and run a paper from one topic or synopsis input."""
    if research_input and from_file:
        _fail("Provide either the positional research input or --from-file, not both.")
    text = (
        from_file.read_text(encoding="utf-8").strip()
        if from_file
        else (research_input or typer.prompt("Research topic or synopsis")).strip()
    )
    if len(text) < 8:
        _fail("Research input must contain at least eight characters.")
    topic, synopsis = _split_topic_and_synopsis(text)
    root = project or Path("projects") / _slug(topic)
    store = ProjectStore(root)
    if store.state_path.exists():
        _fail(
            f"Project already exists: {store.root}. Use 'paperforge run' to resume it or choose "
            "a different --project path."
        )
    try:
        store.initialize(
            ResearchProfile(
                topic=topic,
                synopsis=synopsis,
                domain=domain,
                requested_paper_type=paper_type,
                target_journal=journal,
            ),
            _default_config(),
        )
    except (FileExistsError, OSError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"[green]Created[/green] {store.root}")
    _run_project(store, provider=provider, force_rebuild=False)


@app.command()
def init(
    project: Path = typer.Argument(..., help="New project directory."),
    topic: str = typer.Option(..., prompt=True, help="Research topic."),
    synopsis_file: Path | None = typer.Option(
        None,
        "--synopsis-file",
        exists=True,
        dir_okay=False,
        help="Optional synopsis text/Markdown file.",
    ),
    domain: str = typer.Option("engineering", help="Research discipline."),
    journal: str | None = typer.Option(None, help="Target journal, if known."),
    paper_type: PaperType = typer.Option(PaperType.AUTO),
) -> None:
    """Initialize without running; paperforge write is the minimal one-command path."""
    synopsis = synopsis_file.read_text(encoding="utf-8").strip() if synopsis_file else None
    try:
        store = ProjectStore(project)
        state = store.initialize(
            ResearchProfile(
                topic=topic,
                synopsis=synopsis,
                domain=domain,
                requested_paper_type=paper_type,
                target_journal=journal,
            ),
            _default_config(),
        )
    except (FileExistsError, OSError, ValueError) as exc:
        _fail(str(exc))
    console.print(f"[green]Created[/green] {store.root} ({state.project_id})")
    console.print(f'Run: paperforge run "{store.root}"')


@app.command()
def run(
    project: Path = typer.Argument(..., exists=True, file_okay=False),
    provider: str | None = typer.Option(None, help="Override provider: ollama or mock."),
    rebuild: bool = typer.Option(
        False,
        "--rebuild",
        help="Preserve inputs and restart all generated stages.",
    ),
) -> None:
    """Resume until completion or a genuine evidence-integrity blocker."""
    _run_project(ProjectStore(project), provider=provider, force_rebuild=rebuild)


@app.command()
def status(project: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Show stage progress, readiness, and remaining author actions."""
    store = ProjectStore(project)
    try:
        config = load_config(store.config_path)
        state = store.load_state()
    except (OSError, ValueError) as exc:
        _fail(str(exc))
    table = Table("Stage", "Status", "Score", "Model")
    for stage in config.workflow.stages:
        record = state.stage_records.get(stage)
        table.add_row(
            stage,
            state.status_for(stage).value,
            f"{record.score:.2f}" if record else "-",
            record.model if record and record.model else "-",
        )
    console.print(table)
    if state.workflow_completed:
        label = "submission candidate" if state.submission_ready else "author action required"
        console.print(f"Overall: [bold]{label}[/bold]")
    if state.author_actions:
        console.print("\n[bold]Author actions[/bold]")
        for action in state.author_actions:
            prefix = "BLOCKING" if action.blocking else "Review"
            console.print(f"- {prefix}: {action.action}")


@app.command()
def doctor(
    project: Path = typer.Argument(..., exists=True, file_okay=False),
    inference: bool = typer.Option(
        False,
        "--inference",
        help="Also send a minimal prompt to verify actual model entitlement.",
    ),
) -> None:
    """Check configuration, endpoint, model visibility, and optional inference access."""
    store = ProjectStore(project)
    provider: ModelProvider | None = None
    try:
        config = load_config(store.config_path)
        provider = _provider(config)
        healthy, message = provider.healthcheck()
        console.print(("[green]OK:[/green] " if healthy else "[red]Failed:[/red] ") + message)
        if not healthy:
            raise typer.Exit(code=1)
        available = set(provider.available_models())
        for model in sorted({role.model for role in config.models.values()}):
            label = "visible" if not available or model in available else "not listed"
            console.print(f"  {model}: {label}")
        if inference:
            response = provider.generate(
                ModelRequest(
                    role="planner",
                    system="Return a concise response.",
                    prompt="Reply with exactly OK.",
                    temperature=0,
                    metadata={"operation": "doctor"},
                )
            )
            console.print(f"[green]Inference OK:[/green] {response.model}")
        elif isinstance(provider, OllamaProvider):
            console.print("Use --inference to verify plan entitlement, not only model visibility.")
    except (OSError, ProviderError, ValueError) as exc:
        _fail(str(exc))
    finally:
        if provider:
            provider.close()


@app.command()
def validate(project: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Run the deterministic final manuscript gates without a model call."""
    store = ProjectStore(project)
    try:
        config = load_config(store.config_path)
        context = ValidationContext(
            config=config,
            plan=store.load_plan(),
            evidence=store.load_evidence(),
            references=store.load_references(),
            publication_profile=store.load_publication_profile(),
            claim_ledger=store.load_claim_ledger(),
            evidence_coverage=store.load_evidence_coverage(),
        )
        issues = validate_manuscript(
            store.read_manuscript(),
            context,
            review_type="final_review",
        )
    except (OSError, ValueError) as exc:
        _fail(str(exc))
    if not issues:
        console.print("[green]All deterministic final gates passed.[/green]")
        return
    for issue in issues:
        console.print(
            f"[{issue.severity.value}] {issue.code}: {issue.description} "
            f"({issue.disposition.value if issue.disposition else 'unclassified'})"
        )
    if any(issue.disposition == IssueDisposition.INTEGRITY_BLOCKER for issue in issues):
        raise typer.Exit(code=1)


@app.command()
def export(project: Path = typer.Argument(..., exists=True, file_okay=False)) -> None:
    """Recreate all output artifacts from the current manuscript and state."""
    store = ProjectStore(project)
    try:
        report = OutputExporter(store).export(store.load_state())
    except (OSError, ValueError) as exc:
        _fail(str(exc))
    for path in report.files:
        console.print(f"[green]Created[/green] {path}")
    for warning in report.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


def _run_project(
    store: ProjectStore,
    *,
    provider: str | None,
    force_rebuild: bool,
) -> None:
    model_provider: ModelProvider | None = None
    literature: LiteratureService | None = None
    try:
        config = load_config(store.config_path)
        model_provider = _provider(config, provider)
        engine, literature = _engine(store, config, model_provider)
        report = engine.run(force_rebuild=force_rebuild)
    except (
        FileLockTimeout,
        LiteratureError,
        OSError,
        ProviderError,
        ValueError,
        KeyError,
    ) as exc:
        _fail(str(exc))
    finally:
        if literature:
            literature.close()
        if model_provider:
            model_provider.close()
    _print_run_report(store, report)


def _print_run_report(store: ProjectStore, report: WorkflowReport) -> None:
    if report.invalidated:
        console.print("[yellow]Input change detected; generated stages were rebuilt.[/yellow]")
    if report.resumed_stages:
        console.print(f"Resumed {report.resumed_stages} completed stage(s).")
    for record in report.records:
        console.print(f"{record.stage}: {record.status.value} (score={record.score:.2f})")
        for change in record.changes:
            console.print(f"  revised: {change}")
    state = store.load_state()
    if state.workflow_completed:
        if state.submission_ready:
            console.print("[green]Completed: publication submission candidate.[/green]")
        else:
            console.print("[yellow]Completed: author action remains before submission.[/yellow]")
    elif report.records and report.records[-1].status == StageStatus.BLOCKED:
        console.print("[red]Stopped at a genuine evidence-integrity blocker.[/red]")
        for issue in report.records[-1].issues:
            if issue.disposition == IssueDisposition.INTEGRITY_BLOCKER:
                console.print(f"  {issue.code}: {issue.description}")
                console.print(f"    Required: {issue.required_change}")
        evidence_actions = store.root / "author-actions" / "evidence-required.md"
        if report.records[-1].stage == "evidence_mapping" and evidence_actions.exists():
            console.print(f"[yellow]Complete the evidence prompts:[/yellow] {evidence_actions}")
            console.print("Save answers under inputs/responses.yaml, then rerun paperforge run.")
    for path in report.export.files:
        console.print(f"  {path}")
    for warning in report.export.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}")


def _split_topic_and_synopsis(value: str) -> tuple[str, str | None]:
    lines = [line.strip(" #\t") for line in value.splitlines() if line.strip()]
    if len(value.split()) <= 24 and len(lines) == 1:
        return value.strip(), None
    topic = lines[0].rstrip(".") if lines else value[:140].strip()
    if len(topic) > 180:
        topic = " ".join(value.split()[:20]).rstrip(".,;:")
    return topic, value.strip()


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:60].rstrip("-") or "paperforge-project"


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
