# Initialize repository guidance

Use init to create concise root `AGENTS.md` guidance and its deterministic ownership
sidecar. Review before writing.

## Exact dry-run

```console
slygentify init path/to/repository --dry-run
```

Dry-run validates the applicable preconditions and prints complete generated guidance
plus a deterministic provenance summary without writing either file. Use `--show-state`
to print exact state JSON. Init has no JSON output mode.

## Apply a reviewed plan

```console
slygentify init path/to/repository
```

Ordinary application creates new guidance, regenerates unchanged managed guidance,
repairs a recoverable missing sidecar, or rebuilds bounded invalid state when ownership
is independently safe. When an unmanaged or human-edited safe regular
`AGENTS.md` exists, ordinary init preserves it and prints a paste-ready Slygentify
section for manual incorporation instead of replacing it. This result exits 4 and does
not create `.slygentify/state.json`. To retain existing human guidance while enabling
maintenance, review and apply `slygentify init PATH --adopt --dry-run` then
`slygentify init PATH --adopt`; it appends one visible marked section and manages only
that section. With bounded invalid state, `--adopt` can perform the same append and state
rebuild when no markers exist. Dry-run never echoes surrounding human text.

`--replace` may discard an existing regular `AGENTS.md`. It creates no backup and does
not merge text. It never authorizes replacing a symbolic link, directory, oversized or
unreadable state, or a newer state schema.

## Invalid provenance state

`.slygentify/state.json` is Slygentify's generated ownership and provenance record for
`AGENTS.md`. Ordinary init automatically rebuilds bounded readable invalid state when
there is exactly one well-formed managed section, no artifact, or whole-document bytes
that equal fresh generation. Section recovery replaces everything inside the markers and
preserves every surrounding byte. Keep durable human edits outside the markers.

For unmanaged guidance, use `--adopt --dry-run` to append a managed section and rebuild
state, or use `--replace --dry-run` only when the whole document may be discarded.
Malformed or duplicate markers require explicit whole-document replacement. A newer
state schema is never downgraded, even under `--replace`; install a compatible reviewed
build instead.

Oversized or unreadable state cannot receive digest-based revalidation. Correct its
permissions or retain it by renaming it to a new, non-existing backup name, then rerun
the dry-run. Unsafe filesystem entries always require manual correction.

On POSIX shells, first confirm the backup is absent, then rename:

```console
test ! -e .slygentify/state.json.rejected && mv .slygentify/state.json .slygentify/state.json.rejected
```

In PowerShell:

```powershell
if (-not (Test-Path -LiteralPath .slygentify/state.json.rejected)) { Move-Item -LiteralPath .slygentify/state.json -Destination .slygentify/state.json.rejected }
```

## Python

```python
from slygentify import apply_initialization, plan_initialization

plan = plan_initialization("path/to/repository")
for diagnostic in plan.diagnostics:
    print(diagnostic.disposition, diagnostic.code, diagnostic.recovery)

if plan.can_apply:
    result = apply_initialization(plan)
    print(result.changed_locations)
```

The plan contains the exact artifact bytes, `state_recovery`, and source digests.
Application revalidates it and reports exact changed locations if guidance succeeds but
the sidecar write fails.

## Generated state JSON

The v2 sidecar records safe relative locations, hashes, effective limits, derivations,
artifacts, completion, and durable skipped scopes. It omits fresh observations caused
only by checked-out Gitignore rules or built-in cache and dependency exclusions, so
ordinary workspace caches do not create state changes. Scan, map, and doctor results
still report those observed exclusions. The sidecar contains no timestamps, host paths,
source bodies, environment values, or credentials. It reads legacy v1 full-document
ownership; v2 can also own only a visible managed section. See the schema-valid
[state shape example](../examples/state.json) and the
[configuration/state reference](../configuration-and-provenance.md).

## Next steps

- Run [doctor](doctor.md) to compare managed guidance with fresh repository evidence.
- Review ownership and replacement behavior in the
  [configuration/state reference](../configuration-and-provenance.md#slygentifystatejson).
- Use [map](map.md) to obtain fresh task-scoped context without expanding `AGENTS.md`.
