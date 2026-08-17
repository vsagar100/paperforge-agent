# Changelog

## 2.1.0

Research-first author-validation release.

### Added

- an 18th `author_validation` stage after literature synthesis;
- one topic-applicable `author-actions/validation.yaml` with known facts, reporting relevance,
  verified-source context, stable decisions, and a human-readable companion report;
- ingestion and fingerprinting for resolved author decisions only;
- `research_then_validate` default and an explicit `strict_pre_draft` compatibility mode;
- author-validation summary in exported reports and submission checklist;
- regressions proving the supplied UAV case reaches DOCX export without fabricating flight,
  annotation, calibration, or uncertainty details.

### Changed

- evidence gaps are author actions instead of integrity blockers in the default workflow;
- incomplete original studies continue through research, drafting, seven review passes, and export;
- pending generated validation items do not invalidate or rerun a completed workflow;
- literature-derived practice is available to the question builder and manuscript discussion but is
  prohibited from becoming study-specific evidence;
- original-research section canonicalization prevents simultaneous `Methodology` and
  `Materials and Methods` sections;
- configuration and state schemas advance to 5; PaperForge 2.0 artifacts migrate non-destructively.

### Integrity boundary retained

- fabricated/unknown citations, unsupported numbers or declarations, contradictory facts, missing
  substantive sections, unresolved placeholders, and unsafe/truncated revisions still block.

## 2.0.0

Publication-contract and evidence-integrity rewrite.

### Added

- checked DJES research/review profiles and explicit Scopus/Web of Science indexing context;
- schema-4 publication profile, claim ledger, evidence coverage, and display-item models;
- pre-draft evidence sufficiency gate with author action prompts;
- source-appraisal and discussion-review stages in a 17-stage pipeline;
- structured section batches for drafting and exact-section revision;
- section order/depth, duplicate/injection, abstract, keyword, declaration, table, discussion, and
  manuscript-length gates;
- exact numeric provenance against study evidence or the cited source record;
- publication profile, evidence coverage, display-item plan, and submission checklist exports;
- A4/numbered-section DOCX output with deterministic reference numbering;
- regressions for the supplied UAV responses and the observed malformed/fabricated output failures.

### Changed

- incomplete original studies stop before drafting instead of producing an unsafe manuscript;
- revision preserves unaffected sections byte-for-byte and rejects unsafe candidates;
- default target depth is 5,200 words with 20–40-source literature collection;
- generic/unknown target-journal rules remain an explicit author action;
- schema-3 projects migrate non-destructively and preserve the v1 manuscript.

### Removed

- whole-manuscript model revision;
- model-owned major-section assembly;
- citation presence as a blanket license for unrelated numeric claims;
- inferred funding, conflicts, authorship, permissions, availability, or AI-use declarations.

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
