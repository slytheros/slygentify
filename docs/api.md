# Python API reference

Slygentify exposes supported immutable values and entry points from the `slygentify`
package. Names in implementation modules beginning with an underscore are private.
Package, scan, map, doctor, and initialization-state versions are independent
compatibility surfaces.

## Choose an API topic

| Need | Reference |
| --- | --- |
| Fresh repository inspection and complete scan JSON | [Scan API](api/scan.md) |
| Static managed-knowledge assessment and exit-equivalent diagnostics | [Doctor API](api/doctor.md) |
| Bounded context for one logical path | [Map API](api/map.md) |
| Reviewable or applied root guidance generation | [Initialization API](api/initialization.md) |

Use the task guides for goal-oriented CLI and Python examples, the
[JSON Schema reference](schemas.md) for wire contracts, and
[migration guidance](migration.md) for public compatibility rules.

## Common API rules

Public result values are immutable and constructors are intended only for trusted,
already typed input. Use the documented `validate_*` or `load_*_json` functions for
untrusted Python values and JSON. Human-readable exception text is not a machine
contract; rely only on documented exception types and attributes.

The default operations are local, read-only, and network-free. Scan, map, and doctor
may use the reviewed fixed Git tracked-path lookup. An explicit `git_executable` is
trusted, unsandboxed code with possible arbitrary effects; see
[safety boundaries](safety.md#fixed-git-lookup).

## Generated public reference

The reference below is generated from the source signatures and docstrings without
importing Slygentify. Only names explicitly exported by `slygentify.__all__` are included.
Private modules, third-party validation types, and undocumented attributes are not public
compatibility surfaces.

::: slygentify
    options:
      members:
        - Component
        - ComponentRelationship
        - Diagnostic
        - DoctorDiagnostic
        - DoctorInputError
        - DoctorOperationalError
        - DoctorResult
        - Evidence
        - InitializationDiagnostic
        - InitializationError
        - InitializationPlan
        - InitializationResult
        - Finding
        - ProjectionOmission
        - ProjectionNavigation
        - ProjectionScope
        - ProjectionSection
        - Repository
        - ScanError
        - ScanResult
        - ScanProjection
        - ScanValidationError
        - SkippedScope
        - apply_initialization
        - dump_scan_json
        - dump_scan_projection_json
        - dump_doctor_json
        - implements
        - load_scan_json
        - load_scan_projection_json
        - load_doctor_json
        - map_repository
        - plan_initialization
        - scan_json_schema
        - scan_projection_json_schema
        - scan_repository
        - doctor_json_schema
        - doctor_repository
        - project_scan
        - validate_scan
        - validate_scan_projection
        - validate_doctor
