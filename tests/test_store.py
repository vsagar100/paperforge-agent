import pytest
from filelock import Timeout

from paperforge.schemas import Decision
from paperforge.store import Store


def test_progress_survives_a_new_store_and_read_does_not_repair_running(store):
    store.start("draft")
    reopened = Store(store.root)
    assert reopened.stage("draft")["status"] == "running"
    reopened.finish("draft", {"sections": []})
    assert store.stage("draft")["status"] == "completed"
    assert list((store.root / "audit" / "stages").glob("draft-*.json"))


def test_reservations_survive_crashes_and_budget_is_transactional(store):
    store.reserve("first", "paid", 300, 500)
    with pytest.raises(ValueError, match="Budget"):
        Store(store.root).reserve("second", "paid", 201, 500)
    assert store.spent() == 300
    assert store.reserve("zero", "free", 0, 500)


def test_decisions_pause_resume_cancel_without_bypassing_stage_checks(store):
    store.start("review")
    store.finish("review", {"issues": ["missing evidence"]}, "blocked")
    store.decide(Decision(action="defer"))
    assert store.get("status") == "paused"
    store.decide(Decision(action="continue"))
    assert store.get("status") == "pending"
    assert store.stage("review") is None
    assert store.get("epoch:review") == 1
    store.decide(Decision(action="cancel"))
    with pytest.raises(ValueError, match="Cancelled"):
        store.decide(Decision(action="continue"))


def test_paths_and_duplicate_execution_are_guarded(store):
    with pytest.raises(ValueError):
        store.write("../escape.txt", "bad")
    with store.lock():
        with pytest.raises(Timeout):
            with Store(store.root).lock():
                pass


def test_invalidation_keeps_upstream_stages_and_history(store):
    for name in ("intake", "plan", "draft"):
        store.start(name)
        store.finish(name, {"marker": name})
    store.invalidate("draft", "Author requested revision")
    assert store.stage("plan")["output"] == {"marker": "plan"}
    assert store.stage("draft") is None
    assert list((store.root / "audit" / "stages").glob("draft-*.json"))
