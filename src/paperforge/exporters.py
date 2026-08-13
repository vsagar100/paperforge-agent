from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from paperforge.citations import build_bibtex, render_numbered_citations
from paperforge.domain import IssueDisposition, WorkflowState
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
        references = self.store.load_references()
        rendered, cited = render_numbered_citations(self.store.read_manuscript(), references)
        self.store.write_text("outputs/manuscript.md", rendered)
        report.files.append(self.store.root / "outputs" / "manuscript.md")

        self.store.write_text("outputs/references.bib", build_bibtex(cited))
        report.files.append(self.store.root / "outputs" / "references.bib")

        self.store.write_text("outputs/literature-matrix.csv", self._literature_matrix())
        report.files.append(self.store.root / "outputs" / "literature-matrix.csv")

        quality = self._quality_payload(state, rendered, cited)
        self.store.write_json("outputs/quality-report.json", quality)
        self.store.write_text("outputs/review-report.md", self._review_report(quality))
        report.files.extend(
            [
                self.store.root / "outputs" / "quality-report.json",
                self.store.root / "outputs" / "review-report.md",
            ]
        )

        self.store.write_text("outputs/revision-history.md", self._revision_history(state))
        report.files.append(self.store.root / "outputs" / "revision-history.md")

        try:
            docx = self._export_docx(rendered)
        except ImportError:
            report.warnings.append(
                "DOCX export skipped; install PaperForge with the [documents] extra."
            )
        else:
            report.files.append(docx)
        return report

    def _literature_matrix(self) -> str:
        references = self.store.load_references()
        try:
            synthesis = self.store.load_synthesis()
            notes = {note.reference_id: note for note in synthesis.source_notes}
        except (FileNotFoundError, ValueError):
            notes = {}
        output = io.StringIO()
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(
            [
                "Reference ID",
                "Authors",
                "Year",
                "Title",
                "Venue",
                "Volume",
                "Issue",
                "Pages",
                "DOI",
                "Method",
                "Dataset or material",
                "Key finding",
                "Limitation",
                "Relevance",
                "Verification",
            ]
        )
        for reference in references:
            note = notes.get(reference.id)
            writer.writerow(
                [
                    reference.id,
                    "; ".join(reference.authors),
                    reference.year or "",
                    reference.title,
                    reference.venue or "",
                    reference.volume or "",
                    reference.issue or "",
                    reference.pages or "",
                    reference.doi or "",
                    note.method if note else "Not reported in the available metadata.",
                    note.dataset_or_material if note else "Not reported in the available metadata.",
                    note.key_finding if note else "Not reported in the available metadata.",
                    note.limitation if note else "Not reported in the available metadata.",
                    note.relevance if note else "",
                    "; ".join(reference.verification_sources),
                ]
            )
        return output.getvalue()

    def _quality_payload(
        self,
        state: WorkflowState,
        rendered_manuscript: str,
        cited,
    ) -> dict:
        records = {
            stage: record.model_dump(mode="json") for stage, record in state.stage_records.items()
        }
        unresolved = [
            issue.model_dump(mode="json")
            for record in state.stage_records.values()
            for issue in record.issues
            if not issue.resolved
        ]
        blockers = [
            issue
            for issue in unresolved
            if issue.get("disposition") == IssueDisposition.INTEGRITY_BLOCKER
        ]
        return {
            "schema_version": 1,
            "project_id": state.project_id,
            "paper_type": state.profile.resolved_paper_type,
            "target_journal": state.profile.target_journal,
            "workflow_completed": state.workflow_completed,
            "submission_ready": state.submission_ready,
            "readiness_label": (
                "submission_candidate" if state.submission_ready else "author_action_required"
            ),
            "word_count": len(re.findall(r"\b[\w'-]+\b", rendered_manuscript)),
            "cited_sources": len(cited),
            "verified_cited_sources": sum(reference.verified for reference in cited),
            "integrity_blockers": blockers,
            "unresolved_review_items": unresolved,
            "author_actions": [action.model_dump(mode="json") for action in state.author_actions],
            "stage_records": records,
            "disclaimer": (
                "Submission-ready means the implemented evidence and consistency gates passed. "
                "It does not guarantee journal acceptance and does not replace author verification."
            ),
        }

    @staticmethod
    def _review_report(quality: dict) -> str:
        lines = [
            "# PaperForge Review Report",
            "",
            f"- Readiness: **{quality['readiness_label']}**",
            f"- Manuscript words: {quality['word_count']}",
            f"- Cited sources: {quality['cited_sources']}",
            f"- Verified cited sources: {quality['verified_cited_sources']}",
            "",
            "## Integrity blockers",
            "",
        ]
        blockers = quality["integrity_blockers"]
        if blockers:
            lines.extend(
                f"- **{issue['code']}** ({issue.get('section') or 'Manuscript'}): "
                f"{issue['description']}"
                for issue in blockers
            )
        else:
            lines.append("- None detected by the implemented gates.")
        lines.extend(["", "## Author actions and recommendations", ""])
        items = [issue for issue in quality["unresolved_review_items"] if issue not in blockers]
        if items:
            lines.extend(
                f"- **{issue['code']}** ({issue.get('section') or 'Manuscript'}): "
                f"{issue['required_change']}"
                for issue in items
            )
        else:
            lines.append("- No unresolved review item.")
        lines.extend(["", "## Interpretation", "", quality["disclaimer"], ""])
        return "\n".join(lines)

    @staticmethod
    def _revision_history(state: WorkflowState) -> str:
        lines = ["# Revision History", ""]
        for record in state.run_history:
            lines.extend(
                [
                    f"## {record.stage} — {record.status.value}",
                    "",
                    f"- Completed: {record.completed_at or 'not recorded'}",
                    f"- Score: {record.score:.2f}",
                    f"- Model: {record.model or 'deterministic'}",
                ]
            )
            if record.changes:
                lines.append("- Changes:")
                lines.extend(f"  - {change}" for change in record.changes)
            if record.issues:
                lines.append(f"- Unresolved issues recorded: {len(record.issues)}")
            lines.append("")
        return "\n".join(lines)

    def _export_docx(self, manuscript: str) -> Path:
        try:
            from docx import Document
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.shared import Inches, Pt
        except ImportError as exc:
            raise ImportError from exc

        document = Document()
        section = document.sections[0]
        section.top_margin = Inches(0.8)
        section.bottom_margin = Inches(0.8)
        section.left_margin = Inches(0.9)
        section.right_margin = Inches(0.9)
        styles = document.styles
        styles["Normal"].font.name = "Times New Roman"
        styles["Normal"].font.size = Pt(11)
        styles["Normal"].paragraph_format.line_spacing = 1.15
        lines = manuscript.splitlines()
        index = 0
        while index < len(lines):
            line = lines[index]
            stripped = line.strip()
            if not stripped:
                index += 1
                continue
            if (
                stripped.startswith("|")
                and index + 1 < len(lines)
                and self._is_table_separator(lines[index + 1])
            ):
                headers = self._table_cells(stripped)
                rows: list[list[str]] = []
                index += 2
                while index < len(lines) and lines[index].strip().startswith("|"):
                    rows.append(self._table_cells(lines[index]))
                    index += 1
                table = document.add_table(rows=1, cols=len(headers))
                table.style = "Table Grid"
                for column, value in enumerate(headers):
                    paragraph = table.rows[0].cells[column].paragraphs[0]
                    run = paragraph.add_run(value)
                    run.bold = True
                for values in rows:
                    cells = table.add_row().cells
                    for column, value in enumerate(values[: len(cells)]):
                        self._add_markdown_runs(cells[column].paragraphs[0], value)
                continue
            heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
            if heading:
                level = min(len(heading.group(1)), 3)
                paragraph = document.add_heading("", level=level)
                self._add_markdown_runs(paragraph, heading.group(2))
                if level == 1:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                index += 1
                continue
            numbered = re.match(r"^\d+\.\s+(.+)$", stripped)
            if numbered:
                paragraph = document.add_paragraph(style="List Number")
                self._add_markdown_runs(paragraph, numbered.group(1))
                index += 1
                continue
            bullet = re.match(r"^[-*]\s+(.+)$", stripped)
            if bullet:
                paragraph = document.add_paragraph(style="List Bullet")
                self._add_markdown_runs(paragraph, bullet.group(1))
                index += 1
                continue
            paragraph = document.add_paragraph(stripped)
            paragraph.clear()
            self._add_markdown_runs(paragraph, stripped)
            paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
            paragraph.paragraph_format.space_after = Pt(6)
            index += 1
        output = self.store.root / "outputs" / "manuscript.docx"
        document.save(output)
        return output

    @staticmethod
    def _add_markdown_runs(paragraph, value: str) -> None:
        for token in re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", value):
            if not token:
                continue
            if token.startswith("**") and token.endswith("**"):
                run = paragraph.add_run(token[2:-2])
                run.bold = True
            elif token.startswith("*") and token.endswith("*"):
                run = paragraph.add_run(token[1:-1])
                run.italic = True
            else:
                paragraph.add_run(token)

    @staticmethod
    def _table_cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    @classmethod
    def _is_table_separator(cls, line: str) -> bool:
        cells = cls._table_cells(line)
        return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells)
