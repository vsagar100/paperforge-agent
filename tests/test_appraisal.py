import json

import pytest

from paperforge.audit import source_text
from paperforge.llm import EvidenceValidationError, Gateway, GatewayFailure
from paperforge.prompts import APPRAISE
from paperforge.schemas import Reply, Source, SourceAssessment
from paperforge.store import Store, fingerprint
from paperforge.workflow import Workflow


def source(identifier="SRC-one"):
    return Source(
        id=identifier,
        title="Synthetic appraisal fixture",
        url="https://example.org/test-only",
        abstract="A thermal method was evaluated. Independent tests were not performed.",
        access_level="abstract",
    )


def assessment(identifier="SRC-one", quote="A thermal method was evaluated."):
    return SourceAssessment(
        source_id=identifier,
        category="thermal",
        method={"summary": "A thermal approach was tested.", "quote": quote},
    )


class Backend:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.prompts = []

    def has_credentials(self, _):
        return True

    def generate(self, model, system, prompt):
        self.prompts.append(prompt)
        return Reply(
            text=next(self.replies).model_dump_json(),
            provider=model.provider,
            requested_model=model.requested_id,
            returned_model=model.requested_id,
        )

    def close(self):
        pass


class Literature:
    def __init__(self, sources):
        self.sources = sources

    def search(self, *_):
        return self.sources


def test_cached_invalid_appraisal_is_repaired_before_checkpoint(store, settings):
    settings.max_schema_repairs = 1
    item = source()
    material = source_text(item)
    bad = assessment(quote="An invented quotation.")
    good = assessment()
    backend = Backend([bad, good])
    gateway = Gateway(store, settings, backend)
    # Simulate valid JSON cached before evidence validation was added.
    context = {
        "source_id": item.id,
        "source_title": item.title,
        "access_level": item.access_level,
        "accessible_text": material,
    }
    gateway.structured(
        SourceAssessment, stage="literature", role="extractor", system=APPRAISE, context=context
    )
    workflow = Workflow(store, settings, gateway, Literature([item]))
    assert workflow.run(until="literature")["status"] == "pending"
    assert len(backend.prompts) == 2
    assert "method.quote" in backend.prompts[-1]
    assert "return null" in backend.prompts[-1]
    assert json.dumps(context, ensure_ascii=False) in backend.prompts[-1]
    assert store.stage("literature")["output"]["assessments"] == [good.model_dump()]
    key = "appraisal:" + fingerprint([item.id, material, 1])
    assert store.cached_checkpoint(key) == good.model_dump()
    workflow.run(until="literature")
    assert len(backend.prompts) == 2


@pytest.mark.parametrize(
    "field", ["source_id", "method", "dataset_or_system", "finding", "reported_limitation", "blank"]
)
def test_appraisal_rejects_wrong_identity_and_unsupported_quotes(field):
    item = source()
    value = SourceAssessment(source_id=item.id, category="thermal")
    if field == "source_id":
        value.source_id = "SRC-other"
    else:
        value = SourceAssessment.model_validate(
            {
                **value.model_dump(),
                "method" if field == "blank" else field: {
                    "summary": "Unsupported test-only fact",
                    "quote": " " if field == "blank" else "Invented quotation",
                },
            }
        )
    with pytest.raises(EvidenceValidationError, match=item.id):
        Workflow._validate_appraisal(value, item, source_text(item))


def test_exhausted_repairs_preserve_accepted_sources_and_resume_on_new_model(store, settings):
    settings.max_schema_repairs = 1
    items = [source(), source("SRC-two")]
    bad = assessment("SRC-two", "An invented quotation.")
    backend = Backend([assessment(), bad, bad])
    workflow = Workflow(store, settings, Gateway(store, settings, backend), Literature(items))
    with pytest.raises(GatewayFailure, match=r"SRC-two.*method.quote"):
        workflow.run(until="literature")
    assert len(backend.prompts) == 3
    assert store.stage("intake")["status"] == "completed"
    assert store.stage("literature")["status"] == "failed"
    failed_key = "appraisal:" + fingerprint([items[1].id, source_text(items[1]), 1])
    assert store.cached_checkpoint(failed_key) is None
    # A fresh model route must not replay the rejected response cache.
    settings.models["local"].version = "replacement-model"
    replacement = Backend([assessment("SRC-two")])
    reopened = Store(store.root)
    resumed = Workflow(
        reopened, settings, Gateway(reopened, settings, replacement), Literature(items)
    )
    assert resumed.run(until="literature")["status"] == "pending"
    assert len(replacement.prompts) == 1
    assert reopened.stage("intake")["attempt"] == 1
    assert reopened.stage("literature")["attempt"] == 2
    assert len(reopened.stage("literature")["output"]["assessments"]) == 2
