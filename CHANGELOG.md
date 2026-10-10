# Changelog

## Unreleased — PowerShell setup and saved credentials

- Fall back across configured routes after schema/evidence repairs fail, giving each model
  the original task and validating its response before acceptance.
- On HTTP 429, prefer another eligible configured route over retrying the exhausted one.
- Add route --free to save a credentialed free-model chain and enforce free_only policy.
- Diagnose HTTP 429 without exposing server bodies; honor HTTP Retry-After and Gemini
  RetryInfo, avoid brief retries of reported daily/zero quotas, and persist long cooldowns.
- Add saved per-model request spacing and bounded inline waits with explicit fallback.
- Repair source appraisal quote/identity errors within the configured schema-repair limit;
  keep exact-quote checks, accepted checkpoints and actionable source/field diagnostics.
- Load repository/project .env with explicit precedence and UTF-8 BOM support.
- Add a dot-source PowerShell activation/environment helper and end-to-end Windows guide.
- Add non-generating model discovery, one-request route probes and informative Gemini 404 guidance.
- Update new Gemini routes to gemini-3.5-flash-lite; preserve existing configured model IDs.
- Show configuration paths, effective route overrides and dotenv file paths in doctor, without secrets.

## 3.0.0 — Fresh enhanced-writing implementation

- Replace the earlier implementation with explicit stages and new project contracts.
- Persist stages, decisions, accepted sections, revisions, calls and budget reservations.
- Add provider/snapshot selection, stage/role routes, free/paid policy and bounded retries.
- Extract mixed attachments and report native-format conversions.
- Retrieve scholarly metadata, appraise exact source excerpts and discover candidate datasets.
- Draft/review with evidence gates, author continuation and bounded correction loops.
- Export manuscripts, source/claim registers, evidence tables, proposed diagrams and QC.
- Add failure-injection, provider, budget, file and CLI tests.

No automatic old-project migration, native simulations or submission-readiness certification.
