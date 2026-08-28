# Initialize repository guidance

Use init to create concise root `AGENTS.md` guidance and its deterministic ownership
sidecar. Review before writing.

## Exact dry-run

```console
slygentify init path/to/repository --dry-run
```

Dry-run validates the applicable preconditions and prints the exact proposed artifacts
without writing either file. Init has no JSON output mode. Its human dry-run is the
review interface; `.slygentify/state.json` is a generated artifact, not command JSON.

## Apply a reviewed plan

```console
slygentify init path/to/repository
```

Ordinary application creates new guidance, regenerates unchanged managed guidance, or
repairs a recoverable missing sidecar. When an unmanaged or human-edited safe regular
`AGENTS.md` exists, ordinary init preserves it and prints a paste-ready Slygentify
section for manual incorporation instead of replacing it. This result exits 4 and does
not create `.slygentify/state.json`; use `--replace` only when intentionally adopting
the generated document. Dry-run retains its complete exact-artifact review and also
exits 4. Missing managed, malformed, and unsafe targets fail closed.

`--replace` may discard an existing regular `AGENTS.md`. It creates no backup and does
not merge text. It never authorizes replacing a symbolic link, directory, or malformed
state.

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

The sidecar records safe relative locations, hashes, effective limits, derivations,
artifacts, completion, and skipped scopes. It contains no timestamps, host paths, source
bodies, environment values, or credentials. See the schema-valid
[state shape example](../examples/state.json) and the
[configuration/state reference](../configuration-and-provenance.md).

## Next steps

- Run [doctor](doctor.md) to compare managed guidance with fresh repository evidence.
- Review ownership and replacement behavior in the
  [configuration/state reference](../configuration-and-provenance.md#slygentifystatejson).
- Use [map](map.md) to obtain fresh task-scoped context without expanding `AGENTS.md`.
