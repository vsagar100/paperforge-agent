import json
from collections import Counter
from pathlib import Path

import yaml

from paperforge.config import load_config
from paperforge.domain import ResearchProfile, StageStatus
from paperforge.providers.base import ModelProvider, ModelRequest, ModelResponse
from paperforge.stages import StageExecutor
from paperforge.storage import ProjectStore
from paperforge.workflow import WorkflowEngine

COMPLETE_MANUSCRIPT = """# Abstract
This work reports an evidence-grounded engineering study.
# Introduction
Context and objective.
# Methodology
Calibration and uncertainty were evaluated. Reproducibility is supported by parameters.
# Results
Verified results are reported.
# Discussion
The result is interpreted within the operating range.
# Limitations
Limitations are stated.
# Conclusion
The objective was addressed.
# References
No external references supplied.
"""


class DraftingProvider(ModelProvider):
    def generate(self, request: ModelRequest) -> ModelResponse:
        patches = []
        if request.role == "drafting" and '"manuscript": ""' in request.prompt:
            patches = [
                {
                    "section": "__document__",
                    "before": "",
                    "after": COMPLETE_MANUSCRIPT,
                    "rationale": "Create initial evidence-safe scaffold",
                    "evidence_ids": [],
                    "scientific_change": False,
                }
            ]
        content = json.dumps(
            {"score": 1, "findings": [], "questions": [], "patches": patches, "notes": []}
        )
        return ModelResponse(content=content, model="test-model", provider="test")

    def healthcheck(self) -> tuple[bool, str]:
        return True, "ready"


def test_workflow_runs_and_applies_initial_document_patch(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    store.initialize(ResearchProfile(topic="Engineering condition monitoring experiment"), default)
    config = load_config(store.config_path)
    executor = StageExecutor(store, config, DraftingProvider())
    results = WorkflowEngine(store, config, executor).run()
    assert results[-1].status == StageStatus.PASSED
    assert "# Methodology" in store.read_manuscript()
    assert all(x == StageStatus.PASSED for x in store.load_state().stage_status.values())


class ReaskingProvider(ModelProvider):
    def __init__(self, ask_later: bool = False) -> None:
        self.calls: Counter[str] = Counter()
        self.ask_later = ask_later

    def generate(self, request: ModelRequest) -> ModelResponse:
        stage = str(request.metadata["stage"])
        self.calls[stage] += 1
        questions = []
        if stage == "intake":
            offset = (self.calls[stage] - 1) * 5
            questions = [
                {
                    "key": f"intake_group_{offset + index}",
                    "text": f"Provide consolidated research details group {offset + index}.",
                    "reason": "Required for evidence-grounded drafting.",
                    "blocking": True,
                }
                for index in range(1, 6)
            ]
        elif self.ask_later and stage == "methodology_review":
            questions = [
                {
                    "key": "another_method_detail",
                    "text": "Provide another method detail.",
                    "reason": "The model attempted a later follow-up.",
                    "blocking": True,
                }
            ]
        payload = {
            "score": 1,
            "findings": [],
            "questions": questions,
            "patches": [],
            "replacement_document": COMPLETE_MANUSCRIPT if stage == "initial_draft" else None,
            "profile_update": None,
            "notes": [],
        }
        return ModelResponse(content=json.dumps(payload), model="test-model", provider="test")

    def healthcheck(self) -> tuple[bool, str]:
        return True, "ready"


def test_answered_intake_never_generates_question_batches_6_to_18(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    store.initialize(ResearchProfile(topic="UAV thermal fire detection engineering study"), default)
    config = load_config(store.config_path)
    provider = ReaskingProvider()
    engine = WorkflowEngine(store, config, StageExecutor(store, config, provider))

    first_run = engine.run()
    assert first_run[-1].status == StageStatus.NEEDS_INPUT
    assert len(store.load_state().open_questions()) == 5
    assert provider.calls["intake"] == 1

    engine.answer_many(
        {
            question.id: f"Complete answer for {question.id}"
            for question in store.load_state().open_questions()
        }
    )
    prompt_context = json.loads(engine.executor._build_prompt("outline", [], 1))
    assert len(prompt_context["answered_questions"]) == 5
    assert prompt_context["interaction_policy"]["questions_allowed"] is False
    second_run = engine.run()
    state = store.load_state()
    assert provider.calls["intake"] == 1
    assert state.open_questions() == []
    assert len(state.pending_questions) == 5
    assert state.workflow_completed is True
    assert second_run[0].stage == "intake"
    assert "without another model call" in second_run[0].notes[0]

    calls_after_completion = provider.calls.copy()
    assert engine.run() == []
    assert provider.calls == calls_after_completion


def test_run_imports_completed_response_template_without_answer_all(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    store.initialize(ResearchProfile(topic="UAV thermal fire detection field study"), default)
    config = load_config(store.config_path)
    provider = ReaskingProvider()
    engine = WorkflowEngine(store, config, StageExecutor(store, config, provider))

    first_run = engine.run()
    assert first_run[-1].status == StageStatus.NEEDS_INPUT
    response_payload = yaml.safe_load(store.response_template_path.read_text(encoding="utf-8"))
    for question_id in response_payload["answers"]:
        response_payload["answers"][question_id] = (
            "Objective, setup, dataset, verified results, and constraints supplied by the user."
        )
    store.response_template_path.write_text(
        yaml.safe_dump(response_payload, sort_keys=False), encoding="utf-8"
    )

    # The user-facing minimal path is now edit -> run. No answer-all call is required.
    second_run = engine.run()
    state = store.load_state()
    assert engine.last_imported_answers == 5
    assert state.open_questions() == []
    assert state.intake_closed is True
    assert state.workflow_completed is True
    assert provider.calls["intake"] == 1
    assert second_run[0].stage == "intake"
    assert all(store.read_response_answers().values())


def test_run_preserves_partial_answers_and_waits_only_for_remaining_questions(
    tmp_path: Path,
) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    store.initialize(ResearchProfile(topic="UAV thermal telemetry reliability study"), default)
    config = load_config(store.config_path)
    provider = ReaskingProvider()
    engine = WorkflowEngine(store, config, StageExecutor(store, config, provider))

    engine.run()
    response_payload = yaml.safe_load(store.response_template_path.read_text(encoding="utf-8"))
    first_question_id = next(iter(response_payload["answers"]))
    response_payload["answers"][first_question_id] = "First consolidated evidence answer."
    store.response_template_path.write_text(
        yaml.safe_dump(response_payload, sort_keys=False), encoding="utf-8"
    )

    assert engine.run() == []
    state = store.load_state()
    assert engine.last_imported_answers == 1
    assert len(state.open_questions()) == 4
    assert provider.calls["intake"] == 1
    assert store.read_response_answers()[first_question_id] == (
        "First consolidated evidence answer."
    )


def test_later_stage_question_is_a_warning_not_another_input_round(tmp_path: Path) -> None:
    default = Path(__file__).parents[1] / "config" / "default.yaml"
    store = ProjectStore(tmp_path / "paper")
    store.initialize(ResearchProfile(topic="Condition monitoring engineering study"), default)
    config = load_config(store.config_path)
    provider = ReaskingProvider(ask_later=True)
    engine = WorkflowEngine(store, config, StageExecutor(store, config, provider))

    engine.run()
    engine.answer_many(
        {question.id: f"Answer {question.id}" for question in store.load_state().open_questions()}
    )
    engine.run()
    state = store.load_state()
    methodology = [run for run in state.stage_runs if run.stage == "methodology_review"][-1]
    assert state.open_questions() == []
    assert len(state.pending_questions) == 5
    assert methodology.status == StageStatus.PASSED_WITH_WARNINGS
    assert any(finding.id.startswith("INPUT-DEFERRED") for finding in methodology.findings)
