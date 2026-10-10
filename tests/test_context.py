"""Scale and recovery tests use synthetic evidence and mocked HTTP, never live inference."""

import json
from collections import Counter
from itertools import count

import httpx
import pytest

from paperforge.audit import source_text
from paperforge.config import Model, Settings
from paperforge.context import ContextComposer, section_batches, size, text_parts
from paperforge.llm import Gateway, GatewayFailure
from paperforge.providers import HTTPProvider
from paperforge.schemas import Paragraph, Project, Section, Source
from paperforge.store import Store, fingerprint
from paperforge.workflow import Workflow


class LargeLiterature:
    def __init__(self):
        self.sources = [
            Source(
                id=f"SRC-scale-{i}",
                title=f"Synthetic thermal UAV validation source {i}",
                url="https://example.org/synthetic-test-only",
                abstract=f"Source {i} evaluated thermal detection under controlled acquisition. "
                + (f"Thermal UAV acquisition and detection scope {i} were described. " * 160),
                access_level="abstract",
                metadata_verified=True,
            )
            for i in range(35)
        ]
        # A full paper alone exceeds the former 80k limit.
        self.sources[0].passages = [
            {"text": "Long accessible thermal source material. " * 4000, "locator": "full-text"}
        ]
        self.sources[0].access_level = "full_text"

    def search(self, *_):
        return self.sources

    def dataset_candidates(self, _):
        return []

    def close(self):
        pass


class WorkflowHTTP:
    def __init__(self, interrupt=False):
        self.requests = []
        self.reviews = Counter()
        self.interrupt = interrupt
        self.quota = True
        self.invalid_section = True
        self.failed_batch_routes = 0

    def handle(self, request):
        body = json.loads(request.content)
        prompt = body["messages"][-1]["content"]
        context, _ = json.JSONDecoder().raw_decode(prompt.split("\n", 1)[1])
        schema = json.loads(prompt.split("Return one JSON object matching:\n", 1)[1])["title"]
        self.requests.append((body["model"], schema, context))
        assert size(context) <= 80000
        # Exercise HTTP-provider handling and explicit free fallback within the full run.
        if self.quota and body["model"] == "first":
            self.quota = False
            return httpx.Response(429, json={"error": "Synthetic rate limit"})
        if schema == "SourceAssessment":
            quote = context["accessible_text"][: min(90, len(context["accessible_text"]))]
            response = {
                "source_id": context["source_id"],
                "category": "thermal acquisition",
                "method": {
                    "summary": "Synthetic source describes a scoped acquisition method.",
                    "quote": quote,
                },
            }
        elif schema == "StudyPlan":
            response = {
                "research_question": "What limits thermal UAV detection?",
                "proposed_gap": "Acquisition conditions require further evaluation.",
                "gap_source_ids": ["SRC-scale-0"],
                "objectives": ["Synthesize supported observations"],
                "method": "A bounded literature synthesis",
                "experiments": [],
                "metrics": [],
                "assumptions": ["Synthetic evidence is test-only"],
                "missing_evidence": [],
            }
        elif schema == "Section":
            title = context["requested_section"]
            if title == "Research Gap" and self.invalid_section and body["model"] == "first":
                self.invalid_section = False
                response = {
                    "title": title,
                    "paragraphs": [
                        {
                            "text": "An invented finding.",
                            "kind": "literature",
                            "source_ids": ["invented"],
                            "supporting_quotes": {"invented": "invented"},
                        }
                    ],
                }
            elif title == "Keywords":
                response = {
                    "title": title,
                    "paragraphs": [
                        {
                            "text": "thermal detection, UAV monitoring, acquisition, validation, evidence synthesis",
                            "kind": "disclosure",
                        }
                    ],
                }
            elif title == "Abstract":
                response = {
                    "title": title,
                    "paragraphs": [
                        {
                            "text": " ".join(
                                ["proposed scoped thermal UAV literature synthesis"] * 40
                            ),
                            "kind": "proposal",
                        }
                    ],
                }
            elif title == "Declarations":
                response = {
                    "title": title,
                    "paragraphs": [
                        {"text": "Author statements: [AUTHOR TO PROVIDE].", "kind": "disclosure"}
                    ],
                }
            elif title == "Related Work":
                response = {
                    "title": title,
                    "paragraphs": [
                        {
                            "text": f"Source {i} describes evaluation within its stated acquisition scope. "
                            + "This synthesis considers the accessible evidence within that stated scope. "
                            * 35,
                            "kind": "literature",
                            "source_ids": [f"SRC-scale-{i}"],
                            "supporting_quotes": {
                                f"SRC-scale-{i}": f"Source {i} evaluated thermal detection under controlled acquisition."
                            },
                        }
                        for i in range(5)
                    ],
                }
            else:
                response = {
                    "title": title,
                    "paragraphs": [
                        {
                            "text": f"{title} paragraph {i}. "
                            + "The proposed evaluation records thermal acquisition conditions before any performance comparison and requires independently verified observations. "
                            * 18,
                            "kind": "proposal",
                        }
                        for i in range(7)
                    ],
                }
        elif schema == "Review":
            batch = context["review_batch"]["index"]
            self.reviews[batch] += 1
            if self.interrupt and batch == 2:
                self.failed_batch_routes += 1
                if self.failed_batch_routes == 2:
                    self.interrupt = False
                return httpx.Response(401, json={"error": "Synthetic interrupted access"})
            response = {"issues": [], "summary": f"Synthetic test-only batch {batch}"}
        else:
            raise AssertionError(schema)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(response)}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 100},
            },
        )


def scale_engine(tmp_path, monkeypatch, paper_type, interrupt):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-not-a-real-key")
    settings = Settings(
        policy="free_only",
        max_schema_repairs=0,
        max_retries=0,
        models={
            name: Model(
                provider="compatible", model=name, base_url="https://example.org/v1", billing="free"
            )
            for name in ("first", "second")
        },
        default_routes=["first", "second"],
    )
    store = Store.create(
        tmp_path / "scale",
        Project(
            topic="Edge AI for thermal fire detection and geo-tagged alerting using UAVs",
            paper_type=paper_type,
        ),
        settings,
    )
    # Also exercise a sizeable author note within the default ingestion cap.
    (store.root / "inputs" / "author-notes.txt").write_text(
        "Thermal UAV author scope and proposed design. " * 2400, encoding="utf-8"
    )
    literature, backend = LargeLiterature(), WorkflowHTTP(interrupt)
    provider = HTTPProvider(transport=httpx.MockTransport(backend.handle))
    clock = count(1000, 5)
    gateway = Gateway(store, settings, provider, clock=lambda: next(clock))
    return Workflow(store, settings, gateway, literature), backend, literature


@pytest.mark.parametrize("paper_type", ["review", "original_research"])
def test_default_limit_full_workflow_large_evidence_manuscript_fallback_and_resume(
    tmp_path, monkeypatch, paper_type
):
    workflow, backend, literature = scale_engine(tmp_path, monkeypatch, paper_type, interrupt=True)
    store = workflow.store
    with pytest.raises(GatewayFailure, match="all configured routes"):
        workflow.run()
    assert store.stage("review")["status"] == "failed"
    assert store.stage("draft")["status"] == "completed"
    assert backend.reviews[0] == backend.reviews[1] == 1
    previous = Counter(schema for _, schema, _ in backend.requests)
    # Reopen SQLite and construct a fresh engine, as a new CLI process would.
    reopened = Store(store.root)
    resumed = Workflow(
        reopened,
        workflow.settings,
        Gateway(
            reopened, workflow.settings, workflow.gateway.backend, clock=workflow.gateway.clock
        ),
        literature,
    )
    assert resumed.run()["status"] == "completed_with_actions"
    current = Counter(schema for _, schema, _ in backend.requests)
    # Accepted appraisal/planning/writing and first two review batches survive reopening.
    assert current["SourceAssessment"] == previous["SourceAssessment"]
    assert current["StudyPlan"] == previous["StudyPlan"]
    assert current["Section"] == previous["Section"]
    assert backend.reviews[0] == backend.reviews[1] == 1
    assert backend.reviews[2] == 3  # two rejected routes + one successful resume
    assert backend.invalid_section is False  # evidence failure tried the alternative model
    assert store.spent() == 0
    canonical = store.stage("literature")["output"]["sources"]
    assert canonical == [s.model_dump(mode="json") for s in literature.sources]
    sections = store.stage("draft")["output"]["sections"]
    assert size(sections) > 80000
    accepted_reviews = [
        c
        for _, schema, c in backend.requests
        if schema == "Review" and (c["review_batch"]["index"] != 2)
    ]
    # Canonical manuscript is complete; every paragraph is included in some review batch.
    successful = {}
    for _, schema, context in backend.requests:
        if schema == "Review":
            successful[context["review_batch"]["index"]] = context
    reviewed = [p for c in successful.values() for s in c["sections"] for p in s["paragraphs"]]
    assert reviewed == [p for s in sections for p in s["paragraphs"]]
    assert len(accepted_reviews) > 2
    assert (store.root / "outputs" / "manuscript.md").exists()
    audits = list((store.root / "audit" / "context").glob("*.json"))
    assert audits and any(json.loads(p.read_text(encoding="utf-8"))["omitted"] for p in audits)
    appraisal_audit = json.loads(
        next((store.root / "audit" / "appraisal").glob("*.json")).read_text(encoding="utf-8")
    )
    spans = appraisal_audit["batches"]
    assert spans[0]["start"] == 0 and spans[-1]["end"] == len(source_text(literature.sources[0]))
    assert all(a["end"] == b["start"] for a, b in zip(spans, spans[1:], strict=False))
    before = len(backend.requests)
    workflow.run()
    assert len(backend.requests) == before
    workflow.close()


def test_exact_text_splitting_accounts_for_escaped_characters():
    text = '"\\\t\n😀 exact whitespace ' * 300
    spans = text_parts(text, 160)
    assert "".join(part for _, part in spans) == text
    assert all(size(part) <= 160 for _, part in spans)
    assert all(start == sum(len(p) for _, p in spans[:i]) for i, (start, _) in enumerate(spans))


def test_paragraph_batches_preserve_titles_quotes_and_complete_content():
    sections = [
        Section(
            title=f"Section {i}",
            paragraphs=[
                Paragraph(text=f"Paragraph {j}: " + "evidence " * 100, kind="proposal")
                for j in range(8)
            ],
        )
        for i in range(3)
    ]
    batches = section_batches(sections, 3000)
    assert all(size(batch) <= 3000 for batch in batches)
    assert [p for b in batches for s in b for p in s["paragraphs"]] == [
        p.model_dump(mode="json") for s in sections for p in s.paragraphs
    ]
    with pytest.raises(ValueError, match="single paragraph"):
        section_batches(sections, 100)


def test_context_retains_valid_claim_anchors_and_audits_omissions(store, settings):
    sources = LargeLiterature().sources
    quote = "Source 34 evaluated thermal detection under controlled acquisition."
    packet = ContextComposer(store, 8000).compose(
        project=store.get("project"),
        sources=sources,
        assessments=[],
        inputs=[],
        results=[],
        task={
            "sections": [
                {
                    "title": "Related Work",
                    "paragraphs": [
                        {
                            "text": "Supported observation",
                            "supporting_quotes": {"SRC-scale-34": quote, "invented": "fabricated"},
                        }
                    ],
                }
            ]
        },
    )
    assert size(packet) <= 8000
    assert any(
        x["id"] == "SRC-scale-34" and quote in x["excerpts"][0]["text"] for x in packet["sources"]
    )
    assert not any(x["id"] == "invented" for x in packet["sources"])
    audit = json.loads(
        (store.root / packet["context_selection"]["audit_file"]).read_text(encoding="utf-8")
    )
    assert audit["omitted"] and audit["selected"]
    assert audit["packet_chars"] == size(packet)


def test_legacy_accepted_section_checkpoint_is_reused(store, settings):
    from test_workflow import StageFixture, engine

    gateway = StageFixture()
    workflow = engine(store, settings, gateway)
    workflow.run(until="analysis")
    title = "Introduction"
    task = {
        "plan": workflow._output("plan")["plan"],
        "paper_type": "review",
        "requested_section": title,
        "generation": 0,
        "section_word_target": settings.minimum_section_words,
    }
    legacy_key = "section:" + fingerprint([title, {**workflow._legacy_context(), **task}, 1])
    saved = Section(
        title=title,
        paragraphs=[
            Paragraph(
                text="An accepted previously saved proposed evaluation of thermal conditions.",
                kind="proposal",
            )
        ],
    )
    store.checkpoint(legacy_key, saved.model_dump(mode="json"))
    workflow.run(until="draft")
    assert gateway.calls[("Section", title, "writer")] == 0
    assert store.stage("draft")["output"]["sections"][0] == saved.model_dump(mode="json")


def test_long_paragraph_gets_its_own_batch_without_text_loss():
    paragraph = Paragraph(text="An exact long paragraph " * 150, kind="proposal")
    section = Section(
        title="Discussion",
        paragraphs=[paragraph, Paragraph(text="Short next paragraph.", kind="proposal")],
    )
    batches = section_batches([section], 1000, max_paragraph_budget=5000)
    assert len(batches) == 2
    assert batches[0][0]["paragraphs"] == [paragraph.model_dump(mode="json")]
    assert size(batches[0]) <= 5000


def test_late_input_chunks_are_retrievable_and_result_records_are_not_replaced(store):
    from paperforge.schemas import InputItem

    text = "Urgent thermal UAV scope from the final author note."
    chunks = [
        {"locator": f"page {i}", "text": "Unrelated background text. " * 80} for i in range(29)
    ]
    chunks.append({"locator": "page 29", "text": text})
    item = InputItem(
        id="INPUT-notes",
        path="inputs/notes.txt",
        sha256="test-only",
        role="author_note",
        status="extracted",
        chunks=chunks,
    )
    result = {"id": "RESULT-actual", "calculator": "test-only", "metrics": {"samples": 10}}
    packet = ContextComposer(store, 6000).compose(
        project=store.get("project"),
        sources=[],
        assessments=[],
        inputs=[item],
        results=[result],
        task={
            "sections": [
                {
                    "title": "Results",
                    "paragraphs": [
                        {
                            "text": "Unverified claim",
                            "supporting_quotes": {"RESULT-actual": "Invented 999"},
                        }
                    ],
                }
            ]
        },
    )
    assert result in packet["results"]
    assert all(r["metrics"]["samples"] == 10 for r in packet["results"])
    assert any(text == c["text"] for i in packet["inputs"] for c in i["excerpts"])
    assert packet["context_selection"]["omitted_units"] > 0


def test_small_configured_limit_keeps_selection_manifest_inside_budget(store):
    source = Source(
        id="SRC-small",
        title="Small synthetic source",
        url="https://example.org/test",
        abstract="A thermal method was evaluated.",
    )
    packet = ContextComposer(store, 1000).compose(
        project=store.get("project"), sources=[source], assessments=[], inputs=[], results=[]
    )
    assert size(packet) <= 1000
    assert "context_selection" in packet


def test_packet_budget_change_preserves_accepted_writing_after_failure(store, settings):
    from test_workflow import StageFixture, engine

    gateway = StageFixture()
    gateway.fail_title = "Research Gap"
    workflow = engine(store, settings, gateway)
    with pytest.raises(GatewayFailure):
        workflow.run()
    settings.max_context_chars = 16000
    assert workflow.run()["status"] == "completed_with_actions"
    assert gateway.calls[("Section", "Introduction", "writer")] == 1
    assert gateway.calls[("Section", "Related Work", "writer")] == 1
