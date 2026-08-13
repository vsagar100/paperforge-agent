# PaperForge 2.0 Publication Standard

Checked on 2026-08-13. These sources inform the built-in rules and review dimensions; authors must
still recheck the live target-journal page immediately before submission.

## Correct interpretation of Scopus and SCIE

Scopus and the Web of Science Core Collection/SCIE are indexing and journal-evaluation systems. Their
published criteria address the journal's peer-review policy, editorial quality, content, standing,
publishing regularity, accessibility, and citation influence. They do not prescribe one universal
word count, section sequence, experiment, or acceptance checklist for every submitted manuscript.

PaperForge therefore never reports that a manuscript “meets Scopus standards” or “meets SCI
standards.” It checks the target journal's current author instructions and applies explicit
reproducibility, integrity, and reviewer-quality gates.

Official sources:

- [Scopus content policy and selection](https://www.elsevier.com/products/scopus/content/content-policy-and-selection)
- [Web of Science journal evaluation process and selection criteria](https://clarivate.com/academia-government/scientific-and-academic-research/research-discovery-and-referencing/web-of-science/web-of-science-core-collection/editorial-selection-process/journal-evaluation-process-selection-criteria/)

## Built-in DJES contract

The Diyala Journal of Engineering Sciences (DJES) author guidelines were used as the concrete target
for the current UAV project. The checked profile encodes these requirements:

| Item | Research article | Review article |
| --- | --- | --- |
| Main text | 4,000–7,000 words; target 5,200 | 8,000–12,000 words; target 9,000 |
| Abstract | 200–250 words | 200–250 words |
| Keywords | 4–6 | 4–6 |
| References | 25–40 | 50–100 |
| Tables | Up to 5 | Up to 5 |
| Figures | Up to 10 | Up to 10 |

The profile also requires a detailed reproducible method, clear results and discussion, numbered
sections, IEEE references, editable tables/equations, publication-quality figures, an anonymized main
manuscript, separate title page and cover letter, and complete declarations including AI use. The
exporter prepares only what can be derived safely; authors must supply identities, approvals,
declarations, source figures, and any journal template not represented in project evidence.

Official source:

- [DJES Author Guidelines](https://djes.info/index.php/djes/AuthorGuidelines)

## Peer-review and reproducibility dimensions

The staged reviewers cover the same practical concerns emphasized by IEEE author guidance:

- novelty and contribution are explicit but proportionate;
- literature coverage is current, relevant, and critical rather than enumerative;
- methods, experimental design, and analysis are technically sound and reproducible;
- results are traceable, appropriately analyzed, and clearly presented;
- conclusions follow from results;
- organization, terminology, references, tables, and figures are publication quality;
- data/code availability and enough method detail support reproducibility where feasible.

Official sources:

- [IEEE reviewer guidance](https://journals.ieeeauthorcenter.ieee.org/submit-your-article-for-peer-review/become-an-ieee-reviewer/)
- [IEEE research reproducibility guidance](https://journals.ieeeauthorcenter.ieee.org/create-your-ieee-journal-article/research-reproducibility/)

## Comparable published-paper benchmark

The redesign also inspected the structure—not the prose—of Xiwen Chen et al., “Wildland Fire
Detection and Monitoring Using a Drone-Collected RGB/IR Image Dataset,” published in *IEEE Access*
in 2022 (vol. 10, pp. 121301–121317, DOI 10.1109/ACCESS.2022.3222805).

The paper demonstrates the level of technical reporting expected from a credible UAV/thermal study:

- an explicit dataset comparison table;
- prescribed-burn setting and acquisition conditions;
- equipment and sensor specifications;
- labeling and preprocessing protocols;
- method and parameter descriptions;
- metric definitions and experimental protocol;
- repeated tests, comparisons/ablation, and computing-speed results;
- clear limitations and a traceable reference list.

PaperForge uses these as structural expectations only. It must never transfer that paper's values,
methods, claims, or conclusions into another project.

Source:

- [USDA Forest Service record and public manuscript](https://research.fs.usda.gov/treesearch/67004)

## PaperForge readiness meaning

`submission_ready=true` is allowed only when the implemented checks find no unresolved integrity
blocker, required author action, or unchecked publication contract, and the final deterministic score
meets configuration. It means “credible submission candidate after author verification,” not
“accepted,” “Scopus compliant,” “SCIE compliant,” or “plagiarism free.”
