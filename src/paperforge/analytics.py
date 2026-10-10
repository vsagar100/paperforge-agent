from __future__ import annotations

import json
import math

from paperforge.schemas import InputItem
from paperforge.store import fingerprint


def analyze(items: list[InputItem]) -> list[dict]:
    """Whitelisted calculation only; no invented experiments or execution of uploaded code."""
    results = []
    for item in items:
        if item.role not in {"dataset", "author_note"} or not item.path.endswith(".json"):
            continue
        try:
            payload = json.loads("".join(chunk["text"] for chunk in item.chunks))
        except ValueError:
            continue
        if not isinstance(payload, dict) or "confusion_matrix" not in payload:
            continue
        matrix = payload["confusion_matrix"]
        if not isinstance(matrix, dict) or set(matrix) != {"tp", "tn", "fp", "fn"}:
            raise ValueError("confusion_matrix requires exactly tp, tn, fp and fn")
        if any(type(value) is not int or value < 0 for value in matrix.values()):
            raise ValueError("Confusion-matrix values must be nonnegative integer counts")
        tp, tn, fp, fn = (matrix[key] for key in ("tp", "tn", "fp", "fn"))
        total = tp + tn + fp + fn
        if total == 0:
            raise ValueError("Confusion matrix must contain observations")

        def divide(numerator, denominator):
            return numerator / denominator if denominator else None

        metrics = {
            "sample_count": total,
            "accuracy": (tp + tn) / total,
            "precision": divide(tp, tp + fp),
            "recall": divide(tp, tp + fn),
            "specificity": divide(tn, tn + fp),
            "f1": divide(2 * tp, 2 * tp + fp + fn),
        }
        # Wilson interval: independent Bernoulli observations are an assumption, not inferred.
        z, p = 1.959963984540054, metrics["accuracy"]
        centre = (p + z * z / (2 * total)) / (1 + z * z / total)
        half = (
            z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / (1 + z * z / total)
        )
        results.append(
            {
                "id": "RESULT-" + fingerprint([item.sha256, "confusion_matrix_v1"])[:12],
                "input_id": item.id,
                "input_sha256": item.sha256,
                "calculator": "confusion_matrix_v1",
                "matrix": matrix,
                "metrics": metrics,
                "accuracy_wilson_95": [centre - half, centre + half],
                "assumptions": [
                    "Counts supplied by author; provenance does not establish experimental validity",
                    "Wilson interval assumes independent Bernoulli observations; correlated samples require another analysis",
                ],
                "undefined_metrics": [key for key, value in metrics.items() if value is None],
            }
        )
    return results
