# Map Python API

Use the map API for bounded repository context for one logical path rather than a full
scan document.

## Create a fresh projection

`map_repository(path=".", *, scope=".", sections=None, max_bytes=8192,
git_executable=None)` performs a fresh scan and returns an immutable `ScanProjection`.
It does not cache or consume saved JSON, write repository files, or contact the network.
Its Git override and partial-scan behavior match `scan_repository`.

Scope is a repository-relative POSIX logical path and need not exist. Sections are
`orientation`, `workflows`, `architecture`, `automation`, and `boundaries`; the default
is orientation plus boundaries. The byte ceiling includes the final newline. Required
source metadata, repository, and owning-component context cannot be omitted; an
insufficient envelope raises `ScanError`.

`project_scan(result, *, scope=".", sections=None, max_bytes=8192)` creates the same
kind of projection from an existing trusted `ScanResult`.

## Navigate a projection

`navigation.owner` identifies the deepest matching component, `ancestors` are ordered
from root toward that owner, and `children` are direct-child stubs in deterministic path
order. Rerun map at a child path to drill down. The projection is component navigation,
not a recursive file, symbol, call, or semantic graph. Children are empty when
orientation was not selected; a capped child set is reported as an orientation/component
omission.

## Read and write map JSON

`dump_scan_projection_json(projection)` returns deterministic UTF-8
`scan-projection-v1` bytes. Use `scan_projection_json_schema()` for a fresh schema and
`validate_scan_projection(value)` or `load_scan_projection_json(data)` for untrusted
input. Parser bounds and same-major additive-field behavior match scan JSON.

Optional records and newly required evidence are indivisible units. Excluded selected
records are counted by section and record kind. Projection JSON has its own version and
cannot be loaded as a complete `ScanResult`.

See the [map task guide](../guides/map.md) for CLI use and the
[JSON Schema reference](../schemas.md) for the wire contract.
