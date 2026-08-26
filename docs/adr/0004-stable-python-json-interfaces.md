# ADR 0004: Stable Python and JSON interfaces

## Status

Accepted — scan projection policy extended by ADR 0009

## Context

ADR 0002 makes named Python entry points and explicitly selected versioned JSON
documents supported machine contracts at public 1.0. It leaves their exact types,
serialization library, schema, and compatibility rules to the pre-public planning item decision in Gitea
pre-public approval record.

The pre-public planning item research in pre-public approval record compared frozen standard-library dataclasses with
a private Pydantic adapter, public Pydantic models, msgspec structures, and a pure
standard-library implementation. The comparison exercised nested validation,
serialization, JSON Schema generation, error handling, imports, installed size, and the
supported Python versions. It recommended conventional public Python values with a
replaceable private validation engine.

An authorized human selected **Frozen stdlib dataclasses + private Pydantic
TypeAdapter** in pre-public approval record. This ADR specifies the complete proposed public boundary for
review. It does not implement the model, add a dependency, publish a schema, or claim
that `scan` exists. Those changes remain owned by their dependent feature issues and
must receive requirements, tests, and traceability when implemented.

## Decision

Adopt frozen standard-library dataclasses as the public Python representation and keep
Pydantic validation private behind Slygentify-owned interfaces.

### Public Python value types

The public value types are:

- `ScanResult`;
- `Repository`;
- `Component`;
- `Evidence`;
- `Finding`;
- `Diagnostic`; and
- `SkippedScope`.

Each type uses `@dataclass(frozen=True, slots=True, kw_only=True)`. Immutable collections
use tuples and serialize as JSON arrays. Immutability is shallow in the Python sense;
the public contract does not claim that annotations alone enforce runtime types.

Direct constructors are supported for trusted, already typed values. Documented class
names, top-level import paths, fields, keyword-only constructor parameters, and field
meanings become part of the 1.x compatibility surface when released at 1.0. Essential
cheap local and cross-field invariants belong in `__post_init__`. Callers must use the
validation entry points for untyped mappings, JSON, and other untrusted input because
ordinary dataclass construction does not recursively validate annotations. Exact
generated `repr` text and hash values are not compatibility promises.

`ScanResult` carries:

- integer `schema_version`, initially `1`, independently of the package version;
- `producer_version` identifying the Slygentify package version;
- a closed completion state of `complete` or `partial`;
- one `Repository`; and
- ordered tuples of components, evidence, findings, diagnostics, and skipped scopes.

The supporting types preserve the following normalized information:

- `Repository` has a deterministic opaque identifier, a repository-relative root
  normally represented as `.`, an observable repository kind, and evidence references.
- `Component` has a deterministic opaque identifier, a safe repository-relative path,
  extensible ecosystem and kind values, and evidence references.
- `Evidence` has a deterministic opaque identifier, source kind, safe relative
  location, an optional semantic locator, concise observation, and an optional
  verification method. TOML locators use dotted keys and JSON locators use RFC 6901
  JSON Pointers. Evidence does not reproduce raw inspected values or sensitive content.
- `Finding` has a deterministic opaque identifier, extensible stable code, claim
  classification, subject reference, concise summary, and evidence references.
- `Diagnostic` has a deterministic opaque identifier, extensible stable code,
  subject or safe relative location, actionable message, and evidence references.
- `SkippedScope` records a safe relative scope, stable reason or limit code, effective
  limit, consumed amount, and known omitted scope so partial work cannot appear
  complete.

The claim classification is the closed string vocabulary `verified`, `inferred`,
`recommended`, and `unknown`. It has no numeric confidence. Diagnostic severity and
code values remain owned by the later diagnostic decision. Exact ecosystem identifiers
remain owned by the ecosystem capability decision. Those downstream decisions may fill
the explicitly extensible spaces but may not change this claim vocabulary or the
complete/partial representation within schema major 1.

Identifiers derive deterministically from normalized repository evidence. They never
derive from random UUIDs, host-absolute paths, timestamps, or traversal order. Serialized
paths use repository-relative POSIX form and never disclose a host-absolute path.

### Public Python entry points and errors

The supported names are exported directly from `slygentify`. Implementation modules
remain private and are not compatibility surfaces. The public entry points are:

```python
def validate_scan(value: object) -> ScanResult: ...
def load_scan_json(data: str | bytes) -> ScanResult: ...
def dump_scan_json(result: ScanResult) -> bytes: ...
def scan_json_schema() -> dict[str, object]: ...
def scan_repository(...) -> ScanResult: ...
```

`scan_repository` is the future scan entry point; its arguments remain owned by the
scan feature, but its public name and `ScanResult` return type replace that feature's
provisional `RepositoryReport` wording. `scan_json_schema` returns a fresh mapping so a
caller cannot mutate private shared schema state.

`ScanValidationError` is the public failure type for invalid scan objects or JSON.
Pydantic exceptions never cross the supported boundary. The exception type and any
explicitly documented attributes are stable; human-readable message wording and
undocumented upstream error details are not machine contracts.

The package may add new public entry points compatibly. A Python module, class,
function, exception, attribute, method, or generated dataclass behavior is private
unless this ADR or later public documentation explicitly promotes it.

### Private validation and dependency boundary

Use reusable Pydantic v2 `TypeAdapter` instances privately for boundary validation,
JSON conversion, serialization conformance, and development-time schema comparison.
Pydantic `BaseModel`, `TypeAdapter`, configuration, core schemas, generated schema
formatting, and `ValidationError` remain private implementation details.

When implementation begins, adopt the initial runtime dependency bound
`pydantic>=2.13,<3`. The lower bound is the version exercised by pre-public planning item and the upper
bound fails closed across a Pydantic major change. Dependency maintenance must revisit
the cap before Pydantic 3 becomes necessary; changing the private engine is compatible
when the supported Slygentify behavior remains unchanged.

Validation is strict for scalar types. Every selected public field type receives an
explicit strict-mode test because Pydantic's strict JSON behavior varies by type.
Numbers must be finite. Parsing must remain bounded under ADR 0005; implementation
requirements define and test the applicable document-size, nesting, string, and
collection limits.

Producer validation forbids undeclared fields and validates an object immediately
before serialization. This catches invalid values introduced through ordinary
dataclass constructors and prevents Slygentify from emitting fields outside its
declared contract.

### Normative JSON Schema and serialization

The wire schema major begins at integer `1` and is independent of the Slygentify package
version. The normative artifact is a checked-in, Slygentify-owned JSON Schema using
Draft 2020-12:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "schemas/scan-v1.schema.json"
}
```

The future packaged artifact is
`src/slygentify/schemas/scan-v1.schema.json`. The package-relative `$id` avoids a
dependency on an unconfirmed public domain. Pydantic-generated schema is a
development-time synchronization input, not the published contract. Tests must detect
drift between the private adapter, checked-in schema, public model, and representative
documents.

Same-major readers ignore unknown object properties so an older 1.x reader can consume
additive output from a newer producer. The normative consumer schema therefore remains
open to undeclared properties. This policy is intentionally paired with the separate
closed producer validation above: Slygentify emits and accepts as its own output only
declared canonical fields. Consumer typo detection is consequently weaker than under a
globally closed schema.

Serialization has these deterministic rules:

- emit UTF-8 bytes without a byte-order mark, using LF line endings and one final
  newline;
- emit a fixed readable field order, while assigning no semantic meaning to JSON object
  key order;
- omit optional values unless `null` has a documented domain meaning;
- reject NaN, positive infinity, and negative infinity;
- sort components and evidence by deterministic identifier and path or location;
- sort findings and diagnostics by stable code, subject, and identifier;
- sort skipped scopes by scope and reason; and
- never allow timestamps, host paths, set iteration, filesystem traversal races, or
  random identifiers to affect emitted bytes.

The same normalized input, effective configuration, repository content, and producer
version must produce identical JSON bytes.

### Compatibility, deprecation, and migration

The following changes are compatible within schema major 1:

- adding an optional field that older readers can ignore;
- adding a finding or diagnostic code in an explicitly extensible code space;
- adding an ecosystem identifier;
- relaxing validation without changing the meaning of an existing valid document;
- adding a new named Python entry point without changing an existing one; and
- replacing private validation machinery while preserving public behavior.

The following changes are breaking and require a new schema major where they affect the
wire contract:

- adding a required field;
- removing or renaming a field;
- changing a field's JSON type, cardinality, requiredness, or established meaning;
- changing promised deterministic array ordering;
- adding or changing a member of a closed vocabulary;
- changing the representation or meaning of complete and partial results; or
- removing or incompatibly changing a supported Python name, constructor, argument,
  return type, exception contract, or documented field.

During public 1.x, deprecated Python names remain available through the end of the 1.x
line and emit a standard `DeprecationWarning`. Deprecated JSON fields remain accepted
through schema major 1. After a documented transition, producers emit only the
canonical replacement; an alias never causes old and new spellings to be emitted
together.

Removal occurs only in the next applicable major release. A breaking wire change uses a
new schema major, retains an explicit reader or migration path for supported older
documents according to the release policy, and ships a migration guide. Package and
wire major versions need not advance together. Before 1.0, ADR 0002 continues to permit
explicit refinement without a compatibility promise, provided repository data is
preserved and material migrations are documented.

If later end-to-end profiling shows that model conversion or serialization materially
causes failure of an accepted startup, throughput, or memory target, the private engine
may be re-evaluated. Synthetic microbenchmark superiority alone is not a trigger, and
the public wrappers and dataclass representation remain unchanged unless a separate
breaking decision is approved.

## Consequences

Public consumers receive conventional immutable Python values without inheriting a
third-party model type. Slygentify retains control over its constructors, errors,
schema, and compatibility policy and can replace the private validation engine without
changing supported imports.

Ordinary construction remains convenient for trusted typed code but does not provide
recursive runtime validation. Producer revalidation and clear documentation are
required to prevent an invalid directly constructed graph from becoming JSON.

Pydantic adds a compiled core and transitive runtime packages. Its current wheel matrix
covers Slygentify's supported Python versions, but future wheel availability and the
`<3` cap remain packaging and dependency-resolution risks. Raw upstream error and schema
changes are contained by exception translation, a checked-in schema, and conformance
tests.

Open same-major readers allow additive evolution but accept misspelled unknown consumer
properties. Closed producer checks prevent Slygentify from generating such properties;
consumers needing typo detection may apply an additional strict policy outside the
forward-compatible contract.

Deterministic identifiers, safe relative paths, bounded parsing, and evidence summaries
support the interaction and repository-safety contracts. They also require deliberate
normalization and comprehensive contract tests in dependent implementation work. This
decision neither authorizes sensitive reads nor weakens any ADR 0005 effect boundary.

The top-level import surface is simple for users but makes every promoted name an
ongoing 1.x obligation. Private modules and human-readable messages remain free to
evolve so long as the supported behavior and meaning do not change.

## Alternatives considered

### Public Pydantic `BaseModel`

Public models would validate ordinary construction and reduce wrapper code. They were
rejected because Pydantic inheritance, constructors, methods, schema formatting, and
error behavior would become public compatibility concerns. Upstream explicitly permits
some schema-reference and error-shape changes within Pydantic v2.

### Public msgspec `Struct`

The pre-public planning item prototype found msgspec faster and smaller for a synthetic nested decode.
It was rejected because ordinary structure construction still does not validate
annotations, the public types would inherit a compiled third-party representation, and
no accepted end-to-end performance requirement justifies that coupling. It remains a
private-engine candidate only if later profiling demonstrates a material product need.

### Pure standard-library dataclasses

Avoiding a validation dependency would reduce installed dependencies. It was rejected
because Slygentify would then own recursive validation, useful nested error locations,
JSON conversion, strict decoding, and schema synchronization. That bespoke maintenance
surface is larger and riskier than the contained private dependency.

### Closed schemas for readers and producers

Rejecting every unknown property would catch consumer misspellings earlier. It was
rejected as the default because an older same-major reader would reject a newer
producer's optional additive fields. The selected split keeps readers forward-compatible
and producers conformant.

### Public third-party schema and errors

Publishing Pydantic's generated schema or raw validation errors would require less
normalization. It was rejected because upstream minor releases may change those shapes,
transferring Pydantic's compatibility policy into Slygentify's public contract.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
