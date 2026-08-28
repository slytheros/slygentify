# `slygentify.toml` configuration and provenance

Slygentify optionally reads one `slygentify.toml` at the selected Git repository root.
It does not search parent directories, component directories, user profiles, or
environment variables for configuration.

The file must be a regular, non-symbolic-link file no larger than 1 MiB and contain
strict UTF-8 TOML without a byte-order mark. `schema_version = 1` is required. Unknown
tables or keys, malformed values, unsafe paths, duplicate component paths, and invalid
patterns fail before repository traversal. The failure does not write repository files.

## Complete example

```toml
schema_version = 1

[scan]
ignore = ["generated/**", "!generated/keep.txt"]

[[scan.components]]
path = "services/api"
ecosystem = "python"
kind = "application"

[[scan.components]]
path = "packages/web"
ecosystem = "javascript"

[scan.limits]
max_entries = 2000000
max_elapsed_seconds = "unlimited"

[init]
max_agents_bytes = 4096
max_component_entries = 8
```

All sections are optional apart from the top-level schema version. Defaults apply when
a section or value is absent.

## `[scan]`

`ignore` is an array of Gitignore-style strings applied in order after built-in safety
exclusions. Negation with `!` can re-include a path excluded by an earlier configuration
pattern, but configuration cannot re-include built-in ignored directories, sensitive
paths, nested repositories, unsafe links, non-regular files, or paths outside the root.
An invalid Gitignore pattern rejects the configuration.

### `[[scan.components]]`

Each declaration adds repository evidence for one component boundary.

| Key | Required | Meaning |
| --- | --- | --- |
| `path` | yes | Existing repository-relative POSIX directory, or `.` for the root. |
| `ecosystem` | no | Non-empty ecosystem facet to retain alongside detected evidence. |
| `kind` | no | Non-empty component kind to retain alongside detected evidence. |

Paths use `/`, must be normalized, contained, unique, and refer to an existing regular
directory that is not a descendant Git repository or symbolic link. Absolute paths,
backslashes, `.`/`..` segments other than the root value `.`, drive-qualified paths, and
missing directories are rejected.

Declarations supplement deterministic inspection. A configured ecosystem is retained
alongside a different detected ecosystem and a diagnostic identifies that conflict. A
configured `kind` selects the component's normalized kind while the underlying detected
evidence remains cited. Component declarations do not change workspace membership.
Resolve overlapping workspace ownership by narrowing or excluding the workspace
declarations that produced it.

### `[scan.limits]`

Each value accepts a positive integer or the exact string `"unlimited"`.

| Key | Default | Unit or scope |
| --- | ---: | --- |
| `max_depth` | 256 | directory levels |
| `max_entries` | 1,000,000 | catalogued entries |
| `max_file_bytes` | 268,435,456 | bytes per file |
| `max_total_bytes` | 17,179,869,184 | total bytes read |
| `max_elapsed_seconds` | 1,800 | elapsed seconds |
| `max_open_files` | 256 | simultaneous file descriptors |
| `max_memory_bytes` | 2,147,483,648 | deterministic logical accounting bytes |

Lower values tighten inspection. Raising a default or selecting `"unlimited"` emits one
`configuration.relaxed-limits` diagnostic. Unlimited removes that accounting limit only;
containment, file type, sensitive-path, link, nested-repository, command, and network
boundaries remain in force. See [inspection accounting](inspection-accounting.md) for
partial-result and budget semantics.

## Init guidance bounds

The `[init]` table controls only generated root guidance:

| Key | Default | Valid values |
| --- | ---: | --- |
| `max_agents_bytes` | 4,096 | integer at least 1,536, or `"unlimited"` |
| `max_component_entries` | 8 | positive integer, or `"unlimited"` |

The bounds act independently. The generated document remains a fixed bootstrap router
plus a bounded primary-component index, not a scan serialization. Raising either default
or selecting unlimited produces an initialization warning. Because the values are
committed configuration, dry-runs and later regeneration remain deterministic and the
configuration digest changes provenance.

## Effect boundaries

Configuration cannot authorize sensitive-content reads, descendant-link following,
nested-repository traversal, command execution, network access, or writes. It also cannot
select `--git-executable`; that trusted-code authorization exists only per CLI or Python
invocation. A higher or unlimited resource setting never weakens these boundaries.

## `.slygentify/state.json`

`slygentify init` writes deterministic schema-major-2 provenance beside generated
guidance. The sidecar records safe relative locations, SHA-256 digests, effective limits,
derivations, generated-artifact ownership, completion, and skipped scopes. It contains no
timestamps, host paths, source bodies, environment values, or credentials. Packaged
[`state-v2.schema.json`](schemas.md) describes the document; legacy v1 sidecars remain
readable. Scan and map do not read or write it; a fresh scan remains authoritative.

When a whole-document digest matches a regular `AGENTS.md`, init may regenerate it. An
adopted visible section records its own digest, so later updates preserve human changes
outside the fixed markers and refuse a changed or malformed section. Missing, unmatched,
malformed, or unsafe state is protected by default. `--dry-run` displays generated
guidance plus a provenance summary; `--show-state` displays exact state JSON. `--replace`
may discard an existing regular `AGENTS.md` but never a symbolic link, directory, or
malformed state. Writes are atomic, guidance first and state second.

For an unmanaged or human-edited safe regular `AGENTS.md`, ordinary init instead prints
a deterministic paste-ready section and exits 4. `init --adopt` is an explicit, safe
alternative only for unmanaged guidance without provenance: it appends a marked visible
section and writes v2 state, without echoing surrounding user content during review.
