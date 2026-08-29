# Doctor Python API

Use the doctor API to compare fresh static repository evidence with configuration,
generated guidance, and managed provenance.

## Assess managed knowledge

`doctor_repository(path=".", *, git_executable=None)` returns an immutable
`DoctorResult`. It performs one fresh bounded assessment, is local and read-only, and
never executes discovered project validation commands. Its Git behavior and explicit
trusted-code boundary match `scan_repository`; see [safety boundaries](../safety.md).

`DoctorResult` exposes schema and producer versions, complete or partial completion,
repository and evidence records, diagnostics, and skipped scopes. Each
`DoctorDiagnostic` has a stable opaque code, severity, independent claim classification,
independent `problem`, `limitation`, or `notice` disposition, target, description, effect,
optional remediation, and evidence references.
It may additionally contain a machine-readable category and a safety rationale when
automatic repair is deliberately unavailable.
`doctor.inspection.partial` remains the compatible warning code and may occur once per
distinct fresh-scan cause. Its evidence identifies the originating scan diagnostic code
or skipped-scope boundary; its remediation names a resource limit only when that exact
limit caused the omission.

Human presentation is not a machine compatibility surface. `info`, `warning`, and
`error` severities are independent of disposition and claim classification.

Invalid caller input, Python objects, or JSON raise `DoctorInputError`. Failures that
prevent a trustworthy result raise `DoctorOperationalError`. Exact exception wording and
undocumented attributes are not compatibility contracts.

## Read and write doctor JSON

`dump_doctor_json(result)` returns deterministic UTF-8 `doctor-v1` bytes with one final
newline. `doctor_json_schema()` returns a fresh packaged Draft 2020-12 schema mapping.
Use `validate_doctor(value)` or `load_doctor_json(data)` for untrusted inputs. Reader
bounds, omitted absent optional fields, same-major additive-field behavior, and closed
producer output match scan JSON.
Readers default an omitted diagnostic disposition to `problem` for older
schema-major-1 documents; current producers always emit it.

See the [doctor task guide](../guides/doctor.md) for CLI exit semantics and automation
recipes, the [JSON Schema reference](../schemas.md) for the wire contract, and
[troubleshooting](../guides/troubleshooting.md) for safe diagnostic recovery.
