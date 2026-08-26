# Map task context

Use map when an agent or tool needs bounded context for one logical path rather than a
complete scan document.

## Canonical JSON

```console
slygentify map path/to/repository --scope src/example.py > map.json
slygentify map path/to/repository --scope apps/api \
  --section workflows --section architecture > map.json
```

Map has no text output mode. Successful standard output is exactly one canonical
`scan-projection-v1` document. The logical scope need not exist, which supports planning
before creating a file. See the validated [minimal map document](../examples/map.json).

The default sections are `orientation` and `boundaries`; other choices are `workflows`,
`architecture`, and `automation`. The default byte ceiling is 8 KiB including the final
newline. Required repository and owner context is never silently removed.

## Python

Fresh scan and projection:

```python
from slygentify import dump_scan_projection_json, map_repository

projection = map_repository(
    "path/to/repository",
    scope="src/example.py",
    sections=("orientation", "workflows", "boundaries"),
)
document = dump_scan_projection_json(projection)
```

Project an existing trusted scan:

```python
from slygentify import project_scan

projection = project_scan(result, scope="src/example.py", max_bytes=8192)
```

Follow `navigation.children` by rerunning map at a child's component path until
`navigation.owner` identifies the relevant component. Map is component navigation, not a
recursive file, symbol, call, or semantic graph.

## Next steps

- Run a complete [scan](scan.md) when you need every retained record and diagnostic.
- Use the [JSON Schema reference](../schemas.md) and [Python API reference](../api.md)
  when consuming projections in automation.
- Rerun map at a child component path to continue bounded navigation.
