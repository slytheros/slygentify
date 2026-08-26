# ADR 0007: Git-backed tracked-path discovery

## Status

Accepted

## Context

ADR 0005 selected checked-out `.gitignore` files as repository inspection scope rules
while excluding the Git index from inspection. It records the resulting limitation:
Slygentify can omit a tracked file when the checked-out ignore rules match that file.
External validation in pre-public approval record demonstrated that this limitation can hide an
otherwise approved operating manifest and reduce the recall of verified scan claims.

The initial correction proposed reading and parsing only a root-local Git index. That
would preserve the non-executing inspection boundary, but it would make Slygentify
responsible for validating multiple index versions, hash formats, extensions, split and
sparse layouts, and future format evolution. Available Python Git libraries either call
Git, open a broader repository capability, require native code, or do not provide the
strict caller-bounded and fail-closed index parser required by the existing safety
contract.

Git itself provides the stable `git ls-files --cached -z` interface for returning
tracked index paths without quoting them. Using that interface delegates index-format
compatibility to the user's Git installation, but it changes the effect boundary:
`scan` launches a process, Git may follow a root `.git` indirection and consult
repository-local configuration and administrative metadata, and an explicitly selected
executable may be repository-controlled code.

pre-public approval record therefore requires a new human decision rather than an implementation-only
change. This ADR narrows ADRs 0004, 0005, and 0006 only for the tracked-path lookup
described here. It does not authorize discovered project commands, general Git
operations, command verification, repository mutation, or network-backed inspection.

## Decision

Adopt an **optional Git-backed tracked-path lookup with explicit executable override and
honest fallback**.

### Public selection

`slygentify scan` adds an optional `--git-executable PATH` option. The public Python
entry point adds the corresponding keyword-only argument:

```python
def scan_repository(
    path: str | os.PathLike[str] = ".",
    *,
    git_executable: str | os.PathLike[str] | None = None,
) -> ScanResult: ...
```

An explicit path is expanded and resolved relative to the caller's current directory.
It must identify an existing regular executable. Invalid explicit input is an
operational error before traversal; Slygentify does not fall back to PATH discovery.

Supplying the option authorizes execution of that exact resolved path, including when
the executable is inside the selected repository. This is a distinct execution effect:
Slygentify cannot prove that an arbitrary selected executable behaves like Git or
prevent it from reading, writing, launching processes, accessing credentials, or using
the network. CLI and Python documentation must state this consequence directly.

Without an override, Slygentify resolves `git` through the invoking environment's PATH.
The resolved target must be a regular executable outside the selected repository. A
PATH-resolved executable inside the repository is treated as unavailable because no
exact repository-code execution was authorized.

Slygentify records and revalidates the selected executable's filesystem identity
immediately before launch. Portable path-based execution still leaves a replacement
race, which remains an explicit residual risk.

### Fixed Git capability

The only automatically permitted process is equivalent to:

```text
<git> --no-pager
      -c core.fsmonitor=false
      -c core.hooksPath=<null-device>
      ls-files --cached --full-name -z --
```

It runs without a shell, with closed standard input, and with the selected repository
root as its working directory. Slygentify clears inherited `GIT_*` overrides and sets a
minimal environment that disables system and global configuration, terminal prompts,
lazy fetching, paging, filesystem-monitor hooks, configured hooks, and optional locks.
Repository-local configuration and its includes remain available because the selected
policy uses full Git repository semantics.

Git may therefore follow a root `.git` file, use an external or common Git directory,
read split-index metadata, expand sparse-index information, and consult other local Git
administrative data required by `ls-files`. Repository-local configuration includes may
read outside the selected root. The subprocess receives no recurse-submodules option and
must not contact a remote; lazy object fetching is disabled.

The tracked-path lookup has a ten-second ceiling or the remaining scan elapsed-time
budget, whichever is shorter. Standard output is streamed within the remaining
aggregate-byte and scanner-accounted-memory budgets. Standard error is capped at 64 KiB.
Slygentify terminates the process when a time or output bound is exhausted.

A result is trusted only when the process exits zero, standard error is empty, and
standard output consists entirely of valid NUL-terminated safe repository-relative path
records. Paths remain filesystem bytes internally, are deduplicated, and are never
reproduced as Git-output evidence. Malformed output invalidates the complete lookup
rather than retaining a prefix.

Git executable absence, automatic-selection rejection, timeout, non-zero exit, standard
error, unsafe output, or resource exhaustion is a recoverable capability failure. Scan
continues with no tracked-path exceptions, preserves the existing checked-out Gitignore
behavior, returns `completion="partial"`, and emits the stable diagnostic
`inspection.git-tracked-paths-unavailable` plus skipped reason
`git_tracking_unavailable` for repository-wide potentially omitted scope. It does not
reproduce stderr, executable paths, Git configuration, or index content. A partial scan
remains a successful CLI result with exit status zero.

An explicit path that validates but fails during invocation uses the same recoverable
partial-result behavior. Invalid explicit input remains an operational error because it
is a malformed caller request rather than environmental inspection exhaustion.

### Ignore semantics

The lookup runs once before checked-out Gitignore scope is applied. Inspection retains
an ignored regular file only when its exact repository-relative path is tracked. It
enters an otherwise ignored directory only when that directory is a prefix of a tracked
path. Untracked ignored siblings remain excluded and retain ordinary `gitignore`
skipped-scope provenance.

Tracked state changes only the Gitignore layer. Hard containment, sensitive-content,
nested-repository, descendant link and reparse-point, mount or volume, special-entry,
built-in cache, and resource-budget boundaries remain authoritative. Git output never
authorizes content reads or weakens those policies.

Manifests retained through tracked state produce the same ordinary evidence and
provenance as the same manifest outside ignored scope. The public scan model and
schema-major-1 JSON shape do not add a Git-output or executable record.

### Compatibility and determinism

Adding the optional CLI option and keyword-only Python argument is compatible within the
pre-1.0 surface. The new diagnostic and skipped-scope reason use the extensible code
spaces accepted by ADR 0004. No JSON field or closed vocabulary changes.

Complete tracked-path behavior now also depends on the selected Git executable, its
version and implementation, repository-local Git configuration, reachable Git
administrative metadata, and the executable's behavior. Byte determinism is promised
only when those effective inputs and the existing ADR 0004 inputs are equivalent.
Unavailable or exhausted Git lookup follows the environmental partial-result exception:
its reason and diagnostic semantics remain stable, but the evidence prefix may differ
from an environment where lookup succeeds.

## Consequences

Tracked approved manifests are no longer silently omitted merely because a checked-out
Gitignore rule matches them when Git lookup succeeds. Git owns compatibility with index
versions and common repository layouts, avoiding a second partial implementation of its
binary formats. Users with nonstandard installations can select an exact executable,
while installations without Git still receive useful current-scope results.

Default scan now launches one trusted local tool and is no longer literally
non-executing. Full Git semantics weaken the selected-root metadata boundary because
linked worktrees, common directories, repository configuration includes, split indexes,
and sparse-index support may require reads outside the worktree. Git availability and
version also become environmental inputs.

An explicitly selected executable inside a repository is executable repository content.
The option is deliberate authorization, not a claim of isolation: a malicious or
mistaken executable can mutate files, disclose credentials, access the network, or spawn
other processes before Slygentify can react. Timeout and stream limits bound waiting and
scanner-owned buffers, not child-process CPU, RSS, filesystem, credential, process-tree,
or network effects. Human acceptance of this residual risk is required before
implementation.

Git's fixed built-in `ls-files` command is not a discovered project command and does not
authorize later command execution. Scan remains responsible for never treating returned
paths as instructions or bypassing content-safety boundaries.

## Alternatives considered

### Parse the root index in Slygentify

A narrow parser would preserve the existing non-executing, root-local metadata boundary.
It was rejected for this proposal because correctness requires handling or explicitly
rejecting index versions, SHA-1 and SHA-256 layouts, extensions, split and sparse indexes,
checksums, path compression, conflicts, malformed input, and future Git evolution.

### Add a Python Git library

Dulwich is mature and pure Python but its low-level reader does not supply the complete
strict checksum, extension, path, and resource validation boundary needed here. pygit2
adds native libgit2 binaries and repository-wide behavior; GitPython invokes Git; other
evaluated packages are either broad Git implementations or too immature for this safety
boundary. A dependency would enlarge the audit surface without eliminating the
Slygentify-specific effect and validation work.

### Restrict Git to a root-local index snapshot

Pointing Git at an isolated temporary administrative directory could preserve more of
the original metadata boundary. It was rejected by the selected direction because it
adds temporary writes, cannot transparently support linked, split, and sparse layouts,
and would still need object-format and repository-layout emulation.

### Allow only an automatically discovered Git

This keeps repository code out of the executable selection boundary but prevents users
with portable, version-managed, or otherwise nonstandard Git installations from using
the capability. The explicit path is accepted instead, with its execution effect stated
as user-authorized.

### Treat unavailable Git as an operational failure

Hard failure would make scan unusable on systems without Git and discard evidence that
the existing scanner can still collect safely. Honest partial fallback preserves that
value without claiming exhaustive scope.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
