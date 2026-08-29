# ADR 0008: Editable AGENTS.md artifact ownership and regeneration

## Status

Superseded

Superseded by [ADR 0013](0013-generated-artifact-recovery.md), which consolidates the
generated-guidance ownership and recovery policy.

## Context

ADR 0002 makes generated agent guidance part of the public 1.0 workflow, but requires
`init` to preserve user-owned content through an ownership and regeneration policy.
It also warns that generated AGENTS.md content can gain apparent authority after its
evidence becomes stale. ADR 0005 separately establishes `.slygentify/state.json` as
committed, deterministic, regenerable provenance: it records generated-artifact digests
and evidence relationships but never supplies current repository facts.

The current conservative `init` vertical slice creates a root `AGENTS.md` only when no
entry exists and refuses every existing filesystem entry. That behavior protects user
work, but it does not define how a later component-aware generator can distinguish its
own unchanged output from an unmanaged file or a maintainer edit.

pre-public planning item, recorded in pre-public approval record on 2026-08-18, found that a plain root-level
`AGENTS.md` is a useful portable baseline but that nesting, override, inclusion, and
precedence behavior differs materially between consumers. Codex supports hierarchical
instruction chains; GitHub Copilot CLI combines applicable instruction files without a
general precedence guarantee; Cursor documents a root-level-only AGENTS.md mode;
OpenCode, Gemini CLI, and Claude Code use different file and fallback conventions.
This decision therefore concerns one concise root artifact, not vendor-specific files or
unportable generated structure.

The confirmed pre-public planning item default is an editable `AGENTS.md` with a separate deterministic
provenance sidecar, not a hidden managed section. The policy must give maintainers a
review path, keep ordinary regeneration non-destructive, identify safe recovery paths,
and preserve the local-only, non-executing effect boundary. It must also be explicit
about the residual risk when a maintainer deliberately replaces human-edited content
without an automatic backup.

This ADR supplies the decision requested by pre-public approval record. It does not implement the
generation interface, state schema, or diagnostic codes; those remain dependent work.

## Decision

Adopt an **editable root artifact with digest-guarded regeneration** policy.

### Artifact and provenance boundary

`init` manages at most these repository-root artifacts:

- `AGENTS.md`, a concise, plain-Markdown, user-editable instruction document; and
- `.slygentify/state.json`, the deterministic provenance sidecar defined by ADR 0005.

The generated document has no hidden managed region, front matter, imports, vendor
metadata, nested AGENTS.md files, or vendor-specific copies. It contains only generated
facts classified according to the public evidence model, explicit unknowns, conservative
safety guidance, and visible references to relevant repository material. A reference is
ordinary Markdown text, not an instruction for a consumer to import another file.

State records the root artifact location, the SHA-256 digest of its exact UTF-8 bytes,
and the evidence relationships used to derive it. It is provenance only: the current
filesystem and fresh inspection remain authoritative for current facts. State never
stores AGENTS.md content, raw source content, timestamps, absolute paths, credentials,
or a hidden maintainer-correction channel.

### Ownership states

On every `init` planning operation, Slygentify reads only the root artifact and safe
state needed to classify one of these states:

| State | Condition | Ordinary `init` behavior |
| --- | --- | --- |
| New | Both AGENTS.md and state are absent. | Plan creation of both artifacts. |
| Clean managed | State is valid, records AGENTS.md, and its digest equals the current regular file. | Plan a no-op or regeneration. |
| Unmanaged | AGENTS.md exists but no valid matching managed state exists. | Refuse without writes. |
| Human-edited | Valid state records AGENTS.md but its digest differs from the current regular file. | Refuse without writes. |
| Missing managed artifact | Valid state records AGENTS.md but the artifact is absent. | Refuse without writes. |
| Invalid state or unsafe entry | State is malformed or unsupported, or either target is a symlink, directory, or another non-regular entry where a file is required. | Refuse without writes. |

When a valid but stale state does not match the current artifact, Slygentify computes a
fresh candidate. If that candidate's exact bytes equal the current regular AGENTS.md,
the state is recoverably stale: Slygentify may plan a sidecar-only repair. Otherwise the
ordinary result remains Human-edited, even if the difference was caused by a prior
interrupted operation. This fail-closed rule never assumes that unmatched text belongs to
Slygentify.

### Review, regeneration, and replacement

`--dry-run` validates the same applicable preconditions as its mutating counterpart and
performs no repository writes. It reports the ownership state and shows the exact
proposed content for every artifact that would change.

For a Clean managed artifact, ordinary mutating `init` writes only when freshly generated
AGENTS.md bytes or provenance bytes differ. It makes no write for an exact no-op. It may
perform the sidecar-only repair described above after the same reviewable planning.

Ordinary `init` never overwrites or merges an Unmanaged or Human-edited artifact. A
separate, explicit replacement mode is the only operation permitted to replace an
existing regular AGENTS.md. Before replacement, it must identify the target, state that
the existing content will be discarded, and offer the exact dry-run result. It does not
create or retain an automatic backup; the maintainer must preserve any desired content
before invoking the replacement operation. Explicit replacement does not authorize
replacing symlinks, directories, malformed-state targets, or other unsafe entries.

The stable CLI option and corresponding Python API names belong to the dependent public
interface work, but replacement must remain explicit and non-default.

### Write and failure behavior

Every mutating path remains local, does not execute discovered repository commands, and
does not contact network services. Before writing, it revalidates the relevant target
entry kinds and ownership state. Each file is written to a temporary file in its own
directory and atomically replaced only after its complete deterministic bytes are ready.

For an operation changing both artifacts, Slygentify replaces AGENTS.md first and state
second. It does not attempt a destructive rollback if the second replacement fails.
Instead it reports that AGENTS.md changed while state did not, identifies the safe
relative paths, and directs the maintainer to run dry-run again. A later exact candidate
match may repair the lagging state; otherwise the mismatch remains a protected
Human-edited state. This ordering favors provenance that never claims an artifact was
installed when it was not.

Failures before the first replacement change neither artifact. The public behavior must
distinguish no-change failure from this bounded partial mutation and must not reproduce
user content or sensitive source values in diagnostics.

## Consequences

Maintainers can edit generated guidance directly without learning a proprietary region
syntax. Digest comparison makes that ownership visible and prevents ordinary
regeneration from silently discarding user work. The sidecar provides deterministic
evidence and drift links without pretending to be a cache or an authority for current
repository facts.

Clean generated files can evolve automatically with evidence-backed regeneration, while
unmanaged and changed files remain safe by default. The explicit replacement path is a
deliberate escape hatch for maintainers who have reviewed and preserved prior content.
Not creating automatic backups avoids retaining duplicate, potentially sensitive
instructions or inventing retention, cleanup, and backup-ownership policy.

The policy does not merge human changes into newly generated content. Maintainers who
want a changed document to remain intact must retain it, manually reconcile the dry-run
result, or choose not to replace it. A process interruption between two atomic file
replacements can leave a recoverable state/artifact mismatch; Slygentify reports and
protects that state rather than risking destructive rollback.

The contract introduces future requirements, diagnostics, tests, and state validation
work. It does not promise compatibility for the current pre-1.0 `init` interface; the
later public-interface decision must document the replacement action and migration from
the existing create-only behavior.

## Alternatives considered

### Hidden managed section in AGENTS.md

Embedding generated content between markers would simplify selective replacement, but
it makes the document's ownership less legible, makes manual edits ambiguous, and is not
portable across agent consumers. It conflicts with the confirmed editable-document
default.

### Always refuse every existing file

The current behavior is maximally conservative but prevents Slygentify from refreshing
its own unchanged output. Digest-guarded clean regeneration retains the safety property
for user-owned content while enabling the intended operating-map workflow.

### Automatic three-way merge

Automatic merging would require a durable base-content store or managed boundaries,
conflict semantics, and trusted interpretation of arbitrary Markdown edits. It could
silently alter human instructions and is disproportionate to the initial workflow.

### Treat sidecar state as authoritative

Using state to restore or infer current guidance would let stale or altered provenance
override inspected repository evidence or maintainer text. ADR 0005 rejects this use of
state.

### Automatic backups on replacement

Automatic backups duplicate potentially sensitive user-owned text and require retention,
cleanup, placement, and recovery policy. The selected explicit replacement warning keeps
that preservation decision with the maintainer.

### Generate nested or vendor-specific instruction files

Nested scope, precedence, inclusion, and supported filenames differ across consumers.
Generating those files would claim portability that pre-public planning item did not establish and enlarge
the artifact ownership surface without an approved need.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
