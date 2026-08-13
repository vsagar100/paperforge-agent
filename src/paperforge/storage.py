from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import yaml
from filelock import FileLock

from paperforge.domain import (
    CURRENT_STATE_SCHEMA,
    Claim,
    EvidenceItem,
    ResearchProfile,
    StageStatus,
    WorkflowState,
    normalize_question_key,
    utc_now,
)

PROJECT_DIRS = (
    "inputs",
    "sources",
    "data",
    "figures",
    "evidence",
    "claims",
    "manuscript/versions",
    "reviews",
    "audit",
    "outputs",
)


class ProjectStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @property
    def config_path(self) -> Path:
        return self.root / "paperforge.yaml"

    @property
    def state_path(self) -> Path:
        return self.root / "audit" / "state.json"

    @property
    def manuscript_path(self) -> Path:
        return self.root / "manuscript" / "current.md"

    @property
    def response_template_path(self) -> Path:
        return self.root / "inputs" / "responses.yaml"

    @contextmanager
    def workflow_lock(self, timeout: float = 2.0) -> Iterator[None]:
        lock = FileLock(str(self.root / "audit" / "workflow.lock"), timeout=timeout)
        with lock:
            yield

    def initialize(self, profile: ResearchProfile, default_config: Path) -> WorkflowState:
        if self.root.exists() and any(self.root.iterdir()):
            raise FileExistsError(f"Project directory is not empty: {self.root}")
        for directory in PROJECT_DIRS:
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(default_config, self.config_path)
        state = WorkflowState(project_id=str(uuid4()), profile=profile)
        self.save_state(state)
        self.write_json("evidence/registry.json", [])
        self.write_json("claims/registry.json", [])
        self.write_text("manuscript/current.md", "")
        self.write_text(
            "inputs/research_brief.md",
            "# Research brief\n\n"
            f"Topic: {profile.topic}\n\n"
            f"Domain: {profile.domain}\n\n"
            "Add any known objectives, setup, dataset, results, constraints, and target journal here. "
            "PaperForge will also inspect files placed in sources/, data/, and figures/.\n",
        )
        self.write_response_template(state)
        return state

    def load_state(self) -> WorkflowState:
        payload = self.read_json("audit/state.json")
        migrated, changed = self._migrate_state_payload(payload)
        state = WorkflowState.model_validate(migrated)
        for stage, status in list(state.stage_status.items()):
            if status == StageStatus.NEEDS_INPUT and not state.open_questions(stage):
                state.stage_status[stage] = StageStatus.PENDING
                changed = True
        if changed:
            self.save_state(state)
        return state

    def save_state(self, state: WorkflowState) -> None:
        state.schema_version = CURRENT_STATE_SCHEMA
        state.updated_at = utc_now()
        self.write_json("audit/state.json", state.model_dump(mode="json"))

    def load_evidence(self) -> list[EvidenceItem]:
        return [
            EvidenceItem.model_validate(item) for item in self.read_json("evidence/registry.json")
        ]

    def save_evidence(self, items: list[EvidenceItem]) -> None:
        self.write_json("evidence/registry.json", [item.model_dump(mode="json") for item in items])

    def load_claims(self) -> list[Claim]:
        return [Claim.model_validate(item) for item in self.read_json("claims/registry.json")]

    def save_claims(self, items: list[Claim]) -> None:
        self.write_json("claims/registry.json", [item.model_dump(mode="json") for item in items])

    def read_manuscript(self) -> str:
        return self.manuscript_path.read_text(encoding="utf-8")

    def save_manuscript_version(self, text: str, stage: str, state: WorkflowState) -> Path:
        state.manuscript_version += 1
        version = (
            self.root / "manuscript" / "versions" / (f"v{state.manuscript_version:03d}-{stage}.md")
        )
        self.write_text(str(version.relative_to(self.root)), text)
        self.write_text("manuscript/current.md", text)
        self.save_state(state)
        return version

    def write_stage_review(self, stage: str, run_number: int, payload: dict[str, Any]) -> Path:
        path = self.root / "reviews" / f"{stage}-{run_number:03d}.json"
        self.write_json(str(path.relative_to(self.root)), payload)
        return path

    def write_response_template(self, state: WorkflowState) -> Path:
        existing_payload: dict[str, Any] = {}
        existing_answers: dict[str, str] = {}
        if self.response_template_path.exists():
            existing_payload = self._read_response_payload(self.response_template_path)
            existing_answers = self._normalize_response_answers(existing_payload)

        # responses.yaml is user-owned input once it has been generated. Merge state into
        # the file instead of recreating it, so a run can never erase an answer that was
        # typed but has not yet been committed to the state ledger.
        answers = dict(existing_answers)
        for question in state.pending_questions:
            if not (question.is_open or question.answer):
                continue
            persisted_answer = (question.answer or "").strip()
            answers[question.id] = persisted_answer or existing_answers.get(question.id, "")

        instruction = (
            "Fill each answer once, save this file, then run: paperforge run <project>. "
            "Answers are imported automatically; paperforge answer-all remains available."
        )
        content = {
            **{
                key: value
                for key, value in existing_payload.items()
                if key not in {"instructions", "answers"}
            },
            "instructions": instruction,
            "answers": answers,
        }
        if existing_payload.get("instructions") == instruction and existing_answers == answers:
            return self.response_template_path

        self.response_template_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.response_template_path.with_suffix(".yaml.tmp")
        temporary.write_text(
            yaml.safe_dump(content, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        temporary.replace(self.response_template_path)
        return self.response_template_path

    def read_response_answers(self, path: Path | None = None) -> dict[str, str]:
        """Read a response file without mutating it or accepting nested values."""
        response_path = (path or self.response_template_path).resolve()
        payload = self._read_response_payload(response_path)
        return self._normalize_response_answers(payload)

    @staticmethod
    def _read_response_payload(path: Path) -> dict[str, Any]:
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise ValueError(
                f"Response file is invalid YAML and was left unchanged: {path}: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Response file must contain a YAML mapping: {path}")
        return payload

    @staticmethod
    def _normalize_response_answers(payload: dict[str, Any]) -> dict[str, str]:
        raw_answers = payload.get("answers", payload)
        if not isinstance(raw_answers, dict):
            raise ValueError("Response YAML must contain an 'answers' mapping.")

        answers: dict[str, str] = {}
        for identifier, value in raw_answers.items():
            clean_identifier = str(identifier).strip()
            if not clean_identifier:
                raise ValueError("Response YAML contains an empty question identifier.")
            if value is None:
                answers[clean_identifier] = ""
            elif isinstance(value, (str, int, float, bool)):
                answers[clean_identifier] = str(value).strip()
            else:
                raise ValueError(
                    f"Answer for {clean_identifier} must be a scalar value, not a nested object."
                )
        return answers

    def read_json(self, relative_path: str) -> Any:
        return json.loads((self.root / relative_path).read_text(encoding="utf-8"))

    def write_json(self, relative_path: str, value: Any) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)

    def write_text(self, relative_path: str, value: str) -> None:
        path = self.root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(value, encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def checksum(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def config_dict(self) -> dict[str, Any]:
        return yaml.safe_load(self.config_path.read_text(encoding="utf-8"))

    @staticmethod
    def _migrate_state_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        if int(payload.get("schema_version", 1)) >= CURRENT_STATE_SCHEMA:
            return payload, False

        migrated = dict(payload)
        questions = [dict(item) for item in migrated.get("pending_questions", [])]
        created_at = migrated.get("created_at") or utc_now().isoformat()
        updated_at = migrated.get("updated_at") or created_at
        migrated.setdefault("created_at", created_at)
        migrated.setdefault("updated_at", updated_at)
        used_numbers: set[int] = set()
        for index, question in enumerate(questions, start=1):
            raw_id = str(question.get("id", index))
            match = re.search(r"\d+", raw_id)
            number = int(match.group()) if match else index
            while number in used_numbers:
                number += 1
            used_numbers.add(number)
            question["id"] = f"Q-{number:03d}"
            question.setdefault("key", normalize_question_key(str(question.get("text", ""))))
            question.setdefault("stage", "intake")
            question["status"] = "answered" if question.get("answer") else "open"
            question.setdefault("created_at", created_at)
        migrated["pending_questions"] = questions

        max_question_number = max(
            (int(question["id"].split("-")[1]) for question in questions), default=0
        )
        migrated["next_question_number"] = max_question_number + 1
        open_questions = [question for question in questions if not question.get("answer")]
        answered_keys = [question["key"] for question in questions if question.get("answer")]
        migrated["closed_question_keys"] = answered_keys

        if questions and not migrated.get("question_rounds"):
            migrated["question_rounds"] = [
                {
                    "stage": "intake",
                    "number": 1,
                    "question_ids": [question["id"] for question in questions],
                    "created_at": created_at,
                    "closed_at": updated_at if not open_questions else None,
                }
            ]
        migrated["intake_closed"] = bool(questions) and not open_questions
        migrated.setdefault("source_fingerprint", None)
        migrated.setdefault("workflow_completed", False)
        migrated.setdefault("submission_ready", False)
        migrated.setdefault("completed_at", None)

        stage_status = dict(migrated.get("stage_status", {}))
        if migrated["intake_closed"] and stage_status.get("intake") == StageStatus.NEEDS_INPUT:
            stage_status["intake"] = StageStatus.PENDING
        migrated["stage_status"] = stage_status
        migrated["schema_version"] = CURRENT_STATE_SCHEMA
        return migrated, True
