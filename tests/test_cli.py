import json

from typer.testing import CliRunner

from paperforge.cli import app
from paperforge.config import load_settings

runner = CliRunner()


def test_cli_initialization_attachment_model_policy_and_stage_route(tmp_path):
    project = tmp_path / "paper"
    assert (
        runner.invoke(
            app, ["init", str(project), "--topic", "Thermal monitoring workflow"]
        ).exit_code
        == 0
    )
    source = tmp_path / "measurements.csv"
    source.write_text("temperature\n20\n")
    assert (
        runner.invoke(app, ["attach", str(project), str(source), "--role", "dataset"]).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["model", str(project), "google", "gemini", "--billing", "free", "--select"]
        ).exit_code
        == 0
    )
    assert (
        runner.invoke(app, ["route", str(project), "local", "google", "--stage", "draft"]).exit_code
        == 0
    )
    assert (
        runner.invoke(
            app, ["policy", str(project), "--mode", "free_only", "--budget-inr", "0"]
        ).exit_code
        == 0
    )
    settings = load_settings(project / "paperforge.yaml")
    assert settings.policy == "free_only"
    assert settings.default_routes == ["google"]
    assert settings.stage_routes["draft"] == ["local", "google"]
    result = runner.invoke(app, ["run", str(project), "--until", "intake"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["stages"][0]["status"] == "completed"


def test_old_project_and_unknown_model_routes_fail_explicitly(tmp_path):
    assert runner.invoke(app, ["status", str(tmp_path)]).exit_code != 0
    project = tmp_path / "new"
    runner.invoke(app, ["init", str(project), "--topic", "A test research topic"])
    assert runner.invoke(app, ["route", str(project), "missing-route"]).exit_code != 0
    result = runner.invoke(app, ["doctor", str(project)])
    assert result.exit_code == 0
    assert "does not prove entitlement" in result.output


def test_python_module_entrypoint_matches_installed_cli():
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "paperforge", "version"], capture_output=True, text=True, check=True
    )
    assert result.stdout.strip() == "3.0.0"
