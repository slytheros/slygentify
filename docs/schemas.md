# JSON Schema reference

Slygentify owns four packaged JSON Schemas using JSON Schema Draft 2020-12. The package
copies under `src/slygentify/schemas/` are normative. This page is an index and
compatibility guide; it does not duplicate or redefine their constraints.

| Document | Schema identifier | Producer or artifact | Canonical source |
| --- | --- | --- | --- |
| Complete scan | `schemas/scan-v1.schema.json` | `dump_scan_json` | [`scan-v1.schema.json`](https://github.com/slytheros/slygentify/blob/develop/src/slygentify/schemas/scan-v1.schema.json) |
| Task map | `schemas/scan-projection-v1.schema.json` | `dump_scan_projection_json` | [`scan-projection-v1.schema.json`](https://github.com/slytheros/slygentify/blob/develop/src/slygentify/schemas/scan-projection-v1.schema.json) |
| Static doctor | `schemas/doctor-v1.schema.json` | `dump_doctor_json` | [`doctor-v1.schema.json`](https://github.com/slytheros/slygentify/blob/develop/src/slygentify/schemas/doctor-v1.schema.json) |
| Initialization state | `schemas/state-v1.schema.json` | `.slygentify/state.json` | [`state-v1.schema.json`](https://github.com/slytheros/slygentify/blob/develop/src/slygentify/schemas/state-v1.schema.json) |

Every schema is checked during tests with an independent Draft 2020-12 validator. Scan,
map, and doctor also expose public functions that return fresh schema dictionaries so a
caller cannot mutate shared package state. Initialization state is a managed artifact,
not a public Python result type.

## Consumer rules

- Treat `schema_version` and the schema `$id` as the wire compatibility identity.
- Use Slygentify's public loaders for untrusted scan, map, or doctor JSON when possible;
  they enforce duplicate-key, encoding, size, depth, collection, and graph bounds that a
  JSON Schema validator alone does not provide.
- Schema-major-1 readers ignore unknown object properties for compatible additive
  evolution. Current producers emit only canonical declared fields.
- Do not load a task map as a complete scan or use initialization state as cached scan
  authority.

Validated minimal documents are available for [scan](examples/scan.json),
[map](examples/map.json), [doctor](examples/doctor.json), and initialization
[state](examples/state.json). They demonstrate shape, not a claim that every repository
has no components or diagnostics.

Richer canonical documents generated from the first-repository tutorial fixture are
available for [scan](examples/representative-scan.json),
[map](examples/representative-map.json), and
[doctor](examples/representative-doctor.json). CI regenerates the same results through the
public APIs and fails if checked-in bytes, schema validity, or the intended partial and
diagnostic scenarios drift.
