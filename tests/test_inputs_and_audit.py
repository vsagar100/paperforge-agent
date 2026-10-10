import json

import pytest

from paperforge.analytics import analyze
from paperforge.audit import audit_sections
from paperforge.config import Ingestion
from paperforge.export import assemble, svg_diagram
from paperforge.inputs import Ingestor, input_signature
from paperforge.schemas import InputItem, Paragraph, Section, Source


def test_mixed_files_preserve_locators_and_report_native_conversion(store):
    from openpyxl import Workbook
    from PIL import Image

    store.write("inputs/notes.txt", "Author-supplied protocol details.")
    store.write("inputs/design.cdr", "native placeholder for a conversion test")
    store.write("inputs/model.m", "disp('must not run');")
    workbook = Workbook()
    workbook.active["A1"] = "Temperature"
    workbook.active["B2"] = "=1+2"
    workbook.save(store.root / "inputs" / "data.xlsx")
    Image.new("RGB", (20, 10)).save(store.root / "inputs" / "setup.png")
    items = {item.path: item for item in Ingestor(store, Ingestion()).run()}
    assert items["inputs/design.cdr"].status == "conversion_required"
    assert items["inputs/model.m"].role == "code"
    assert items["inputs/setup.png"].status == "partial"
    assert items["inputs/setup.png"].metadata["width"] == 20
    cells = items["inputs/data.xlsx"].chunks
    assert any(c["locator"] == "Sheet!B2" and "cached value" in c["text"] for c in cells)
    assert items["inputs/notes.txt"].metadata["verification"].startswith("file provenance")


def test_changed_inputs_are_detected_and_ids_stay_stable(store):
    store.write("inputs/results.csv", "accuracy,value\ntrial,0.8\n")
    first_signature = input_signature(store)
    before = Ingestor(store, Ingestion()).run()[0]
    store.write("outputs/generated.txt", "not an input")
    assert input_signature(store) == first_signature
    store.write("inputs/results.csv", "accuracy,value\ntrial,0.9\n")
    after = Ingestor(store, Ingestion()).run()[0]
    assert before.id == after.id
    assert before.sha256 != after.sha256
    assert input_signature(store) != first_signature


def test_bad_file_does_not_discard_good_file_and_symlink_is_rejected(store, tmp_path):
    store.write("inputs/good.txt", "scientific note")
    store.write("inputs/bad.xlsx", "not a ZIP file")
    items = Ingestor(store, Ingestion()).run()
    assert {item.status for item in items} == {"extracted", "failed"}
    outside = tmp_path / "outside.txt"
    outside.write_text("external")
    (store.root / "inputs" / "link.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        input_signature(store)


def test_mat_old_and_hdf5_versions_extract_numeric_variables(store):
    import h5py
    import numpy as np
    from scipy.io import savemat

    savemat(store.root / "inputs" / "old.mat", {"temperature": np.array([20.0, 21.0])})
    with h5py.File(store.root / "inputs" / "new.mat", "w") as file:
        file["voltage"] = np.array([230.0, 231.0])
    items = Ingestor(store, Ingestion()).run()
    assert len(items) == 2
    assert all(item.status == "extracted" for item in items)
    assert any("230." in c["text"] for item in items for c in item.chunks)


def test_confusion_metrics_are_computed_and_undefined_metrics_are_explicit():
    item = InputItem(
        id="EV-data",
        path="inputs/data.json",
        sha256="hash",
        role="dataset",
        status="extracted",
        chunks=[
            {
                "locator": "full file",
                "text": json.dumps({"confusion_matrix": {"tp": 8, "tn": 8, "fp": 2, "fn": 2}}),
            }
        ],
    )
    result = analyze([item])[0]
    assert result["metrics"]["accuracy"] == pytest.approx(0.8)
    assert result["metrics"]["f1"] == pytest.approx(0.8)
    assert result["accuracy_wilson_95"][0] < 0.8 < result["accuracy_wilson_95"][1]
    item.chunks[0]["text"] = '{"confusion_matrix":{"tp":0,"tn":8,"fp":0,"fn":0}}'
    result = analyze([item])[0]
    assert result["metrics"]["precision"] is None
    assert "precision" in result["undefined_metrics"]
    item.chunks[0]["text"] = '{"confusion_matrix":{"tp":-1,"tn":8,"fp":0,"fn":0}}'
    with pytest.raises(ValueError):
        analyze([item])


def source(identifier, abstract):
    return Source(
        id=identifier,
        title="A verified record",
        url="https://example.org",
        year=2025,
        abstract=abstract,
        metadata_verified=True,
        authors=["A. Author"],
        access_level="abstract",
    )


def test_source_existence_is_not_claim_support_and_invented_numbers_fail():
    reference = source("SRC-1", "The method was evaluated in a controlled setting.")
    section = Section(
        title="Results",
        paragraphs=[
            Paragraph(
                text="The system achieved 99.9% accuracy.",
                kind="study",
                evidence_ids=["EV-1"],
                supporting_quotes={"EV-1": "accuracy=0.8"},
            ),
            Paragraph(
                text="An invented source supports this claim.",
                kind="literature",
                source_ids=["SRC-no"],
                supporting_quotes={"SRC-no": "claim"},
            ),
        ],
    )
    evidence = InputItem(
        id="EV-1",
        path="inputs/results.txt",
        sha256="hash",
        role="dataset",
        status="extracted",
        chunks=[{"locator": "line 1", "text": "accuracy=0.8"}],
    )
    issues = audit_sections([section], [reference], [evidence], [])
    assert any("unsupported percentage" in i.description for i in issues)
    assert any("unknown source" in i.description for i in issues)
    assert any(i.needs_author for i in issues)


def test_citations_number_first_appearance_after_abstract_reordering():
    refs = [source("A", "a"), source("B", "b")]
    sections = [
        Section(
            title="Introduction",
            paragraphs=[
                Paragraph(text="First substantive claim.", kind="literature", source_ids=["B"])
            ],
        ),
        Section(
            title="Related Work",
            paragraphs=[Paragraph(text="Second claim.", kind="literature", source_ids=["A", "B"])],
        ),
        Section(
            title="Abstract", paragraphs=[Paragraph(text="A bounded abstract.", kind="disclosure")]
        ),
    ]
    text, cited = assemble("Paper title", sections, refs)
    assert cited == ["B", "A"]
    assert text.index("## Abstract") < text.index("## Introduction")
    assert "Second claim. [2], [1]" in text
    assert text.count("## References") == 1


def test_diagram_escapes_labels_and_rejects_bad_edges():
    diagram = svg_diagram(["Input <data>", "Evaluation"], [(0, 1)], "Proposed method")
    assert "&lt;data&gt;" in diagram
    with pytest.raises(ValueError):
        svg_diagram(["Input", "Output"], [(0, 9)], "Bad")
