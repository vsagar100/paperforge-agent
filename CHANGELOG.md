# Changelog

## 1.0.0

Complete workflow rewrite.

### Added

- one-command `paperforge write` entry point for a topic or synopsis;
- deterministic article-type policy with a review fallback when original evidence is absent;
- OpenAlex discovery and optional Crossref DOI verification;
- source-level literature matrix and reproducible search manifest;
- section-by-section manuscript drafting;
- evidence, methodology, results, writing, journal, and final review/revision gates;
- citation allowlist and deterministic IEEE-style rendering;
- unsupported-number, unknown-citation, placeholder, and truncation revision guards;
- confusion-matrix metrics and Wilson intervals from complete supplied counts;
- schema-3 state, input fingerprints, idempotent resume, and safe invalidation;
- Markdown, DOCX, BibTeX, CSV, JSON, review, and revision-history exports;
- non-destructive migration from v0.2.1.

### Removed

- model-generated intake questions;
- `answer` and `answer-all` workflow control;
- model-owned stage pass/fail decisions;
- exact-string patch application;
- automatic treatment of simulation, regulation, and baseline work as blockers.
