# ADR 0012: Visible managed guidance sections

## Status

Superseded

Superseded by [ADR 0013](0013-generated-artifact-recovery.md), which consolidates the
generated-guidance ownership and recovery policy.

## Context

The original digest-guarded lifecycle safely preserves an existing `AGENTS.md`, but its
paste-only route remains unmanaged. Doctor cannot subsequently establish whether that
adopted guidance is current. Printing complete provenance state during every dry-run
also makes routine review disproportionately noisy.

## Decision

`init --adopt` may append one visible `Slygentify bootstrap guidance` Markdown section to
an existing safe unmanaged root `AGENTS.md`. Fixed ASCII begin and end markers bound the
section. The operation is explicit, reviewable, local, and never echoes surrounding
human-owned text. It is not a general Markdown merge facility.

State v2 records whether an artifact owns the whole document or only the marked section.
For section ownership, regeneration replaces only a section whose recorded digest still
matches; all surrounding bytes are preserved. Missing, duplicate, malformed, or edited
markers fail closed. State v1 remains readable as legacy whole-document ownership.

Normal dry-runs display generated guidance and a deterministic provenance summary.
`--show-state` provides the exact state document when detailed inspection is needed.

Generated guidance names the maintenance loop: read-only `doctor` identifies drift, and
`init --dry-run` precedes any explicitly authorized refresh. Slygentify does not create
or schedule CI workflows.

## Consequences

Existing guidance can become maintainable without surrendering human text, at the cost
of one explicit visible block syntax and a v2 state schema. This deliberately supersedes
ADR 0008's blanket rejection of managed regions only for the opt-in, clearly labelled
section lifecycle; full-document generation remains supported.
