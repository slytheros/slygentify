# ADR 0013: First-class generated-artifact recovery

## Status

Accepted

Supersedes [ADR 0008](0008-editable-agents-artifact-ownership.md) and
[ADR 0012](0012-visible-managed-guidance-sections.md).

## Context

ADRs 0008 and 0012 establish digest-guarded whole-document and visible-section
ownership for root `AGENTS.md`. A valid legacy sidecar can already be upgraded during
`init`, but an invalid `.slygentify/state.json` blocks every refresh even when one
well-formed visible Slygentify section provides a safe replacement boundary. Recovery
therefore requires a maintainer to rename generated state and reconstruct ownership by
hand.

Issue #17 selected a tool-owned recovery path. Generated artifacts should upgrade during
their owning mutating workflow whenever ownership and write boundaries can be established
without trusting stale provenance. Read-only commands must remain read-only, future state
must not be downgraded, and human content outside a managed section must remain byte-for-byte
unchanged.

## Decision

`init` treats exactly one well-formed pair of the fixed Slygentify begin and end markers as
independent ownership evidence for the bytes inside that section. When a bounded, readable,
safe regular state sidecar is invalid, ordinary `init` may replace only that section from a
fresh scan and atomically rebuild canonical state. It preserves all surrounding bytes. If
the current section already equals fresh generation, it rebuilds state without changing
`AGENTS.md`.

Ordinary `init` may also rebuild invalid state without changing a whole-document artifact
whose bytes exactly equal fresh generation, or create guidance when no artifact exists.
`--adopt` may append a managed section to an otherwise unmanaged regular `AGENTS.md` while
rebuilding bounded invalid state. Ambiguous whole-document content and malformed or
duplicate markers remain unchanged unless `--replace` explicitly authorizes full-document
replacement. `--replace` never widens a valid section recovery into whole-document
replacement.

Valid-state digest protection remains authoritative: a section changed while valid state
exists is human-edited and ordinary regeneration refuses it. When state is invalid, marker
ownership intentionally permits replacement of all bytes inside the markers. Maintainers
must keep durable human guidance outside the managed section.

Bounded invalid state is replaced in place without a backup and without reproducing its
contents. Planning records a digest and filesystem identity for concurrency revalidation.
Unreadable, oversized, symbolic-link, directory, or otherwise unsafe state remains
protected and receives an exact manual recovery action. A state document declaring a newer
schema major is never rebuilt or replaced by an older binary, including under `--replace`.

`init` exposes whether it is performing no recovery, a supported schema upgrade, or a state
rebuild. `doctor` remains read-only and retains `doctor.state.invalid`; its remediation
describes the applicable automatic, adoption, replacement, version-upgrade, or manual
filesystem action. State producers continue to emit schema version 2.

## Consequences

The common invalid-sidecar recovery requires no special command beyond the already
mutating `init`, and marked human documents retain all content outside the managed section.
The marker pair becomes a durable ownership boundary rather than only a locator backed by
state. Human edits inside that boundary can be replaced if state becomes invalid; this is
the explicit cost of state-independent automatic recovery.

No state schema migration is required. The public initialization plan and result gain an
additive recovery classification. Existing automation continues to treat doctor invalid
state as a partial error and must invoke `init` separately to mutate the repository.

## Alternatives considered

A new `--recover` option was rejected because it adds a user step where ownership is already
bounded. Mandatory sidecar renaming was rejected because it transfers deterministic
generated-artifact repair to the user. Automatic backups were rejected because invalid
bytes may contain unintended sensitive data and state is regenerable. Rebuilding future
schema majors was rejected because it could silently downgrade provenance. State-v3 marker
metadata was deferred because the fixed markers and existing v2 state are sufficient for
the selected boundary.

## Approval record

The maintainer selected this design in the issue #17 planning and implementation request
and formally accepted the ADR on 2026-08-29.
