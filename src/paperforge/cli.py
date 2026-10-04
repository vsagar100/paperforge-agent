from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import uuid4

import typer

from paperforge import __version__
from paperforge.config import Model, Settings, load_settings, write_settings
from paperforge.environment import load_environment
from paperforge.llm import Gateway, GatewayFailure
from paperforge.providers import HTTPProvider, ProviderFailure
from paperforge.schemas import Decision, Project
from paperforge.store import STAGES, Store
from paperforge.workflow import Workflow

app = typer.Typer(
    help="Evidence-first manuscript workflow with durable stages.", no_args_is_help=True
)


def output(value):
    typer.echo(json.dumps(value, indent=2, ensure_ascii=False))


def existing(path: Path) -> Store:
    store = Store(path)
    if not store.db_path.exists():
        raise typer.BadParameter(
            "Not a v3 project. Initialize a new directory; v2 projects remain untouched."
        )
    load_environment(store.root)
    return store


@app.command()
def version():
    """Show application version."""
    typer.echo(__version__)


@app.command()
def init(
    project: Path,
    topic: str = typer.Option(...),
    domain: str = "engineering",
    journal: str | None = None,
    paper_type: str = "auto",
    config: Path | None = None,
):
    """Create a fresh project; resolve defaults into its configuration."""
    try:
        load_environment()
        settings = load_settings(config) if config else Settings()
        store = Store.create(
            project,
            Project(topic=topic, domain=domain, journal=journal, paper_type=paper_type),
            settings,
        )
        output(store.snapshot())
    except (ValueError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from exc


def execute(project: Path, until: str | None):
    store = existing(project)
    workflow = Workflow(store)
    try:
        output(workflow.run(until=until))
    except Exception as exc:
        typer.echo(
            f"Workflow stopped: {exc}\nProgress is saved. Correct the cause, then run 'paperforge resume'.",
            err=True,
        )
        raise typer.Exit(1) from exc
    finally:
        workflow.close()
    if store.get("status") in {"failed", "awaiting_author"}:
        raise typer.Exit(2)


@app.command()
def run(project: Path, until: str | None = None):
    """Run pending stages; optionally stop after a named stage."""
    execute(project, until)


@app.command()
def resume(project: Path, until: str | None = None):
    """Resume saved work; deferred projects require a continue decision."""
    execute(project, until)


@app.command()
def status(project: Path):
    """Read current progress without mutating running stage state."""
    output(existing(project).snapshot())


@app.command()
def attach(project: Path, files: list[Path], role: str | None = None):
    """Copy attachments; optionally declare dataset/source/figure/code/author_note/artifact."""
    store = existing(project)
    roles = {"author_note", "dataset", "source", "figure", "code", "artifact"}
    if role and role not in roles:
        raise typer.BadParameter(f"Role must be one of {sorted(roles)}")
    with store.lock():
        paths = []
        manifest_path = store.root / "inputs" / "manifest.json"
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
        for source in files:
            if not source.is_file() or source.is_symlink():
                raise typer.BadParameter("Attach regular files, not directories or symlinks")
            target = store.root / "inputs" / source.name
            if target.exists():
                raise typer.BadParameter(
                    f"Attachment already exists: {source.name}. Rename or edit it explicitly."
                )
            if source.name in {"manifest.json", "source-dois.json"} and role:
                raise typer.BadParameter("Reserved metadata files cannot receive an evidence role")
            shutil.copy2(source, target)
            paths.append(source.name)
            if role:
                manifest[source.name] = role
        if manifest:
            store.write("inputs/manifest.json", json.dumps(manifest, indent=2))
        store.event("attachments", {"files": paths, "role": role})
    output(
        {
            "attached": paths,
            "next": "run/resume detects input changes and revalidates dependent stages",
        }
    )


@app.command("model")
def set_model(
    project: Path,
    name: str,
    provider: str,
    model: str | None = None,
    model_version: str | None = None,
    billing: str = "unknown",
    base_url: str | None = None,
    api_key_env: str | None = None,
    input_rate: float | None = None,
    output_rate: float | None = None,
    temperature: float = 0.1,
    max_output_tokens: int = 4096,
    select: bool = False,
):
    """Add a named model route; paid rate ceilings are INR per million tokens."""
    store = existing(project)
    try:
        new_model = Model(
            provider=provider,
            model=model,
            version=model_version,
            billing=billing,
            base_url=base_url,
            api_key_env=api_key_env,
            input_inr_per_million=input_rate,
            output_inr_per_million=output_rate,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        with store.lock():
            settings = load_settings(store.config_path)
            settings.models[name] = new_model
            if select:
                settings.default_routes = [name]
            write_settings(
                store.config_path, Settings.model_validate(settings.model_dump(mode="json"))
            )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    output(
        {
            "route": name,
            "requested_model": new_model.requested_id,
            "billing": new_model.billing,
            "selected_as_default": select,
            "completed_work_preserved": True,
        }
    )


@app.command()
def route(project: Path, names: list[str], stage: str | None = None, role: str | None = None):
    """Set ordered default, role or stage routes and explicit fallbacks."""
    store = existing(project)
    if stage and role:
        raise typer.BadParameter("Choose --stage or --role, not both")
    if role and role not in {"extractor", "planner", "writer", "reviewer", "reviser"}:
        raise typer.BadParameter("Unknown model role")
    with store.lock():
        settings = load_settings(store.config_path)
        if stage:
            settings.stage_routes[stage] = names
        elif role:
            settings.role_routes[role] = names
        else:
            settings.default_routes = names
        try:
            validated = Settings.model_validate(settings.model_dump(mode="json"))
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        write_settings(store.config_path, validated)
    output({"routes": names, "stage": stage, "role": role})


@app.command()
def models(project: Path, name: str):
    """List provider model metadata using saved credentials; no generation or route change."""
    store = existing(project)
    settings = load_settings(store.config_path)
    if name not in settings.models:
        raise typer.BadParameter("Unknown route name; configure it with 'paperforge model'")
    backend = HTTPProvider()
    try:
        output(
            {
                "route": name,
                "models": backend.list_models(settings.models[name]),
                "note": "Listing does not prove generation access or free entitlement. Test the selected route with 'paperforge probe'.",
            }
        )
    except ProviderFailure as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    finally:
        backend.close()


@app.command()
def probe(project: Path, name: str):
    """Make one small generation request on exactly this route; normal billing/cap applies."""
    store = existing(project)
    settings = load_settings(store.config_path)
    if name not in settings.models:
        raise typer.BadParameter("Unknown route name; configure it with 'paperforge model'")
    data = settings.model_dump(mode="json")
    data.update(default_routes=[name], stage_routes={}, role_routes={}, max_retries=0)
    # A probe always tests the current credential, rather than reusing an old success.
    gateway = Gateway(store, Settings.model_validate(data))
    try:
        with store.lock():
            reply = gateway.call(
                "probe",
                "probe",
                "You are checking text generation connectivity.",
                f"Connectivity check {uuid4()}. Reply with OK.",
            )
        output(
            {
                "route": name,
                "generation_succeeded": True,
                "requested_model": reply.requested_model,
                "returned_model": reply.returned_model,
                "input_tokens": reply.input_tokens,
                "output_tokens": reply.output_tokens,
                "note": "One generation request was made; billing/cap policy applied. This does not prove scientific writing quality or future quota.",
            }
        )
    except GatewayFailure as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    finally:
        gateway.close()


@app.command()
def policy(project: Path, mode: str = "free_first", budget_inr: float = 500):
    """Set free_only/free_first/paid_only and the project budget."""
    store = existing(project)
    with store.lock():
        settings = load_settings(store.config_path).model_dump(mode="json")
        settings.update(policy=mode, budget_inr=budget_inr)
        try:
            validated = Settings.model_validate(settings)
        except ValueError as exc:
            raise typer.BadParameter(str(exc)) from exc
        write_settings(store.config_path, validated)
    output(
        {"policy": mode, "budget_inr": budget_inr, "already_spent_or_reserved_inr": store.spent()}
    )


@app.command()
def decide(
    project: Path, action: str, note: str = "", stage: str | None = None, continue_run: bool = False
):
    """Record continue/revise/defer/cancel; decisions never bypass evidence gates."""
    store = existing(project)
    try:
        store.decide(Decision(action=action, note=note, stage=stage))
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    output(store.snapshot())
    if continue_run and action in {"continue", "revise"}:
        execute(project, None)


@app.command()
def retry(project: Path, stage: str):
    """Regenerate one stage and dependents; keep prior audit history."""
    store = existing(project)
    if stage not in STAGES:
        raise typer.BadParameter(f"Stage must be one of {STAGES}")
    with store.lock():
        if store.get("status") == "cancelled":
            raise typer.BadParameter("Cancelled projects cannot be resumed")
        store.invalidate(stage, "Explicit stage regeneration")
    output(store.snapshot())


@app.command()
def doctor(project: Path):
    """Check configuration/credential presence without a billable inference call."""
    store = existing(project)
    settings = load_settings(store.config_path)
    workflow = Workflow(store, settings)
    try:
        output(
            {
                "policy": settings.policy,
                "budget_inr": settings.budget_inr,
                "env_files": [str(path) for path in load_environment(store.root)],
                "configuration_file": str(store.config_path),
                "default_routes": settings.default_routes,
                "stage_routes": settings.stage_routes,
                "role_routes": settings.role_routes,
                "routes": {
                    name: {
                        "provider": model.provider,
                        "model": model.requested_id,
                        "billing": model.billing,
                        "eligible": workflow.gateway._eligible(model),
                    }
                    for name, model in settings.models.items()
                },
                "note": "Credential presence does not prove entitlement, availability or free quota. Use 'paperforge models' and 'paperforge probe' before writing.",
            }
        )
    finally:
        workflow.close()


if __name__ == "__main__":
    app()
