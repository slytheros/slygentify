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

Ordinary application creates new guidance, regenerates unchanged managed guidance, or
repairs a recoverable missing sidecar. When an unmanaged or human-edited safe regular
`AGENTS.md` exists, ordinary init preserves it and prints a paste-ready Slygentify
section for manual incorporation instead of replacing it. This result exits 4 and does
not create `.slygentify/state.json`. To retain existing human guidance while enabling
maintenance, review and apply `slygentify init PATH --adopt --dry-run` then
`slygentify init PATH --adopt`; it appends one visible marked section and manages only
that section. Dry-run never echoes surrounding human text. Missing managed, malformed,
and unsafe targets fail closed.

`--replace` may discard an existing regular `AGENTS.md`. It creates no backup and does
not merge text. It never authorizes replacing a symbolic link, directory, or malformed
state.

## Invalid provenance state

`.slygentify/state.json` is Slygentify's generated ownership and provenance record for
`AGENTS.md`. If init reports that it is invalid, it leaves both artifacts unchanged and
does not let `--replace` bypass that protection. First upgrade to the latest reviewed
build and rerun `slygentify init PATH --dry-run`. If it still fails, retain the sidecar
by renaming it to a new, non-existing backup name, then rerun the dry-run and apply only
an expected safe ownership state.

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
    print(diagnostic.code, diagnostic.recovery)

if plan.can_apply:
    result = apply_initialization(plan)
    print(result.changed_locations)
```

The plan contains the exact artifact bytes. Application revalidates it and reports exact
changed locations if guidance succeeds but the sidecar write fails.

## Generated state JSON

The v2 sidecar records safe relative locations, hashes, effective limits, derivations,
artifacts, completion, and skipped scopes. It contains no timestamps, host paths, source
bodies, environment values, or credentials. It reads legacy v1 full-document ownership;
v2 can also own only a visible managed section. See the schema-valid
[state shape example](../examples/state.json) and the
[configuration/state reference](../configuration-and-provenance.md).

## Next steps

- Run [doctor](doctor.md) to compare managed guidance with fresh repository evidence.
- Review ownership and replacement behavior in the
  [configuration/state reference](../configuration-and-provenance.md#slygentifystatejson).
- Use [map](map.md) to obtain fresh task-scoped context without expanding `AGENTS.md`.
