# ADR 0009: Thin AGENTS.md and task-scoped operating maps

## Status

Accepted

## Context

ADR 0002 defines a trustworthy local workflow in which `scan` exposes a small,
reviewable operating map and `init` bootstraps concise component-aware agent guidance.
ADR 0004 makes explicitly selected versioned JSON documents and named Python entry
points public 1.0 contracts. ADR 0008 selects an editable root `AGENTS.md` with a
separate deterministic provenance sidecar and digest-guarded regeneration.

The first component-aware generator projected many normalized scan records directly
into `AGENTS.md`. The pre-public planning item usefulness review in pre-public approval record tested that design against
the 20 pinned public repositories. Its 2026-08-20 candidate did not pass review. The
median artifact was approximately 9.9 KiB; twelve artifacts exceeded roughly 2,000
tokens; five were effectively at the 24 KiB ceiling; fourteen still omitted facts; and
partial-boundary presentation enumerated 110 individual paths. Representative outputs
spent persistent instruction context on CI shell bodies, runtime matrices, repeated
tool declarations, and skipped certificate paths without consistently identifying an
authoritative local workflow.

This is not merely a presentation defect. `AGENTS.md` is loaded as persistent
instructions by supporting agents, while scan results are current repository
observations. Codex, for example, concatenates applicable instruction files and has a
32 KiB default combined project-instruction limit. A 24 KiB generated root artifact can
therefore crowd out global or more specific instructions. Other consumers have
different scope, precedence, and size behavior, as recorded by pre-public planning item in pre-public approval record.

A generic router-only document would minimize context but would make `init` produce
nearly identical output for every repository and cease to provide component-aware
orientation. Conversely, requiring every agent to load and filter the complete scan
JSON duplicates selection logic, risks broken evidence closure, and can still inject a
large amount of irrelevant repository-controlled text into model context.

pre-public approval record requests the corrective human decision. It blocks implementation and the
completion of pre-public approval record. This ADR changes the content responsibility selected by ADR
0008 but preserves ADR 0008's artifact ownership, regeneration, replacement, atomicity,
and recovery policy.

## Decision

Adopt a **thin bootstrap artifact with task-scoped operating-map projections**.

### Public workflow and command surface

Public 1.0 has four top-level commands:

```text
slygentify scan
slygentify map
slygentify init
slygentify doctor
```

`scan` remains the complete normalized repository inspection. `map` performs a fresh
scan and selects a bounded, evidence-closed machine-readable projection for one logical
repository path. `init` generates concise persistent bootstrap guidance that explains
how to request current map context. `doctor` retains its accepted future drift and
readiness responsibilities.

`map` is local, read-only, network-free, and non-executing under the same default
inspection and trusted-Git boundaries as `scan`. It does not cache projections, write
repository files, or accept a saved scan document in public 1.0.

### Thin root AGENTS.md

`init` continues to manage only root `AGENTS.md` and `.slygentify/state.json`. It does
not generate nested, override, imported, hidden-region, front-matter, or vendor-specific
instruction files.

The generated Markdown contains only:

1. a stable explanation that fresh `slygentify map` output is authoritative for current
   repository facts, including how to select map sections and interpret classifications
   and evidence;
2. a bounded bootstrap index of primary components; and
3. conservative safety guidance separating repository reads, writes, command execution,
   network access, and external effects.

The default component index contains at most eight non-auxiliary components. It lists
the root first and then primary descendants in deterministic breadth-first path order.
Each entry contains only repository-relative path, component kind, ecosystem facets,
and one canonical evidence path. Additional components produce an aggregate omission
count. No detected components produce an explicit unknown. A partial generation scan
produces one summary warning rather than enumerating skipped paths.

Generated `AGENTS.md` does not contain discovered command text, CI bodies, runtime
matrices, tool inventories, general findings, relationships, diagnostics, or individual
skipped scopes. The provenance artifact relationship includes only repository or
component evidence visibly cited by the generated document.

The default generated artifact is designed to remain near 2 KiB and may never exceed
4 KiB. Root configuration may deterministically raise or disable both the byte and
component-entry bounds:

```toml
[init]
max_agents_bytes = 4096
max_component_entries = 8
```

Each value is either a supported positive integer or `"unlimited"`. Values too small
to hold the fixed bootstrap guidance are invalid. Raising or disabling one bound does
not alter the other. Configuration remains committed input, and the existing complete
configuration digest binds it into provenance. An invocation-only generation override
is not provided because an omitted rerun option would change candidate bytes and make
regeneration unpredictable.

ADR 0008's ownership states, exact dry-run, ordinary refusal of unmanaged or edited
content, explicit replacement, per-file atomic writes, partial-write reporting, and
recovery behavior remain authoritative and unchanged.

### Task-scoped map command

The public command is:

```text
slygentify map [PATH]
  [--scope REPOSITORY_RELATIVE_PATH]
  [--section SECTION]...
  [--max-bytes INTEGER|unlimited]
  [--git-executable PATH]
```

`PATH` and `--scope` default to `.`. The scope is a safe repository-relative POSIX
logical path and need not exist, which permits orientation for a planned file. Absolute
paths, backslashes, NULs, and `.` or `..` segments are invalid except that the complete
root value `.` is allowed. The deepest component containing the logical path owns the
projection. An unmatched path receives repository-level context and top-level component
navigation rather than an error.

Map navigation is an iterative, one-hop component drill-down rather than a recursive
listing. A caller begins at scope `.`, resolves the component records referenced by the
explicit direct-child navigation IDs, and reruns map using the selected component path.
Each result identifies ancestor component IDs in root-to-parent order, the owning
component ID when one exists, and the included direct-child component IDs in
deterministic path order. Child IDs reference only component stubs present in that
projection; excluded stubs remain visible through the orientation/component omission
count. When orientation is not selected, the child list is empty. This navigation does
not enumerate files or symbols and is the handoff point to independent code-navigation
tools.

The closed initial section vocabulary and order are:

1. `orientation`: repository, owner, ancestors, direct child navigation stubs,
   relationships among included components, and identity, runtime, and manager findings;
2. `workflows`: non-CI setup, run, test, lint, format, build, and other declared task
   findings;
3. `architecture`: component-level operating entry points, frameworks, declared package
   or build dependencies, and tools evidenced by repository manifests and
   configuration;
4. `automation`: attributable CI workflow and command findings; and
5. `boundaries`: applicable unknowns, recommendations, conflicts, diagnostics, and
   skipped scopes intersecting the requested scope.

With no `--section`, `map` selects `orientation` and `boundaries`. Explicit section
options select exactly the deduplicated requested sections in canonical order. The
default canonical JSON ceiling is 8 KiB, including the final newline. A positive
per-call value may change the ceiling, and `unlimited` explicitly removes it.

Selection treats each record and its newly required evidence as one indivisible unit.
Required source, repository, scope, and owning-component context is retained first,
followed by applicable boundaries, relationships, requested findings, and child
navigation stubs. The projection never references absent evidence. Selected records
that do not fit produce deterministic omission counts by section and record kind. If
the required envelope cannot fit, the operation fails rather than emitting invalid or
misleading JSON. A partial source scan remains a successful partial source result and
is identified in the projection.

### Versioned projection interfaces

Add a distinct `scan-projection-v1` JSON document and corresponding frozen public
standard-library dataclasses. A filtered projection is never represented as or accepted
by `ScanResult`.

The projection records:

- its schema version and the source scan schema version;
- producer version, canonical source-scan SHA-256, and source completion;
- requested scope and matched component identity and path, when any;
- explicit ancestor, owner, and included direct-child component navigation references;
- selected sections;
- the repository and selected component, relationship, finding, diagnostic,
  skipped-scope, and evidence records; and
- explicit output-limit omissions by section and record kind.

Existing public record types are reused. New public projection, scope, navigation,
omission, and section types follow ADR 0004's immutable-value rules. Public entry points
provide a fresh repository map, projection of an existing trusted `ScanResult`,
validation, canonical load and dump, and a fresh packaged JSON Schema mapping.

Canonical ordering, UTF-8 and finite-value behavior, parser resource bounds, schema
distribution, forward-compatible unknown-property handling, public error isolation,
and CLI/JSON/Python parity follow ADR 0004. The new document has an independent schema
version so its projection and omission semantics can evolve without weakening the
meaning of complete scan JSON.

### Boundary with code-intelligence tools

Slygentify owns repository operating context: component and workspace boundaries,
attributable workflows, declared ecosystem metadata, inspection limitations, evidence,
and authorization boundaries. It does not build or persist symbol, syntax, import,
call, control-flow, inheritance, semantic-search, or change-impact graphs, and it does
not replace code search, language servers, or code-intelligence products.

The `architecture` section remains at repository and component granularity. Entry
points identify attributable application, service, package, or command surfaces rather
than enumerating functions, classes, handlers, or other symbols. Dependencies describe
declared package, workspace, or build relationships rather than source import graphs.
Relationships describe repository components and their declared composition rather
than file, symbol, call, inheritance, or control-flow edges. Child navigation stubs
identify component paths only.

Projection paths and component identities should be directly usable as scopes for
independent code-navigation tools. This interoperability must remain provider-neutral:
public 1.0 does not detect, invoke, configure, proxy, consume an index from, or require
CodeGraph or any equivalent product. Absence or staleness of an independent code index
does not change scan completion or projection completion. Any future integration with
a code-intelligence provider requires a separate ready decision and must preserve the
local-first, effect, trust, and provenance boundaries.

### Acceptance boundary

The initialization usefulness gate reviews the integrated bootstrap-to-map workflow
for all 20 pinned repositories. Every default `AGENTS.md` must remain at or below 4 KiB,
the corpus median must remain at or below 2 KiB, and every default root projection must
remain at or below 8 KiB. Capped indexes and projections identify omissions. Generated
guidance contains no raw command text or individual skipped-scope enumeration.

The human review covers bootstrap clarity, component-index accuracy, map navigation,
boundary honesty, safety, and concision. Candidate generation remains local,
non-mutating, disposable, and digest-only in committed evidence. Formal verification
must reproduce both artifact digests and metrics. Task-specific fresh-agent correctness
remains part of ADR 0002's later release acceptance scenarios rather than being invented
from an unfinished expected-facts matrix during pre-public planning item.

## Consequences

Persistent instructions become smaller, lower-churn, and less exposed to irrelevant
repository-controlled command text. `init` remains repository-specific through the
bounded component index, while current and detailed facts move to an explicit local
query. The map command gives limited-context consumers a deterministic alternative to
loading complete scan JSON and preserves claim provenance through evidence closure.

The public surface grows by one command, a versioned document, public types, and named
entry points. Path ownership, section membership, boundary intersection, output
budgeting, omission accounting, canonical serialization, and schema validation create
meaningful implementation and compatibility obligations. Repeated map calls rescan the
repository because public 1.0 deliberately avoids cache freshness and ownership policy.

The micro-index can still become stale after a human edit prevents regeneration. The
document explicitly identifies fresh map output as authoritative, and the accepted
provenance and future `doctor` workflow continue to expose drift without overwriting
human content. Users may explicitly configure large or unlimited indexes, accepting
their agent-context and interoperability costs.

The pre-public planning item review and pre-public approval record remain blocked until the corrective feature is implemented
and the corpus evidence is regenerated. Existing lifecycle verification remains useful
and should be retained; the failed scan-fact ranking and capping strategy should not be
promoted as accepted usefulness evidence.

## Alternatives considered

### Keep the scan-fact-heavy AGENTS.md with better ranking

Further heuristics could reorder or deduplicate facts, but component count and CI size
still scale independently of persistent instruction value. The failed corpus review
shows that a larger ranked ledger can remain both incomplete and unhelpful.

### Generate a router with no repository-specific index

This is the smallest and least stale artifact, but it makes `init` nearly generic and
does not itself provide the component-aware bootstrap outcome accepted by ADR 0002.

### Require consumers to filter complete scan JSON

This avoids a new public interface but duplicates path ownership, grouping, evidence
closure, and omission logic in every agent. It also provides no consistent context
budget for limited models.

### Return a filtered ScanResult

Reusing the scan schema would make an intentionally omitted record set appear to be a
complete normalized scan. Projection metadata cannot fully repair that semantic
ambiguity for existing readers, so the filtered document receives its own type and
schema.

### Add scope options that change `scan --format json`

This keeps three top-level commands but makes one format name return documents with
different completeness semantics. A separate `map` command gives the task-focused
workflow a clear name and preserves existing scan behavior.

### Cache or query saved scan documents

Caching reduces repeated inspection cost, but introduces freshness, artifact ownership,
cleanup, compatibility, and trust decisions that are not required for the initial local
projection workflow. Public 1.0 performs a fresh scan instead.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
