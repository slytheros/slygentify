# CLI guide

Slygentify exposes four implemented commands: `init`, `scan`, `map`, and `doctor`. Each
accepts a directory inside a Git repository and resolves the nearest containing Git
root. Command discovery and help are available with `slygentify --help` and
`slygentify COMMAND --help`.

## `slygentify init`

`init` builds concise root `AGENTS.md` guidance from a fresh scan and a deterministic
`.slygentify/state.json` ownership sidecar.

```console
slygentify init path/to/repository --dry-run
slygentify init path/to/repository --adopt --dry-run
slygentify init path/to/repository
slygentify init path/to/repository --replace
```

Use `--dry-run` first. It prints the complete guidance or visible managed section plus a
provenance summary and performs no writes; add `--show-state` to print exact state JSON.
Without `--replace`, initialization applies only to a new target, unchanged
managed guidance, or a recoverable missing sidecar. For an unmanaged or human-edited
safe regular `AGENTS.md`, ordinary init preserves the file and prints a deterministic,
paste-ready Slygentify section; it exits 4 to identify the required manual incorporation.
The section does not include a document-level title or managed-artifact boilerplate, and
this path does not create provenance state. Dry-run still prints the full exact artifact
review and exits 4. `--adopt` is the explicit alternative for an unmanaged regular file:
it appends a visible marked Slygentify section, preserves surrounding text, and records
section ownership. Missing managed, malformed, and unsafe states fail closed.

`--replace` may discard an existing regular `AGENTS.md`; it does not create a backup or
merge user text and never authorizes replacing a symbolic link, directory, or unsafe
state. Application revalidates the plan, writes atomically, and reports exact changed
locations if the guidance write succeeds but the sidecar write fails.

Init uses these exit statuses: 0 for applied, no-change, and applicable dry-run results;
1 for refused or operationally failed results; 2 for CLI usage errors; and 4 when safe
existing guidance was preserved and the displayed section must be pasted manually.

Configuration can bound generated guidance. See
[`[init]`](configuration-and-provenance.md#init-guidance-bounds).

## `slygentify scan`

`scan` performs fresh, bounded repository inspection and emits a complete human report
by default.

```console
slygentify scan path/to/repository
slygentify scan path/to/repository --format json
slygentify scan path/to/repository --interactive
slygentify scan path/to/repository --git-executable path/to/git
```

The text report groups repository orientation, workflows, architecture, automation,
concerns, inspection boundaries, and source provenance. Needs attention leads with
Problems & next steps, nests source-related unknown findings under their diagnostic,
and reports both issue and canonical-record counts. Unmatched unknowns, explicit
cautions, and recommendations remain separate. `--format json` writes only
canonical schema-major-1 JSON to standard output. `--interactive` requires interactive
input and output terminals and cannot be combined with JSON format; it provides a
keyboard-accessible tree, search, filters, evidence-first detail, raw record JSON, and a
glossary.

A successful result is `complete` or `partial`. Partial means some evidence was skipped
or limited, not that the command failed; diagnostics and skipped scopes identify what
was unavailable. Operational failures write to standard error and exit 1. Redirected
text has no terminal control sequences, `NO_COLOR` disables color, and scan does not
launch a pager.

By default, Slygentify may run one fixed bounded Git tracked-path lookup. Missing,
rejected, or failed automatic Git makes the scan partial and uses ordinary checked-out
ignore behavior. `--git-executable` authorizes the exact resolved executable as trusted,
unsandboxed code. It overrides `PATH`; invalid explicit input is an error and does not
fall back. This option can permit arbitrary effects by the selected executable.

## `slygentify doctor`

`doctor` performs a fresh static assessment of configuration, managed provenance,
repository evidence, and generated guidance. It does not change files, contact the
network, prompt, or execute discovered project commands.

```console
slygentify doctor path/to/repository
slygentify doctor path/to/repository --verbose
slygentify doctor path/to/repository --format json
slygentify doctor path/to/repository --git-executable path/to/git
```

Default text identifies the resolved repository, complete or partial status, severity
counts, and every diagnostic with its claim classification, stable code, target,
problem, effect, and remediation. `--verbose` additionally prints diagnostic evidence
references, including the originating scan code or boundary reason for every distinct
partial-inspection cause, one complete evidence appendix, and every skipped scope. Human wording and
layout may evolve and must not be parsed by automation. `--format json` writes exactly
one canonical `doctor-v1` document to standard output and cannot be combined with
`--verbose`.

Doctor has four stable process outcomes:

| Exit | Meaning | Result streams |
| ---: | --- | --- |
| 0 | A trustworthy result has no warning or error diagnostics. | Requested report on stdout; empty stderr. |
| 1 | A trustworthy complete or partial result has at least one warning or error. | Requested report on stdout; empty stderr. |
| 2 | CLI usage or caller-selected input prevented a result. | Actionable error on stderr; empty stdout. |
| 3 | An operational or internal failure prevented a trustworthy result. | Actionable error on stderr; empty stdout. |

The simplest fail-fast CI check needs no output parsing:

```console
slygentify doctor .
```

For POSIX-shell and PowerShell recipes that retain canonical JSON while distinguishing
findings from invocation or tool failure, see the [doctor task guide](guides/doctor.md#ci-recipes).

`--git-executable` has the same exact trusted-code meaning as scan and map: the selected
file is not sandboxed and can have arbitrary effects. It only selects Git for the
reviewed tracked-path lookup and never authorizes a discovered validation command.

## `slygentify map`

`map` performs a fresh scan and emits canonical `scan-projection-v1` JSON for one logical
repository-relative POSIX path. The path need not exist.

```console
slygentify map path/to/repository --scope src/example.py
slygentify map --scope apps/api --section workflows --section architecture
slygentify map --scope . --max-bytes unlimited
```

The default sections are `orientation` and `boundaries`; additional choices are
`workflows`, `architecture`, and `automation`. Repeated sections are deduplicated into
canonical order. The default byte ceiling is 8 KiB including the final newline.
Required repository and owning-component context is never silently removed; if that
envelope cannot fit, map fails. Optional omissions are counted by section and record
kind, and every retained record keeps its required evidence.

Use `navigation.owner`, `navigation.ancestors`, and `navigation.children` to drill down
by component. Rerun map with a child's component `path` until the relevant owner is in
scope. Map is a component operating-map projection, not a recursive file, symbol, call,
or semantic graph.

`--git-executable` has the same explicit trusted-code boundary as scan. Map does not
cache, consume saved scan JSON, write repository files, or contact the network.

## Default effects

Scan, map, and doctor are local, read-only, bounded, contained, and network-free, apart
from the reviewed Git lookup described above. They do not execute discovered repository
commands. Init plans with the same inspection boundary and writes only its two named
artifacts after explicit application. See the public [safety boundary](safety.md) for the
complete operational summary.
