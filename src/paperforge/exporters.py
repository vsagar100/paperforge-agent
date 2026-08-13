from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from paperforge.domain import Severity, WorkflowState
from paperforge.storage import ProjectStore


@dataclass(slots=True)
class ExportReport:
    files: list[Path] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class OutputExporter:
    def __init__(self, store: ProjectStore) -> None:
        self.store = store

    def export(self, state: WorkflowState) -> ExportReport:
        report = ExportReport()
        manuscript = self.store.read_manuscript()
        markdown_path = self.store.root / "outputs" / "manuscript.md"
        self.store.write_text("outputs/manuscript.md", manuscript)
        report.files.append(markdown_path)

        latest_by_stage = {}
        for run in state.stage_runs:
            latest_by_stage[run.stage] = run
        unresolved = [
            finding.model_dump(mode="json")
            for run in latest_by_stage.values()
            for finding in run.findings
            if not finding.resolved
        ]
        summary = {
            "project_id": state.project_id,
            "topic": state.profile.topic,
            "workflow_completed": state.workflow_completed,
            "submission_ready": state.submission_ready,
            "manuscript_version": state.manuscript_version,
            "evidence_items": len(self.store.load_evidence()),
            "registered_claims": len(self.store.load_claims()),
            "stage_status": {stage: status.value for stage, status in state.stage_status.items()},
            "unresolved_findings": unresolved,
            "blocking_or_high_findings": [
                finding
                for finding in unresolved
                if finding["severity"] in {Severity.BLOCKING, Severity.HIGH}
            ],
            "answered_questions": [
                {
                    "id": question.id,
                    "key": question.key,
                    "question": question.text,
                    "answer": question.answer,
                }
                for question in state.pending_questions
                if question.answer
            ],
            "open_questions": [question.id for question in state.open_questions()],
            "dismissed_legacy_questions": [
                {"id": question.id, "key": question.key, "question": question.text}
                for question in state.pending_questions
                if question.status.value == "dismissed"
            ],
        }
        self.store.write_json("outputs/quality-report.json", summary)
        report.files.append(self.store.root / "outputs" / "quality-report.json")

        try:
            docx_path = self._write_docx(manuscript)
        except ImportError:
            report.warnings.append(
                'DOCX export skipped; install with: pip install -e ".[documents]"'
            )
        else:
            report.files.append(docx_path)
        return report

    def _write_docx(self, manuscript: str) -> Path:
        from docx import Document
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.oxml.ns import qn
        from docx.shared import Inches, Pt

        document = Document()
        section = document.sections[0]
        section.top_margin = Inches(1)
        section.bottom_margin = Inches(1)
        section.left_margin = Inches(1)
        section.right_margin = Inches(1)

        normal = document.styles["Normal"]
        normal.font.name = "Times New Roman"
        normal._element.rPr.rFonts.set(qn("w:eastAsia"), "Times New Roman")
        normal.font.size = Pt(12)

        for raw_line in manuscript.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            heading = re.match(r"^(#{1,3})\s+(.+)$", line)
            if heading:
                level = min(len(heading.group(1)), 3)
                paragraph = document.add_heading(heading.group(2), level=level)
                if level == 1:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                continue
            if line.startswith(("- ", "* ")):
                document.add_paragraph(line[2:], style="List Bullet")
                continue
            if re.match(r"^\d+[.)]\s+", line):
                text = re.sub(r"^\d+[.)]\s+", "", line)
                document.add_paragraph(text, style="List Number")
                continue
            paragraph = document.add_paragraph(line)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            paragraph.paragraph_format.space_after = Pt(6)

        output = self.store.root / "outputs" / "manuscript.docx"
        document.save(output)
        return output
