from __future__ import annotations

import csv
import hashlib
import io
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from paperforge.config import IngestionConfig
from paperforge.domain import EvidenceItem, EvidenceKind
from paperforge.storage import ProjectStore

TEXT_EXTENSIONS = {".txt", ".md", ".rst", ".tex", ".bib", ".ris", ".yaml", ".yml"}


@dataclass(slots=True)
class IngestionReport:
    discovered: int = 0
    extracted: int = 0
    unchanged: int = 0
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    fingerprint: str = ""


class DocumentIngestor:
    """Extracts local project evidence with stable IDs and checksum provenance."""

    def __init__(self, store: ProjectStore, config: IngestionConfig) -> None:
        self.store = store
        self.config = config

    def refresh(self) -> IngestionReport:
        report = IngestionReport()
        if not self.config.enabled:
            return report

        paths = self._discover_files()
        report.discovered = len(paths)
        existing = self.store.load_evidence()
        manual = [item for item in existing if not item.metadata.get("auto_ingested")]
        previous = {
            item.source_path: item
            for item in existing
            if item.metadata.get("auto_ingested") and item.source_path
        }
        ingested: list[EvidenceItem] = []
        fingerprint = hashlib.sha256()

        for path in paths:
            relative = path.relative_to(self.store.root).as_posix()
            if path.stat().st_size > self.config.max_file_bytes:
                report.skipped += 1
                report.warnings.append(f"Skipped oversized input: {relative}")
                continue
            checksum = self.store.checksum(path)
            fingerprint.update(relative.encode("utf-8"))
            fingerprint.update(checksum.encode("ascii"))
            old = previous.get(relative)
            if old and old.checksum == checksum:
                ingested.append(old)
                report.unchanged += 1
                continue
            try:
                content, locator = self._extract(path)
            except (OSError, ValueError, RuntimeError) as exc:
                report.skipped += 1
                report.warnings.append(f"Could not extract {relative}: {exc}")
                continue
            truncated = len(content) > self.config.max_chars_per_document
            content = content[: self.config.max_chars_per_document]
            ingested.append(
                EvidenceItem(
                    id=self._evidence_id(relative),
                    kind=self._kind_for(relative, path.suffix.casefold()),
                    title=path.name,
                    source_path=relative,
                    locator=locator,
                    content=content or f"File supplied: {relative}",
                    verified=True,
                    checksum=checksum,
                    metadata={
                        "auto_ingested": True,
                        "verification": "local_checksum_and_locator",
                        "size_bytes": path.stat().st_size,
                        "extension": path.suffix.casefold(),
                        "truncated": truncated,
                    },
                )
            )
            report.extracted += 1

        self.store.save_evidence(manual + ingested)
        report.fingerprint = fingerprint.hexdigest()
        return report

    def _discover_files(self) -> list[Path]:
        paths: list[Path] = []
        excluded_names = {"responses.yaml"}
        for folder in self.config.folders:
            root = self.store.root / folder
            if not root.exists():
                continue
            paths.extend(
                path
                for path in root.rglob("*")
                if path.is_file()
                and path.name not in excluded_names
                and not any(part.startswith(".") for part in path.relative_to(root).parts)
            )
        return sorted(set(paths), key=lambda path: path.as_posix().casefold())

    def _extract(self, path: Path) -> tuple[str, str]:
        suffix = path.suffix.casefold()
        extractors: dict[str, Callable[[Path], tuple[str, str]]] = {
            ".json": self._extract_json,
            ".csv": self._extract_delimited,
            ".tsv": self._extract_delimited,
            ".pdf": self._extract_pdf,
            ".docx": self._extract_docx,
            ".xlsx": self._extract_xlsx,
        }
        if suffix in TEXT_EXTENSIONS:
            return path.read_text(encoding="utf-8", errors="replace"), "full file"
        if suffix in extractors:
            return extractors[suffix](path)
        if suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".svg"}:
            return (
                f"Figure supplied at {path.name}. Pixel/content interpretation is not performed "
                "automatically; use the caption or accompanying data as scientific evidence.",
                "file metadata",
            )
        raise ValueError(f"unsupported file type '{suffix or '[none]'}'")

    @staticmethod
    def _extract_json(path: Path) -> tuple[str, str]:
        value = json.loads(path.read_text(encoding="utf-8"))
        return json.dumps(value, indent=2, ensure_ascii=False), "JSON document"

    @staticmethod
    def _extract_delimited(path: Path) -> tuple[str, str]:
        delimiter = "\t" if path.suffix.casefold() == ".tsv" else ","
        output = io.StringIO()
        with path.open(encoding="utf-8-sig", errors="replace", newline="") as source:
            reader = csv.reader(source, delimiter=delimiter)
            writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
            for row_number, row in enumerate(reader, start=1):
                writer.writerow(row)
                if row_number >= 10_000:
                    break
        return output.getvalue(), "rows 1-10000"

    @staticmethod
    def _extract_pdf(path: Path) -> tuple[str, str]:
        try:
            import fitz
        except ImportError as exc:
            raise RuntimeError("install PaperForge with [documents] for PDF extraction") from exc
        chunks: list[str] = []
        with fitz.open(path) as document:
            page_count = len(document)
            for index, page in enumerate(document, start=1):
                chunks.append(f"\n--- Page {index} ---\n{page.get_text('text')}")
        return "".join(chunks), f"pages 1-{page_count}"

    @staticmethod
    def _extract_docx(path: Path) -> tuple[str, str]:
        try:
            from docx import Document
        except ImportError as exc:
            raise RuntimeError("install PaperForge with [documents] for DOCX extraction") from exc
        document = Document(path)
        chunks = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        for table_number, table in enumerate(document.tables, start=1):
            chunks.append(f"\n[Table {table_number}]")
            chunks.extend(" | ".join(cell.text for cell in row.cells) for row in table.rows)
        return "\n".join(chunks), "paragraphs and tables"

    @staticmethod
    def _extract_xlsx(path: Path) -> tuple[str, str]:
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise RuntimeError("install PaperForge with [documents] for XLSX extraction") from exc
        workbook = load_workbook(path, read_only=True, data_only=True)
        chunks: list[str] = []
        try:
            for sheet in workbook.worksheets:
                chunks.append(f"\n--- Sheet: {sheet.title} ---")
                for row_number, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                    chunks.append("\t".join("" if value is None else str(value) for value in row))
                    if row_number >= 10_000:
                        break
        finally:
            workbook.close()
        return "\n".join(chunks), "workbook values, up to 10000 rows per sheet"

    @staticmethod
    def _evidence_id(relative_path: str) -> str:
        digest = hashlib.sha256(relative_path.casefold().encode("utf-8"))
        return f"EV-FILE-{digest.hexdigest()[:12].upper()}"

    @staticmethod
    def _kind_for(relative_path: str, suffix: str) -> EvidenceKind:
        top_level = relative_path.split("/", 1)[0]
        if top_level == "inputs":
            return EvidenceKind.USER_STATEMENT
        if top_level == "data" or suffix in {".csv", ".tsv", ".xlsx"}:
            return EvidenceKind.EXPERIMENTAL
        if top_level == "figures":
            return EvidenceKind.FIGURE
        return EvidenceKind.SOURCE
