from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass

from paperforge.domain import EvidenceItem, EvidenceKind


@dataclass(frozen=True, slots=True)
class ConfusionMatrix:
    true_positive: int
    true_negative: int
    false_positive: int
    false_negative: int

    @property
    def total(self) -> int:
        return self.true_positive + self.true_negative + self.false_positive + self.false_negative


class StatisticsDeriver:
    """Create transparent computed evidence only when all required counts are supplied."""

    @classmethod
    def derive(cls, evidence: list[EvidenceItem]) -> list[EvidenceItem]:
        matrix = cls._find_confusion_matrix(evidence)
        retained = [
            item
            for item in evidence
            if not (
                item.kind == EvidenceKind.COMPUTED
                and item.metadata.get("calculator") == "confusion_matrix"
            )
        ]
        if matrix is None or matrix.total == 0:
            return retained
        source_ids = [
            item.id for item in evidence if cls._parse_confusion_matrix(item.content) is not None
        ]
        values = cls._metrics(matrix)
        lines = [
            "Deterministic metrics derived from the supplied binary confusion matrix.",
            f"TP={matrix.true_positive}, TN={matrix.true_negative}, "
            f"FP={matrix.false_positive}, FN={matrix.false_negative}, N={matrix.total}.",
        ]
        for name, value in values.items():
            lines.append(f"{name}: {value:.6f} ({value * 100:.3f}%).")
        accuracy_low, accuracy_high = cls._wilson(
            matrix.true_positive + matrix.true_negative, matrix.total
        )
        recall_low, recall_high = cls._wilson(
            matrix.true_positive, matrix.true_positive + matrix.false_negative
        )
        specificity_low, specificity_high = cls._wilson(
            matrix.true_negative, matrix.true_negative + matrix.false_positive
        )
        lines.extend(
            [
                f"Accuracy 95% Wilson interval: {accuracy_low:.6f} to {accuracy_high:.6f}.",
                f"Recall 95% Wilson interval: {recall_low:.6f} to {recall_high:.6f}.",
                f"Specificity 95% Wilson interval: {specificity_low:.6f} to {specificity_high:.6f}.",
            ]
        )
        retained.append(
            EvidenceItem(
                id="EV-COMPUTED-CONFUSION-METRICS",
                kind=EvidenceKind.COMPUTED,
                title="Deterministic confusion-matrix metrics",
                content="\n".join(lines),
                locator="calculated from supplied TP/TN/FP/FN counts",
                verified=True,
                metadata={
                    "generated": True,
                    "calculator": "confusion_matrix",
                    "source_evidence_ids": source_ids,
                    "formula_version": 1,
                },
            )
        )
        return retained

    @classmethod
    def _find_confusion_matrix(cls, evidence: list[EvidenceItem]) -> ConfusionMatrix | None:
        for item in evidence:
            if matrix := cls._parse_confusion_matrix(item.content):
                return matrix
        return None

    @classmethod
    def _parse_confusion_matrix(cls, text: str) -> ConfusionMatrix | None:
        aliases = {
            "true_positive": ("TP", "true positive", "true positives"),
            "true_negative": ("TN", "true negative", "true negatives"),
            "false_positive": ("FP", "false positive", "false positives"),
            "false_negative": ("FN", "false negative", "false negatives"),
        }
        found: dict[str, int] = {}
        for field, names in aliases.items():
            alternatives = "|".join(re.escape(name) for name in names)
            match = re.search(
                rf"(?i)\b(?:{alternatives})\b\s*(?:=|:|is)\s*(\d+)",
                text,
            )
            if match:
                found[field] = int(match.group(1))
        if len(found) == 4:
            return ConfusionMatrix(**found)

        lines = [line for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines[:-1]):
            try:
                header = [cell.strip().casefold() for cell in next(csv.reader([line]))]
                values = [cell.strip() for cell in next(csv.reader([lines[index + 1]]))]
            except (csv.Error, StopIteration):
                continue
            normalized = {
                "tp": "true_positive",
                "tn": "true_negative",
                "fp": "false_positive",
                "fn": "false_negative",
            }
            if not set(normalized).issubset(header) or len(values) < len(header):
                continue
            try:
                data = {
                    normalized[key]: int(float(values[header.index(key)])) for key in normalized
                }
            except (ValueError, IndexError):
                continue
            return ConfusionMatrix(**data)
        return None

    @staticmethod
    def _metrics(matrix: ConfusionMatrix) -> dict[str, float]:
        tp, tn, fp, fn = (
            matrix.true_positive,
            matrix.true_negative,
            matrix.false_positive,
            matrix.false_negative,
        )

        def divide(numerator: float, denominator: float) -> float:
            return numerator / denominator if denominator else 0.0

        accuracy = divide(tp + tn, matrix.total)
        precision = divide(tp, tp + fp)
        recall = divide(tp, tp + fn)
        specificity = divide(tn, tn + fp)
        f1 = divide(2 * precision * recall, precision + recall)
        denominator = math.sqrt((tp + fp) * (tp + fn) * (tn + fp) * (tn + fn))
        mcc = divide(tp * tn - fp * fn, denominator)
        return {
            "accuracy": accuracy,
            "precision": precision,
            "recall": recall,
            "specificity": specificity,
            "f1_score": f1,
            "false_positive_rate": 1 - specificity,
            "false_negative_rate": 1 - recall,
            "matthews_correlation_coefficient": mcc,
        }

    @staticmethod
    def _wilson(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
        if total <= 0:
            return 0.0, 0.0
        proportion = successes / total
        denominator = 1 + z * z / total
        centre = proportion + z * z / (2 * total)
        margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        return (centre - margin) / denominator, (centre + margin) / denominator
