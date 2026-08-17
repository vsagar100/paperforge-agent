from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypeVar
from uuid import uuid4

import yaml
from filelock import FileLock
from pydantic import BaseModel

from paperforge.author_validation import (
    VALIDATION_RELATIVE_PATH,
    dump_author_validation,
    load_author_validation,
    validation_user_material_from_payload,
)
from paperforge.domain import (
    CURRENT_STATE_SCHEMA,
    AuthorValidationPackage,
    ClaimLedger,
    EvidenceCoverage,
    EvidenceItem,
    LiteratureSynthesis,
    ManuscriptOutline,
    PublicationProfile,
    ReferenceRecord,
    ResearchPlan,
    ResearchProfile,
    StageRecord,
    StageStatus,
    WorkflowState,
    utc_now,
)

PROJECT_DIRS = (
    "inputs",
    "sources",
    "data",
    "figures",
    "evidence",
    "literature",
    "planning",
    "author-actions",
    "manuscript/versions",
    "reviews",
    "audit",
    "outputs",
)

T = TypeVar("T", bound=BaseModel)


class ProjectStore:
    """Owns durable project state and all atomic filesystem transitions."""

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
    def evidence_path(self) -> Path:
        return self.root / "evidence" / "registry.json"

    @property
    def references_path(self) -> Path:
        return self.root / "literature" / "references.json"

    @property
    def plan_path(self) -> Path:
        return self.root / "planning" / "research-plan.json"

    @property
    def synthesis_path(self) -> Path:
        return self.root / "literature" / "synthesis.json"

    @property
    def outline_path(self) -> Path:
        return self.root / "planning" / "outline.json"

    @property
    def publication_profile_path(self) -> Path:
        return self.root / "planning" / "publication-profile.json"

    @property
    def claim_ledger_path(self) -> Path:
        return self.root / "evidence" / "claim-ledger.json"

    @property
    def evidence_coverage_path(self) -> Path:
        return self.root / "evidence" / "coverage.json"

    @property
    def author_validation_path(self) -> Path:
        return self.root / VALIDATION_RELATIVE_PATH

    @contextmanager
    def workflow_lock(self, timeout: float = 3.0) -> Iterator[None]:
        (self.root / "audit").mkdir(parents=True, exist_ok=True)
        with FileLock(str(self.root / "audit" / "workflow.lock"), timeout=timeout):
            yield

    def initialize(self, profile: ResearchProfile, default_config: Path) -> WorkflowState:
        if self.root.exists() and any(self.root.iterdir()):
            raise FileExistsError(f"Project directory is not empty: {self.root}")
        for directory in PROJECT_DIRS:
            (self.root / directory).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(default_config, self.config_path)
        state = WorkflowState(project_id=str(uuid4()), profile=profile)
        self.save_state(state)
        self.save_evidence([])
        self.save_references([])
        self.write_text("manuscript/current.md", "")
        self.write_text(
            "inputs/research_brief.md",
            "# Research input\n\n"
            f"## Topic\n\n{profile.topic}\n\n"
            + (
                f"## Synopsis\n\n{profile.synopsis.strip()}\n\n"
                if profile.synopsis and profile.synopsis.strip()
                else ""
            ),
        )
        return state

    def load_state(self) -> WorkflowState:
        payload = self.read_json("audit/state.json")
        if int(payload.get("schema_version", 1)) < CURRENT_STATE_SCHEMA:
            state = self._migrate_state(payload)
            self.save_state(state)
            return state
        state = WorkflowState.model_validate(payload)
        repaired = False
        for stage, status in list(state.stage_status.items()):
            if status == StageStatus.RUNNING:
                state.stage_status[stage] = StageStatus.PENDING
                repaired = True
        if repaired:
            state.current_stage = None
            self.save_state(state)
        return state

    def _migrate_state(self, payload: dict[str, Any]) -> WorkflowState:
        source_schema = int(payload.get("schema_version", 1))
        backup = self.root / "audit" / f"state.v{source_schema}.json"
        if not backup.exists():
            self.write_json(f"audit/state.v{source_schema}.json", payload)

        if source_schema in {3, 4}:
            profile = ResearchProfile.model_validate(payload.get("profile", {}))
            legacy_manuscript = self.read_manuscript() if self.manuscript_path.exists() else ""
            if legacy_manuscript.strip():
                legacy_version = "v1.0.0" if source_schema == 3 else "v2.0.0"
                legacy_path = self.root / "manuscript" / "versions" / f"legacy-{legacy_version}.md"
                if not legacy_path.exists():
                    self.write_text(
                        str(legacy_path.relative_to(self.root)),
                        legacy_manuscript,
                    )
            return WorkflowState(
                project_id=str(payload.get("project_id") or uuid4()),
                profile=profile,
                migration_notes=[
                    (
                        "Migrated from the v1 workflow to the publication-contract workflow."
                        if source_schema == 3
                        else "Migrated from PaperForge 2.0 to the research-first validation workflow."
                    ),
                    "Existing inputs and responses remain authoritative user evidence.",
                    "The prior manuscript was preserved and generated stages require a fresh build.",
                ],
                created_at=payload.get("created_at") or utc_now(),
            )

        legacy_profile = payload.get("profile", {})
        legacy_details: list[str] = []
        objectives = legacy_profile.get("objectives") or []
        if objectives:
            legacy_details.extend(
                ["## Legacy objectives", "", *[f"- {item}" for item in objectives]]
            )
        if contribution := legacy_profile.get("contribution"):
            legacy_details.extend(["", "## Legacy contribution", "", str(contribution)])
        if constraints := legacy_profile.get("constraints"):
            legacy_details.extend(
                [
                    "",
                    "## Legacy constraints",
                    "",
                    json.dumps(constraints, indent=2, ensure_ascii=False),
                ]
            )
        if legacy_details:
            legacy_input = self.root / "inputs" / "legacy-profile.md"
            if not legacy_input.exists():
                self.write_text(
                    "inputs/legacy-profile.md", "\n".join(legacy_details).strip() + "\n"
                )
        profile = ResearchProfile(
            topic=str(legacy_profile.get("topic") or "Untitled research project"),
            synopsis=legacy_profile.get("synopsis"),
            domain=str(legacy_profile.get("domain") or "engineering"),
            target_journal=legacy_profile.get("target_journal"),
            constraints=dict(legacy_profile.get("constraints") or {}),
        )
        legacy_manuscript = self.read_manuscript() if self.manuscript_path.exists() else ""
        if legacy_manuscript.strip():
            legacy_path = self.root / "manuscript" / "versions" / "legacy-v0.2.1.md"
            if not legacy_path.exists():
                self.write_text(
                    str(legacy_path.relative_to(self.root)),
                    legacy_manuscript,
                )
        return WorkflowState(
            project_id=str(payload.get("project_id") or uuid4()),
            profile=profile,
            migration_notes=[
                "Migrated from the v0.2 state model.",
                "Existing responses.yaml is treated as authoritative user evidence.",
                "The legacy manuscript was preserved and the v2 pipeline will create a fresh draft.",
            ],
            created_at=payload.get("created_at") or utc_now(),
        )

    def save_state(self, state: WorkflowState) -> None:
        state.schema_version = CURRENT_STATE_SCHEMA
        state.updated_at = utc_now()
        self.write_json("audit/state.json", state.model_dump(mode="json"))

    def record_stage(self, state: WorkflowState, record: StageRecord) -> None:
        state.stage_status[record.stage] = record.status
        state.stage_records[record.stage] = record
        state.run_history.append(record)
        state.current_stage = None
        self.save_state(state)
        attempt = sum(item.stage == record.stage for item in state.run_history)
        self.write_json(
            f"reviews/{record.stage}-{attempt:03d}.json",
            record.model_dump(mode="json"),
        )

    def reset_generated_state(self, state: WorkflowState, reason: str) -> None:
        if self.manuscript_path.exists() and self.read_manuscript().strip():
            self.save_manuscript(self.read_manuscript(), "pre-invalidation", state)
        state.stage_status.clear()
        state.stage_records.clear()
        state.current_stage = None
        state.workflow_completed = False
        state.submission_ready = False
        state.completed_at = None
        state.author_actions.clear()
        state.migration_notes.append(reason)
        self.write_text("manuscript/current.md", "")
        self.save_state(state)

    def load_evidence(self) -> list[EvidenceItem]:
        if not self.evidence_path.exists():
            return []
        payload = self.read_json("evidence/registry.json")
        legacy_kinds = {
            "user_statement": "user_fact",
            "experimental": "experimental_data",
            "source": "source_document",
            "standard": "source_document",
            "computed": "computed",
            "figure": "figure",
        }
        migrated = False
        normalized: list[dict[str, Any]] = []
        for raw_item in payload:
            item = dict(raw_item)
            kind = str(item.get("kind") or "")
            if kind in legacy_kinds and legacy_kinds[kind] != kind:
                item["kind"] = legacy_kinds[kind]
                item.setdefault("metadata", {})["migrated_from_v2_kind"] = kind
                migrated = True
            normalized.append(item)
        items = [EvidenceItem.model_validate(item) for item in normalized]
        if migrated:
            backup = self.root / "evidence" / "registry.v2.json"
            if not backup.exists():
                self.write_json("evidence/registry.v2.json", payload)
            self.save_evidence(items)
        return items

    def save_evidence(self, items: list[EvidenceItem]) -> None:
        self.write_json("evidence/registry.json", [item.model_dump(mode="json") for item in items])

    def load_references(self) -> list[ReferenceRecord]:
        if not self.references_path.exists():
            return []
        return [
            ReferenceRecord.model_validate(item)
            for item in self.read_json("literature/references.json")
        ]

    def save_references(self, items: list[ReferenceRecord]) -> None:
        self.write_json(
            "literature/references.json", [item.model_dump(mode="json") for item in items]
        )

    def save_plan(self, value: ResearchPlan) -> None:
        self.write_json("planning/research-plan.json", value.model_dump(mode="json"))

    def load_plan(self) -> ResearchPlan:
        return ResearchPlan.model_validate(self.read_json("planning/research-plan.json"))

    def save_synthesis(self, value: LiteratureSynthesis) -> None:
        self.write_json("literature/synthesis.json", value.model_dump(mode="json"))

    def load_synthesis(self) -> LiteratureSynthesis:
        return LiteratureSynthesis.model_validate(self.read_json("literature/synthesis.json"))

    def save_outline(self, value: ManuscriptOutline) -> None:
        self.write_json("planning/outline.json", value.model_dump(mode="json"))

    def load_outline(self) -> ManuscriptOutline:
        return ManuscriptOutline.model_validate(self.read_json("planning/outline.json"))

    def save_publication_profile(self, value: PublicationProfile) -> None:
        self.write_json("planning/publication-profile.json", value.model_dump(mode="json"))

    def load_publication_profile(self) -> PublicationProfile:
        return PublicationProfile.model_validate(
            self.read_json("planning/publication-profile.json")
        )

    def save_claim_ledger(self, value: ClaimLedger) -> None:
        self.write_json("evidence/claim-ledger.json", value.model_dump(mode="json"))

    def load_claim_ledger(self) -> ClaimLedger:
        return ClaimLedger.model_validate(self.read_json("evidence/claim-ledger.json"))

    def save_evidence_coverage(self, value: EvidenceCoverage) -> None:
        self.write_json("evidence/coverage.json", value.model_dump(mode="json"))

    def load_evidence_coverage(self) -> EvidenceCoverage:
        return EvidenceCoverage.model_validate(self.read_json("evidence/coverage.json"))

    def save_author_validation(self, value: AuthorValidationPackage) -> None:
        self.write_text(VALIDATION_RELATIVE_PATH, dump_author_validation(value))

    def load_author_validation(self) -> AuthorValidationPackage:
        return load_author_validation(self.author_validation_path)

    def read_manuscript(self) -> str:
        if not self.manuscript_path.exists():
            return ""
        return self.manuscript_path.read_text(encoding="utf-8")

    def save_manuscript(self, text: str, stage: str, state: WorkflowState) -> Path:
        clean = text.strip() + "\n"
        state.manuscript_version += 1
        safe_stage = "".join(
            character if character.isalnum() or character == "-" else "-" for character in stage
        )
        relative = f"manuscript/versions/v{state.manuscript_version:03d}-{safe_stage}.md"
        self.write_text(relative, clean)
        self.write_text("manuscript/current.md", clean)
        self.save_state(state)
        return self.root / relative

    def input_fingerprint(self, state: WorkflowState) -> str:
        digest = hashlib.sha256()
        profile = state.profile.model_dump(mode="json")
        profile.pop("resolved_paper_type", None)
        digest.update(json.dumps(profile, sort_keys=True).encode("utf-8"))
        for path in self.input_files():
            relative = path.relative_to(self.root).as_posix()
            digest.update(relative.encode("utf-8"))
            digest.update(self.checksum(path).encode("ascii"))
        validation_material = self.author_validation_user_material()
        if validation_material:
            digest.update(b"author-validation-user-decisions")
            digest.update(validation_material.encode("utf-8"))
        return digest.hexdigest()

    def author_validation_user_material(self) -> str:
        if not self.author_validation_path.exists():
            return ""
        try:
            payload = yaml.safe_load(self.author_validation_path.read_text(encoding="utf-8"))
        except yaml.YAMLError:
            # Invalid author-edited YAML must invalidate the prior run so prepare can report it.
            return self.checksum(self.author_validation_path)
        return validation_user_material_from_payload(payload)

    def config_fingerprint(self) -> str:
        return self.checksum(self.config_path)

    def input_files(self) -> list[Path]:
        paths: list[Path] = []
        for folder in ("inputs", "sources", "data", "figures"):
            root = self.root / folder
            if not root.exists():
                continue
            paths.extend(
                path
                for path in root.rglob("*")
                if path.is_file()
                and not any(part.startswith(".") for part in path.relative_to(root).parts)
            )
        return sorted(set(paths), key=lambda item: item.as_posix().casefold())

    def read_json(self, relative_path: str) -> Any:
        return json.loads((self.root / relative_path).read_text(encoding="utf-8"))

    def write_json(self, relative_path: str, value: Any) -> None:
        text = json.dumps(value, indent=2, ensure_ascii=False)
        self.write_text(relative_path, text + "\n")

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

    @staticmethod
    def text_fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
