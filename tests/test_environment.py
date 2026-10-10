from typer.testing import CliRunner

from paperforge.cli import app
from paperforge.config import Settings, load_settings
from paperforge.environment import checkout_root, file_values, load_environment
from paperforge.schemas import Project
from paperforge.store import Store


def root_fixture(tmp_path):
    root = tmp_path / "checkout with spaces"
    (root / "src/paperforge").mkdir(parents=True)
    (root / "pyproject.toml").write_text('[project]\nname="paperforge-agent"\n')
    return root


def test_bom_quotes_literal_values_project_precedence_and_blank_templates(tmp_path, monkeypatch):
    root = root_fixture(tmp_path)
    project = root / "projects" / "a paper"
    project.mkdir(parents=True)
    (root / ".env").write_text(
        'PF_TEST_KEY="shared#value"\nPF_OTHER_KEY=shared\nPF_LITERAL="${HOME}"\n',
        encoding="utf-8-sig",
    )
    (project / ".env").write_text(
        'PF_TEST_KEY="project value"\nPF_OTHER_KEY=\n', encoding="utf-8-sig"
    )
    monkeypatch.chdir(project)
    for name in ("PF_TEST_KEY", "PF_OTHER_KEY", "PF_LITERAL"):
        monkeypatch.setenv(name, "")
    assert checkout_root() == root
    assert file_values(project) == {
        "PF_TEST_KEY": "project value",
        "PF_OTHER_KEY": "shared",
        "PF_LITERAL": "${HOME}",
    }
    assert load_environment(project) == [root / ".env", project / ".env"]
    import os

    assert os.environ["PF_TEST_KEY"] == "project value"
    assert os.environ["PF_OTHER_KEY"] == "shared"
    assert os.environ["PF_LITERAL"] == "${HOME}"


def test_existing_process_environment_wins_and_unrelated_parent_is_not_loaded(
    tmp_path, monkeypatch
):
    (tmp_path / ".env").write_text("PF_NEVER_LOAD=should-not-be-loaded\n")
    root = root_fixture(tmp_path)
    (root / ".env").write_text("PF_TEST_KEY=saved\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("PF_TEST_KEY", "manual")
    load_environment()
    import os

    assert os.environ["PF_TEST_KEY"] == "manual"
    assert "PF_NEVER_LOAD" not in os.environ
    assert file_values(root=root)["PF_TEST_KEY"] == "saved"


def test_cli_automatically_loads_shared_key_from_checkout_without_printing_it(
    tmp_path, monkeypatch
):
    root = root_fixture(tmp_path)
    project = root / "projects" / "paper"
    Store.create(project, Project(topic="Thermal monitoring in engineering"), Settings())
    (root / ".env").write_text("GEMINI_API_KEY=synthetic-test-secret\n")
    monkeypatch.chdir(root)
    monkeypatch.setenv("GEMINI_API_KEY", "")
    runner = CliRunner()
    result = runner.invoke(
        app, ["model", str(project), "google", "gemini", "--billing", "free", "--select"]
    )
    assert result.exit_code == 0
    doctor = runner.invoke(app, ["doctor", str(project)])
    assert doctor.exit_code == 0
    assert "synthetic-test-secret" not in doctor.output
    assert str(root / ".env").replace("\\", "\\\\") in doctor.output
    assert (
        load_settings(project / "paperforge.yaml").models["google"].model == "gemini-3.5-flash-lite"
    )


def test_python_workflow_integration_loads_project_environment(store, monkeypatch):
    from paperforge.workflow import Workflow

    (store.root / ".env").write_text("PF_WORKFLOW_KEY=saved-for-library-caller\n")
    monkeypatch.setenv("PF_WORKFLOW_KEY", "")
    workflow = Workflow(store)
    try:
        import os

        assert os.environ["PF_WORKFLOW_KEY"] == "saved-for-library-caller"
    finally:
        workflow.close()
