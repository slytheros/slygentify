# Acceptance measurement

ADR 0002's public release gate is measured from a versioned public corpus, not from
synthetic fixtures or a scan's own output. The tracked
[`corpus-v1.json`](../tests/acceptance/corpus-v1.json) records the exact 20 public
repositories, immutable revisions, categories, license metadata, and framework
coverage selected for that gate.

The companion expected-fact matrix records every factual component, `verified`
finding, and `verified` relationship. A fact identifies its repository-relative
subject and the source locations and locators that a human reviewed. It deliberately
does not store cloned source trees, raw scan documents, local paths, or secret values.

## Workflow

Corpus acquisition is a separate, explicit developer operation. It may use the network
and must create pinned checkouts outside this repository. In the examples below, replace
uppercase path placeholders such as `CORPUS_ROOT` and `REPORT_PATH` with local paths
outside the repository. The corpus is never a runtime Slygentify input. The measurement
runner rejects tracked source changes and scans a disposable copy at the approved commit.
It removes untracked material from that disposable copy only; the source checkout is
never modified.

Generate a candidate fact matrix from the approved 20 local checkouts:

```console
uv run python -m tools.measure_acceptance \
  --formal-root CORPUS_ROOT \
  --candidate-output CANDIDATE_MATRIX \
  --report REPORT_PATH
```

An authorized human reviews every candidate against the pinned source evidence, then
promotes the reviewed matrix to `tests/acceptance/expected-facts-v1.json` by changing
its `review_status` to `reviewed`. The formal score is fail-closed until that status and
a non-empty, unique fact list are present:

```console
uv run python -m tools.measure_acceptance \
  --formal-root CORPUS_ROOT \
  --matrix tests/acceptance/expected-facts-v1.json \
  --report REPORT_PATH
```

The report fails the formal gate for any unexpected factual claim, omitted expected fact,
or missing reviewed evidence location. It passes only at 100% precision and at least 95%
recall. Every approved checkout is scanned twice and must produce identical canonical
JSON.

## Supplemental breadth rerun

The 71 public local checkouts used by pre-public research provide additional breadth evidence, but
they are not part of the formal score because only the 20-repository manifest has
reviewed immutable ground truth. Run them locally and retain only the sanitized aggregate
report:

```console
uv run python -m tools.measure_acceptance \
  --supplemental-root SUPPLEMENTAL_ROOT \
  --report REPORT_PATH
```

This mode requires exactly 71 direct Git checkouts and reports aggregate completion,
component, finding, diagnostic, and skipped-scope counts. It does not write to targets,
attach raw results, or include the two private snapshots excluded by pre-public tracking record.

## Composed-repository scaling

pre-public tracking record's external performance gate compares the same 71 commit-pinned repositories
with a combined repository built outside this checkout. Run the local benchmark with an
odd number of trials (three is the default):

```console
uv run python tools/benchmark_scan_scaling.py \
  --isolated-root SUPPLEMENTAL_ROOT \
  --composed-root COMPOSED_ROOT \
  --report REPORT_PATH
```

The runner verifies the 71 manifest commits and the absence of tracked checkout changes
(without touching untracked local artifacts), warms both scan shapes, alternates their
trial order, and includes canonical JSON serialization in every measurement. Its report
contains only aggregate timings, counts, result completion, and canonical JSON hashes.
The composed median wall time must not exceed twice the median total isolated-scan wall
time. Do not commit the corpus, raw profiles, or local reports.

## Initialization usefulness review

Initialization has a separate local-only review gate over the same 20 pinned checkouts.
Each corpus entry must be a direct standalone checkout with a regular `.git` directory;
linked worktrees and linked or reparse-point Git metadata are rejected. The runner
disables Git hooks, filesystem monitors, and the untracked cache for its own inspection,
verifies the checkout root, origin, and pinned commit, and never applies an initialization
plan or changes a corpus checkout. Candidate mode copies descendant symbolic links as
links without reading their targets, revalidates the copied Git root and commit, removes
untracked or ignored material only from the disposable location, plans initialization
twice, and generates the default root projection twice. It writes each `AGENTS.md` and
`root-map.json` only to an explicitly selected directory outside both this repository
and the corpus. The candidate matrix and sanitized report contain only digests and
metrics:

```console
uv run python -m tools.review_initialization \
  --formal-root CORPUS_ROOT \
  --candidate-matrix CANDIDATE_MATRIX \
  --artifacts-directory ARTIFACTS_DIRECTORY \
  --report REPORT_PATH
```

Candidate mode fails unless all 20 repositories are present, every `AGENTS.md` is at
most 4 KiB, their median is at most 2 KiB, and every default root projection is at most
8 KiB. The matrix records component-index and projection omission counts so capped
outputs remain visible.

An authorized human reviews every bootstrap-to-map workflow for bootstrap clarity,
component-index accuracy, map navigation, boundary honesty, safety, and concision. They
then promote the matrix to
[`initialization-review-v1.json`](../tests/acceptance/initialization-review-v1.json),
provide their identity and review date, and mark every criterion and overall result as
`pass`. Raw generated guidance and local paths are never committed.

Formal mode regenerates both artifacts from the approved corpus and fails if either
reviewed digest or any metric changes, if the review is incomplete, or if a reviewed
entry is outside the corpus:

```console
uv run python -m tools.review_initialization \
  --formal-root CORPUS_ROOT \
  --reviewed-matrix tests/acceptance/initialization-review-v1.json \
  --report REPORT_PATH
```
