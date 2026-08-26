# ADR 0006: Scan pipeline and extension boundaries

## Status

Accepted

## Context

Gitea issues #15 and #16 deliver the first normalized repository model and complete
read-only `scan` workflow. The implementation needs concrete module and dependency
boundaries before repository traversal, component detection, findings, JSON, and CLI
presentation grow together.

Public 1.0 must provide first-class Python and JavaScript/TypeScript inspection and
honestly represent generic or unsupported components. Later releases may inspect other
ecosystems. Maintainers also want to define repository-specific checks in the future.
Neither future direction should require changes to the stable `ScanResult` contract or
permit presentation, extensions, or repository-controlled configuration to bypass the
inspection safety boundary.

ADR 0004 fixes the public value types, top-level Python entry points, versioned JSON
contract, and compatibility rules. ADR 0005 fixes containment-aware traversal,
configuration, resource budgets, component-boundary policy, and the default local,
read-only, network-free, non-executing effect boundary. In particular,
`slygentify.toml` does not load plugins or authorize code execution.

The architecture must preserve those accepted constraints without prematurely making an
ecosystem or check plugin protocol part of the supported 1.x API. It should introduce
only seams that have an immediate implementation or testing purpose in issues #15 and
#16.

## Decision

Adopt a layered scan pipeline with one controlled repository-access boundary and two
private extension seams: detectors and checks.

The dependency direction is:

```text
CLI / public Python API
            |
            v
     scan orchestration
            |
            v
 inspection kernel -> bounded repository view
            |                    |
            |                    v
            |           ordered detectors
            |                    |
            v                    v
       normalization <- candidates and evidence
            |
            v
       private checks
            |
            v
        ScanResult
        /        \
versioned JSON   human text
```

Dependencies do not point upward in this diagram. CLI and presentation code do not
participate in inspection or normalization. Detectors and checks do not construct CLI
output or serialize JSON.

### Public contracts and private implementation

The public types and functions remain exactly the names selected by ADR 0004 and are
exported directly from `slygentify`. Their implementation modules may be private and are
not compatibility surfaces merely because they contain public objects.

The scan pipeline uses private intermediate types for file metadata, observations,
component candidates, finding candidates, and the pre-result normalized snapshot. These
types may evolve before and during public 1.x without becoming supported construction or
serialization contracts. Only the final assembler creates the public frozen dataclasses.

`scan_repository` is the application entry point. The CLI is a thin adapter over that
entry point. The JSON serializer and human presenter independently consume the same
`ScanResult`; human-readable output is never used as an intermediate data model.

### Inspection kernel and repository capability

The inspection kernel exclusively owns repository filesystem access. It resolves the
selected root, loads and validates root configuration before traversal, applies scope and
sensitive-content rules, accounts for resource budgets, validates entry identity, and
records partial completion and skipped scopes.

Detectors and checks never receive an unrestricted repository root or ambient filesystem
API as part of their contract. A detector receives a private bounded repository view
whose operations accept safe repository-relative paths and enforce the kernel's scope,
identity, content, and budget policies on every access. The real root remains private to
the kernel.

The repository view exposes only the smallest operations needed by implemented
detectors. It is a capability boundary, not a general virtual filesystem. Command
execution, network access, writes, environment lookup, and descendant link following are
absent.

### Detectors

A detector interprets permitted repository evidence and emits internal observations,
evidence candidates, component candidates, or unresolved boundary candidates. It does
not decide public identifiers, global ordering, component conflicts, final claim
classification, or presentation.

Each detector has a stable namespaced rule identifier and positive integer rule version
for deterministic provenance. The scan orchestrator supplies an explicit ordered tuple
of built-in detectors. There is no import-time global mutable registry, entry-point
discovery, module-name configuration, or automatic loading of repository code.

The initial registry contains only detectors required by ready implementation issues.
Python, JavaScript/TypeScript, generic unsupported-manifest, and later ecosystem
detectors use the same private contract. Ecosystem and component-kind values remain
extensible strings as required by ADR 0004; detector modules do not require changes to a
central closed language enumeration.

Shared format parsers and workspace helpers may be extracted when two concrete detectors
need them. A detector-specific parser remains with that detector until reuse is
demonstrated.

### Normalization

Normalization is the only layer that converts detector output into the canonical domain
representation. It owns:

- deterministic opaque identifier derivation;
- stable collection ordering;
- evidence-reference integrity;
- conflict and uncertainty preservation;
- the effective one-component-per-root-path policy for 1.0; and
- validation of candidates before they enter the public result.

Detectors report competing evidence rather than applying precedence silently. This
allows the normalizer to preserve a configured declaration, detected evidence, and any
conflict between them without teaching every ecosystem detector the same policy.

### Checks

A check evaluates the immutable normalized snapshot and emits internal finding
candidates. Checks have stable namespaced identifiers and positive integer versions, and
their output must use existing subject and evidence references. The engine validates
those references before assembly.

Checks are pure by contract: they do not receive the repository view, open files, execute
commands, contact the network, mutate configuration, or depend on presentation. A rule
that needs new repository content is inspection logic and belongs in a detector operating
through the bounded view; a rule that evaluates already normalized facts is a check.

The initial check contract and registry are private. Built-in checks are registered
explicitly and deterministically. Diagnostic behavior for invalid or failed checks must
remain compatible with the later public diagnostic decision and must not fabricate a
successful complete result.

### Future user-defined checks

Public 1.0 does not expose a plugin API or load user code during default scan. The private
check boundary deliberately permits two later extension paths without changing the scan
model:

1. A future versioned declarative policy schema may compile safe repository-defined rules
   into the same internal check representation. Adding that schema requires its own
   requirements, compatibility rules, and human review.
2. Arbitrary executable checks, if later supported, require explicit invocation and a
   separately reviewed execution and trust contract. The preferred interoperability
   boundary is versioned `ScanResult` JSON so checks can run out of process and need not
   be written in Python. Repository configuration never automatically authorizes or
   loads them.

Results produced outside core scan are not silently inserted into Slygentify-authored
JSON as though they were core verified findings. Any future enriched-result contract must
identify the check producer and preserve provenance.

### Module growth and conformance testing

Implementation begins with the smallest cohesive private modules or packages required by
issues #15 and #16. Empty ecosystem packages, a generic plugin manager, a dependency
injection container, and a public extension SDK are not created in anticipation of later
work.

Detector and check conformance tests exercise deterministic IDs and ordering, evidence
integrity, effect boundaries, invalid output, and partial-result behavior. Ecosystem
fixtures test adapters through the scan pipeline rather than only testing parsers in
isolation. CLI, JSON, and Python tests assert parity against the same normalized result.

## Consequences

Filesystem and resource safety remain centralized and reviewable. Adding an ecosystem
does not require changes to CLI or JSON presentation, while adding a check does not
require raw repository access. Normalization provides one place for deterministic
identity, ordering, conflicts, and the current component policy.

Private extension seams allow the implementation to learn from Python and
JavaScript/TypeScript support before promising a third-party API. This reduces immediate
compatibility and security risk, but external authors cannot install in-process plugins
in public 1.0.

Checks limited to normalized facts are easier to make deterministic and safe, but a
custom rule that needs additional source content requires a detector or a separately
authorized external workflow. An out-of-process JSON boundary adds process and result
provenance work if executable checks are later implemented, but avoids coupling custom
checks to Python internals and prevents repository configuration from becoming an
execution mechanism.

Private candidate models add a conversion stage before the public dataclasses. That
additional code is accepted because it keeps incomplete, conflicting, or invalid
detector output from leaking directly into the stable contract.

An explicit built-in registry is less dynamic than automatic discovery. It is
intentional: detector ordering and enabled behavior remain inspectable, deterministic,
and testable. A later extension decision can replace registry construction without
changing detector responsibilities or public scan documents.

## Alternatives considered

### Public plugin protocol in 1.0

A public `EcosystemPlugin` or `Check` protocol would make third-party extension possible
immediately. It is rejected because Python and JavaScript/TypeScript adapters have not
yet supplied enough implementation evidence to stabilize the protocol, and safe plugin
discovery, version negotiation, isolation, failure handling, and compatibility would
materially expand issues #15 and #16.

### Configuration-driven module or entry-point loading

Loading a named module from `slygentify.toml` would be convenient for repository-local
customization. It is rejected because repository configuration is untrusted data, ADR
0005 explicitly excludes plugin loading, and default scan is non-executing.

### Give detectors direct filesystem paths

Direct paths produce simple adapters and make existing ecosystem libraries easy to call.
They are rejected because every detector could bypass containment, sensitive-content,
identity, and budget enforcement. The bounded repository view keeps those effects in one
auditable layer.

### Let detectors create public model objects

This removes candidate and normalization types. It is rejected because component
conflict resolution, claim classification, identity, and ordering would become
duplicated ecosystem behavior and future changes could churn the public model.

### Let checks inspect repository files

One general rule interface would be superficially simpler. It is rejected because it
mixes evidence acquisition with policy evaluation, makes user-defined checks part of the
repository safety boundary, and weakens deterministic reuse of the normalized model.

### General event bus or dependency-injection framework

These abstractions could decouple pipeline stages. They are rejected because explicit
function calls and ordered registries make control flow, effects, ordering, and failure
semantics easier to inspect and test for the current workflow.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
