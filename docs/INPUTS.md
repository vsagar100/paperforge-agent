# Inputs and provenance

attach copies originals into inputs without overwriting attached files. Stable path-based
evidence IDs plus SHA256 track changes. Location, partial extraction and warnings are recorded.

| Format | Handling |
|---|---|
| TXT/MD/TEX/JSON/YAML/RIS/BIB | UTF-8 text; bibliography text is not automatically verified |
| CSV/TSV | Rows with locators |
| XLSX | Sheet/cell locations, formulas and cached values; no recalculation/macros |
| PDF | Page text; image-only pages request OCR text |
| DOCX/PPTX | Paragraph/slide/table text; complex drawings not interpreted |
| Images | Metadata and optional Tesseract OCR; no multimodal scientific interpretation |
| SVG | Safely extracted labels; no script execution/layout interpretation |
| MAT/NPY | Numeric arrays, old MATLAB/HDF5 v7.3; complex objects request exports |
| M/PY/R | Read as text, never executed |
| CDR/FIG/DWG | Retained; export SVG/PDF/PNG plus caption |
| SLX/MDL/MLX | Retained; export model description and CSV/MAT results |
| XLS/PPT/unknown | Retained with conversion/unsupported status |

Install documents/scientific extras for relevant adapters. OCR needs tesseract installed
separately and ingestion.ocr: true. OCR remains unverified. One failed file does not discard
independent successful extractions. Symlinks are rejected.

Default limits: 25 MB/file, 120,000 extracted characters/file, 20,000 rows, 200,000 cells,
100 MB expanded ZIP content and 200 PDF pages. Omitted data is reported. Model context
selects the first 20 input chunks and reports omitted counts; it does not claim exhaustive
dataset reading. Over-limit context fails explicitly. Supply focused validated exports/summaries
for large studies.

manifest.json maps input-relative paths to author_note, dataset, source, figure, code or artifact.
attach --role maintains it. Control metadata is not scientific claim evidence.

## Source DOI association

Create inputs/source-dois.json with real registered values:

~~~json
{"accessible-paper.pdf": "10.xxxx/actual-registered-doi"}
~~~

Crossref must resolve it. The author verifies the file really matches the DOI. This avoids
mistaking bibliography DOIs for the attached paper's own DOI. Extracted passages alone
are available from partial documents; journal indexing/peer review is not certified.

## Implemented analysis

Attach actual observed counts in a JSON file with --role dataset:

~~~json
{"confusion_matrix": {"tp": 80, "tn": 90, "fp": 10, "fn": 20}}
~~~

This is a format example, not research evidence. Counts must be nonnegative integers with
positive total. The calculator derives accuracy, precision, recall, specificity and F1,
marks undefined metrics, and reports Wilson 95% accuracy limits assuming independent
Bernoulli observations. Clustered/time-correlated observations may violate this assumption.
It does not infer AUC, significance, training or ablation. Other files are evidence rather
than automatically executed statistical/simulation pipelines.
