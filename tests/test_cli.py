from pathlib import Path

from typer.testing import CliRunner

from paperforge.cli import app

runner = CliRunner()


def test_version_init_and_status_commands(tmp_path: Path) -> None:
    version = runner.invoke(app, ["--version"])
    assert version.exit_code == 0
    assert "PaperForge 2.1.0" in version.stdout

    project = tmp_path / "cli-paper"
    initialized = runner.invoke(
        app,
        ["init", str(project), "--topic", "Thermal monitoring for engineering systems"],
    )
    assert initialized.exit_code == 0, initialized.stdout
    assert project.joinpath("audit", "state.json").exists()
    assert project.joinpath("paperforge.yaml").exists()

    status = runner.invoke(app, ["status", str(project)])
    assert status.exit_code == 0, status.stdout
    assert "prepare" in status.stdout
    assert "pending" in status.stdout


def test_write_does_not_silently_ignore_input_for_existing_project(tmp_path: Path) -> None:
    project = tmp_path / "existing-paper"
    initialized = runner.invoke(
        app,
        ["init", str(project), "--topic", "Existing thermal engineering project"],
    )
    assert initialized.exit_code == 0, initialized.stdout

    result = runner.invoke(
        app,
        ["write", "A different research synopsis", "--project", str(project)],
    )
    assert result.exit_code == 1
    assert "Project already exists" in result.stdout
    assert "paperforge run" in result.stdout
