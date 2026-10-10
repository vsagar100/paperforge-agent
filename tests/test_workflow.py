from collections import Counter

import pytest

from paperforge.config import Model, write_settings
from paperforge.llm import GatewayFailure
from paperforge.schemas import (
    Decision,
    FigureSpec,
    Paragraph,
    Review,
    ReviewIssue,
    Section,
    Source,
    SourceAssessment,
    StudyPlan,
)
from paperforge.store import Store
from paperforge.workflow import Workflow


class ScholarlyFixture:
    """Synthetic, test-only metadata. Never used by the production workflow."""

    def __init__(self):
        self.calls = 0

    def search(self, *_):
        self.calls += 1
        return [
            Source(
                id="SRC-test",
                title="Synthetic test fixture",
                authors=["A. Test Author"],
                year=2025,
                url="https://example.org/test-only",
                abstract="A thermal method was evaluated.",
                access_level="abstract",
                metadata_verified=True,
            )
        ]

    def dataset_candidates(self, _):
        return []

    def close(self):
        pass


class StageFixture:
    def __init__(self):
        self.calls = Counter()
        self.fail_title = None
        self.author_block = False
        self.writing_block = False
        self.corrupt_revision = False

    def close(self):
        pass

    def structured(self, schema, *, stage, role, system, context, validate=None):
        title = context.get("requested_section", "")
        self.calls[(schema.__name__, title, role)] += 1
        if title == self.fail_title:
            self.fail_title = None
            raise GatewayFailure("Injected interruption")
        if schema == SourceAssessment:
            result = SourceAssessment(source_id=context["source_id"], category="thermal")
            if validate:
                validate(result)
            return result
        if schema == StudyPlan:
            return StudyPlan(
                research_question="How should thermal methods be evaluated?",
                proposed_gap="The accessible study leaves scope for additional validation.",
                gap_source_ids=["SRC-test"],
                objectives=["Define a reproducible evaluation"],
                method="Proposed controlled comparison",
                experiments=["Evaluate independent observations"],
                metrics=["Accuracy when labels exist"],
                assumptions=["Evidence must match the tested setting"],
                missing_evidence=[],
            )
        if schema == Review:
            issues = []
            if self.author_block or self.writing_block:
                issues = [
                    ReviewIssue(
                        section="Related Work",
                        description="Test-only review issue",
                        required_change="Supply evidence"
                        if self.author_block
                        else "Clarify synthesis",
                        category="evidence" if self.author_block else "writing",
                        needs_author=self.author_block,
                    )
                ]
            return Review(issues=issues, summary="Test-only review outcome")
        if role == "reviser" and self.corrupt_revision:
            return Section(
                title=title,
                paragraphs=[
                    Paragraph(
                        text="The experiment achieved 99.9% accuracy.",
                        kind="study",
                        evidence_ids=["invented"],
                        supporting_quotes={"invented": "99.9%"},
                    )
                ],
            )
        if title == "Related Work":
            paragraphs = [
                Paragraph(
                    text="An accessible thermal study reports evaluating a method within its stated scope.",
                    kind="literature",
                    source_ids=["SRC-test"],
                    supporting_quotes={"SRC-test": "A thermal method was evaluated."},
                )
            ]
        elif title == "Keywords":
            paragraphs = [
                Paragraph(
                    text="thermal monitoring, evidence synthesis, reproducibility, evaluation, engineering",
                    kind="disclosure",
                )
            ]
        elif title == "Declarations":
            paragraphs = [
                Paragraph(
                    text="Funding and author contributions: [AUTHOR TO PROVIDE].", kind="disclosure"
                )
            ]
        else:
            paragraphs = [
                Paragraph(
                    text="The proposed evaluation should document acquisition conditions and limitations before any empirical performance claims are made.",
                    kind="proposal",
                )
            ]
        return Section(title=title, paragraphs=paragraphs)


def engine(store, settings, gateway=None, literature=None):
    return Workflow(store, settings, gateway or StageFixture(), literature or ScholarlyFixture())


def test_end_to_end_exports_traceable_packet_and_rerun_is_idempotent(store, settings):
    gateway, literature = StageFixture(), ScholarlyFixture()
    workflow = engine(store, settings, gateway, literature)
    report = workflow.run()
    assert report["status"] == "completed_with_actions"
    assert store.stage("draft")["output"]["paper_type"] == "review"
    assert (store.root / "outputs" / "manuscript.md").exists()
    assert (store.root / "outputs" / "claim-register.json").exists()
    before = gateway.calls.copy()
    assert workflow.run() == report
    assert gateway.calls == before
    assert literature.calls == 1
    assert store.stage("export")["output"]["submission_ready"] is False


def test_mid_section_failure_resumes_without_repeating_accepted_sections(store, settings):
    gateway = StageFixture()
    gateway.fail_title = "Research Gap"
    workflow = engine(store, settings, gateway)
    with pytest.raises(GatewayFailure):
        workflow.run()
    assert Store(store.root).stage("draft")["status"] == "failed"
    assert gateway.calls[("Section", "Introduction", "writer")] == 1
    reopened = engine(Store(store.root), settings, gateway)
    assert reopened.run()["status"] == "completed_with_actions"
    assert gateway.calls[("Section", "Introduction", "writer")] == 1
    assert gateway.calls[("Section", "Research Gap", "writer")] == 2
    assert store.stage("draft")["attempt"] == 2


def test_author_decision_continues_review_without_repeating_draft(store, settings):
    gateway = StageFixture()
    gateway.author_block = True
    workflow = engine(store, settings, gateway)
    assert workflow.run()["status"] == "awaiting_author"
    before = gateway.calls.copy()
    assert workflow.run()["status"] == "awaiting_author"
    assert gateway.calls == before
    store.decide(Decision(action="defer"))
    assert workflow.run()["status"] == "paused"
    gateway.author_block = False
    store.decide(Decision(action="continue"))
    assert workflow.run()["status"] == "completed_with_actions"
    assert (
        gateway.calls[("Section", "Introduction", "writer")]
        == before[("Section", "Introduction", "writer")]
    )
    assert gateway.calls[("Review", "", "reviewer")] == 2


def test_input_change_revalidates_and_model_change_preserves_accepted_work(store, settings):
    gateway = StageFixture()
    workflow = engine(store, settings, gateway)
    workflow.run(until="analysis")
    original_plan_attempt = store.stage("plan")["attempt"]
    settings.models["local"] = Model(version="new-model-snapshot")
    write_settings(store.config_path, settings)
    workflow = engine(store, settings, gateway)
    workflow.run()
    assert store.stage("plan")["attempt"] == original_plan_attempt
    assert gateway.calls[("StudyPlan", "", "planner")] == 1
    store.write("inputs/protocol.txt", "An author-provided scope clarification.")
    workflow.run()
    assert gateway.calls[("StudyPlan", "", "planner")] == 2


def test_revision_limit_is_bounded_and_unsafe_revision_preserves_previous_text(store, settings):
    gateway = StageFixture()
    gateway.writing_block = True
    gateway.corrupt_revision = True
    workflow = engine(store, settings, gateway)
    assert workflow.run()["status"] == "awaiting_author"
    record = store.stage("review")
    assert "previous sections retained" in record["error"]
    assert "99.9%" not in (store.root / "outputs" / "manuscript.md").read_text()
    store.decide(Decision(action="continue"))
    gateway.corrupt_revision = False
    workflow.run()
    assert store.stage("review")["output"]["round"] == settings.max_review_rounds


def test_export_failure_can_resume_without_any_new_model_calls(store, settings, monkeypatch):
    gateway = StageFixture()
    workflow = engine(store, settings, gateway)
    original = workflow._export
    monkeypatch.setattr(workflow, "_export", lambda: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError):
        workflow.run()
    before = gateway.calls.copy()
    monkeypatch.setattr(workflow, "_export", original)
    assert workflow.run()["status"] == "completed_with_actions"
    assert gateway.calls == before


def test_topic_only_original_research_stays_original_and_exports_missing_evidence(store, settings):
    project = store.get("project")
    project["paper_type"] = "original_research"
    store.set("project", project)
    workflow = engine(store, settings)
    workflow.run()
    assert store.stage("draft")["output"]["paper_type"] == "original_research"
    assert store.stage("analysis")["output"]["unperformed_experiments"]


def test_explicit_stage_regeneration_bypasses_old_section_checkpoints(store, settings):
    gateway = StageFixture()
    workflow = engine(store, settings, gateway)
    workflow.run()
    store.invalidate("draft", "Explicit test regeneration")
    workflow.run()
    assert gateway.calls[("Section", "Introduction", "writer")] == 2


def test_resume_repairs_status_after_export_completion_before_final_status_write(store, settings):
    gateway = StageFixture()
    workflow = engine(store, settings, gateway)
    workflow.run()
    before = gateway.calls.copy()
    store.set("status", "running")
    assert engine(Store(store.root), settings, gateway).run()["status"] == "completed_with_actions"
    assert gateway.calls == before


def test_stage_audit_write_failure_leaves_resumable_failed_stage(store, settings, monkeypatch):
    workflow = engine(store, settings)
    original = store.write
    failure = {"pending": True}

    def interrupted_write(relative, text):
        if relative.startswith("audit/stages/intake-") and failure["pending"]:
            failure["pending"] = False
            raise OSError("Injected audit write interruption")
        return original(relative, text)

    monkeypatch.setattr(store, "write", interrupted_write)
    with pytest.raises(OSError):
        workflow.run()
    assert store.stage("intake")["status"] == "failed"
    assert workflow.run()["status"] == "completed_with_actions"


def test_original_research_packet_contains_actual_computations_and_proposed_diagram(
    store, settings
):
    import csv
    import json
    from xml.etree import ElementTree

    store.write("inputs/observations.json", '{"confusion_matrix":{"tp":8,"tn":8,"fp":2,"fn":2}}')
    store.write("inputs/manifest.json", '{"observations.json":"dataset"}')

    class PlannedFixture(StageFixture):
        def structured(self, schema, **kwargs):
            value = super().structured(schema, **kwargs)
            if schema == StudyPlan:
                value.figures = [
                    FigureSpec(
                        title="Proposed evaluation workflow",
                        purpose="Explain the proposed sequence",
                        nodes=["Acquire observations", "Evaluate metrics"],
                        edges=[(0, 1)],
                    )
                ]
            return value

    workflow = engine(store, settings, PlannedFixture())
    workflow.run()
    assert store.stage("draft")["output"]["paper_type"] == "original_research"
    packet = store.root / "outputs"
    quality = json.loads((packet / "quality-control.json").read_text())
    assert quality["datasets_used"][0]["path"] == "inputs/observations.json"
    assert quality["statistical_analyses_performed"][0]["calculator"] == "confusion_matrix_v1"
    assert any("Verify proposed Figure" in action for action in quality["author_actions"])
    diagram = ElementTree.parse(packet / "figure-01.svg")
    assert "proposed architecture" in "".join(diagram.getroot().itertext())
    with (packet / "computed-results.csv").open() as file:
        rows = list(csv.DictReader(file))
    assert any(row["Metric"] == "accuracy" and float(row["Value"]) == 0.8 for row in rows)
    assert (packet / "manuscript.docx").exists()
