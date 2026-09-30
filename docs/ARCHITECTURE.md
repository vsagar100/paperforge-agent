# Stages, decisions and recovery

| Stage | Output | Responsibility |
|---|---|---|
| intake | Inventory, hashes, warnings | Read supported evidence |
| literature | Registered metadata, passages, exact-quote appraisals | Discover and verify existence |
| plan | Question, provisional gap, experiments, candidate datasets | Identify required evidence |
| analysis | Built-in computations, actual search inventory | Calculate implemented analyses only |
| draft | Accepted sections and provenance | Write and checkpoint each section |
| review | Issues, retained revisions, round checkpoints | Review and correct within bounds |
| export | Manuscript, registers, tables, proposed diagrams, QC | Assemble citations and author actions |

~~~mermaid
stateDiagram-v2
    [*] --> Pending
    Pending --> Running: run or resume
    Running --> Failed: exception
    Failed --> Running: fix and resume
    Running --> AwaitingAuthor: evidence gap or revision limit
    AwaitingAuthor --> Paused: defer
    Paused --> Pending: continue
    AwaitingAuthor --> Pending: continue or revise
    Running --> AuthorReview: export
    AuthorReview --> Pending: explicit retry or new evidence
    AwaitingAuthor --> Cancelled: cancel
    Cancelled --> [*]
~~~

## Persistence

Resolved settings live in paperforge.yaml. Project metadata, stages, calls, checkpoints,
events and decisions live in workflow.sqlite3 (WAL, FULL synchronization). inputs, outputs
and audit share the project directory. A process file lock prevents simultaneous writers.
Atomic replacement protects text/settings exports. SQLite is authoritative.

Reading status does not reset running records. A killed process can leave a running stage,
which the next run attempts again. Completed stages and accepted sections are reused.
Each accepted review revision survives a later failure. Rejected revisions retain earlier text.

Generation is cached by request fingerprint. Explicit draft/review retry increments epochs
to prevent substituting old writing for requested regeneration. Changed input bytes/scientific
settings invalidate dependent stages; route and budget changes preserve accepted stages.

## Author transitions

continue invalidates the blocked/failed stage and dependents, preserving audit history.
revise defaults to draft, or uses an explicit stage. Decision notes become author-note inputs
and trigger dependent revalidation. defer pauses; continue leaves the pause; cancel is terminal.
Evidence checks run again: author approval never turns planned experiments into observations.

## Failure behavior

| Failure | Recovery |
|---|---|
| Transient HTTP error | Bounded retries and explicitly listed fallback routes |
| Cap exhausted | Prevent next paid dispatch; choose a legitimate route or revised cap |
| Mid-section exception | Resume and reuse earlier accepted sections |
| Mid-review exception | Retain accepted revisions and review retained work |
| Export disk failure | Resume export without redrafting |
| Unsupported file | Retain original and request conversion |
| Unsupported claim | Block and export accepted partial draft |
| Changed evidence | Revalidate dependent work; preserve prior audit |
| Lost project directory | No recovery; backups required |

After an uncertain timeout the remote provider may have processed a request; a retry can
incur another charge. Reservations stay counted. Back up with SQLite's backup API, or stop
writers and copy the entire project including outstanding WAL and input/audit files.
Do not delete WAL/SHM during a write. Externally deleted outputs are recreated by explicit
retry export followed by resume; completed markers do not monitor external edits.

## Scientific boundary

Crossref/OpenAlex are discovery and bibliographic services, not SCI/SCIE certification.
Guards check IDs, exact excerpts, percentage support and basic provenance. They do not
prove semantic entailment, every numeric claim, novelty or mathematical correctness.
A separate model review evaluates these concerns; author verification remains necessary.

Dataset candidates are not executed experiments. Native runtimes, scientific image reasoning,
additional statistical plug-ins, journal-template typesetting and a web UI are future extensions.
The final QC packet reports missing evidence and always remains an author-review draft.
