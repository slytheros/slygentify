# ADR 0005: Repository inspection, configuration, and provenance

## Status

Accepted

## Context

ADR 0003 established a fail-closed repository safety contract before the traversal,
configuration, component, and provenance models were researched. It made repository
configuration untrusted, permitted it to lower resource limits only, and required a
trusted explicit invocation to raise them. ADR 0002 and ADR 0004 also require identical
structured output for identical repository content, configuration, and producer version.

pre-public planning item in pre-public approval record examined traversal mechanics, ignore precedence, nested
repositories, component boundaries, workspace declarations, encoding, sensitive paths,
configuration, and provenance. The research confirmed the non-following containment and
effect boundaries in ADR 0003, but found two material tensions with the intended value for
very large repositories:

- repository owners need to commit higher or unlimited traversal budgets without a
  confirmation step on every scan; and
- a useful partial result produced at a wall-clock or host-resource boundary cannot also
  be byte-identical across hosts with different performance.

The maintainer selected automatically effective repository-configured resource limits,
with a warning rather than confirmation, and useful environmental partial results with a
narrower determinism promise. The same research recommended one component per root path
for 1.0, checked-out Gitignore handling, optimistic mutable-worktree validation, a
scanner-accounted memory budget, a strict root configuration, and deterministic committed
provenance state.

These changes materially alter ADR 0003, so the accepted record was not edited in
place. This ADR supersedes ADR 0003, carries forward its non-conflicting
safety, execution, and CI controls, and supplies the pre-public planning item configuration/provenance
decision requested in pre-public approval record. It also narrows the absolute determinism clauses in
ADRs 0002 and 0004 only for explicitly reported environmental limit exhaustion. It does
not itself implement scanning, add a dependency, or publish a schema artifact.

## Decision

Adopt the **bounded-by-default, repository-scalable inspection contract** described below.

### Effect boundary

Core inspection and default `doctor` behavior remain local, read-only, network-free, and
non-executing. Content reads, repository writes, command execution, and network access are
separate effects. Authorization for one effect never authorizes another.

Repository configuration is untrusted input. It may describe repository facts, component
boundaries, inspection scope, and resource budgets. It cannot authorize sensitive-content
reads, command execution, network access, repository writes, symlink following,
nested-repository recursion, mount traversal, or weaker execution isolation.

### Filesystem traversal

Resolve the user-selected repository root once, show the resulting root to the user, and
hold or revalidate an equivalent stable filesystem identity during inspection. Traverse
only relative descendants of that identity. Every candidate entry must be proven to
remain within the selected root before content is read.

Traversal is deterministic, metadata-first, and breadth-first. Directory entries are
considered in repository-relative POSIX lexical order. Shallow component-boundary
evidence is therefore considered before deeper content when a deterministic work budget
is exhausted. Filesystem enumeration order, set iteration, and concurrent completion
order never determine model ordering.

Use `lstat`-equivalent metadata inspection. Descendant symbolic links and Windows reparse
points are reported but never followed. Do not cross descendant mount or volume
boundaries. Read only regular files. Devices, FIFOs, sockets, unreadable entries,
unsupported names, entries whose identity changes before or during a read, and paths
whose containment cannot be proven are skipped with concise evidence.

Use handle-relative or equivalent platform operations where available. Revalidate the
opened handle against the inspected identity and revalidate identity and size after the
read. If a race is detected, discard the bytes and report the path as skipped. A platform
or filesystem that cannot enforce the required boundary fails closed for the affected
scope rather than falling back silently to an ordinary path open.

Do not memory-map inspected files. Read a relevant file at most once per scan, count its
raw bytes once, and share its bounded bytes among applicable parsers. If metadata or a
bounded read shows that a file exceeds the effective per-file limit, do not parse a
prefix as a complete file. Archives remain opaque and are not expanded during default
traversal.

Strictly decode formats in their mandated encoding. TOML and interoperable JSON are
UTF-8. Do not guess an encoding or replace invalid code points. Format-specific detectors
may implement a separately specified encoding rule, such as Python's source-encoding
declaration, when that format is later approved. An unsupported encoding produces a
skip or unknown result rather than a lossy claim.

### Nested repositories

A descendant `.git` file or directory establishes an opaque nested-repository boundary.
Report the boundary as safe metadata, do not read the `.git` entry, and do not descend
into that working tree. Scan it only through a separate invocation that selects it as the
root. A `.git` file at the selected root remains valid repository/worktree evidence but
does not need to be read.

The scanner may parse safe `path` declarations from a root `.gitmodules` file to identify
uninitialized submodule boundaries. It does not use or reproduce submodule URLs. A
declared submodule path remains subject to normal containment, entry-kind, and sensitive
content rules.

### Resource budgets

The zero-configuration inspection budgets are:

| Resource | Default | Counting rule |
| --- | ---: | --- |
| Maximum depth | 64 | The selected root is depth 0. |
| Entries examined | 100,000 | Count every directory entry considered, including skipped entries. |
| Bytes per file | 4 MiB | Count raw bytes made available for inspection or parsing. |
| Aggregate bytes read | 256 MiB | Sum raw bytes read across the operation. |
| Elapsed time | 60 seconds | Measure with a monotonic clock. |
| Concurrent open files | 64 | Count repository file handles held concurrently. |
| Scanner-accounted memory | 128 MiB | Count scanner-owned queued metadata, retained buffers, parser inputs, and accumulated normalized model data. |

Scanner-accounted memory is a deterministic logical budget, not a claim that total
process RSS is contained. Its ledger includes retained UTF-8 relative-path and pattern
bytes, retained file-buffer bytes, parser input retained in addition to those buffers,
and the deterministic serialized size of accumulated normalized model records. Shared
storage is counted once. Implementations must publish and test the exact ledger rules
before the field becomes a 1.0 compatibility surface. Interpreter, validation-engine,
regular-expression, allocator, and native-library overhead remain residual host-memory
risk.

Each configured limit is either a positive host-representable integer in its documented
unit or the exact string `"unlimited"`. Zero and negative integers are invalid.
`slygentify.toml` may lower, raise, or disable any resource budget. Valid configured
values apply automatically without a prompt or confirmation. Invocation arguments have
higher precedence and may also lower, raise, or disable limits. There is no
product-defined ceiling; operating-system and host limits still apply.

When repository configuration raises a default or selects `"unlimited"`, emit one
aggregated human-readable warning to standard error and a corresponding structured
diagnostic. The warning is informational and never blocks traversal. Record the default,
requested, effective, and source values in provenance.

The root `slygentify.toml` has a fixed 1 MiB raw-byte bootstrap limit because it must be
parsed before configured limits are available. This bootstrap bound is not configurable
by the file it protects. Exceeding it fails configuration loading before repository
traversal and changes nothing. Once configuration is valid, its effective per-file and
memory budgets govern later state and repository parsing.

Reaching a deterministic work limit stops the affected work safely and returns a
deterministic partial result naming the limit, effective value, consumed amount, and
known skipped scope. Reaching elapsed time, host OOM, operating-system resource limits,
or another environmental boundary also returns a partial result when the process remains
able to do so. Its reason and effective-limit semantics are stable, but its completed
evidence prefix and measured consumption need not be byte-identical across hosts.

The identical-JSON requirement in ADRs 0002 and 0004 therefore applies when inspection
completes or reaches a deterministic Slygentify work budget. It does not promise identical
bytes after elapsed-time or host-resource exhaustion. Every environmental partial result
must remain honest about its omitted scope and must never claim exhaustive inspection.

### Ignore and sensitive-content precedence

Path scope uses this precedence, from highest to lowest:

1. hard containment, entry-kind, nested-repository, VCS-internal, and sensitive-content
   rules;
2. exact per-invocation sensitive-content authorization where this ADR permits it;
3. explicit invocation path rules;
4. root `slygentify.toml` path rules;
5. hierarchical `.gitignore` rules present in the selected checkout; and
6. narrow built-in dependency and cache exclusions.

Within one ordered rule source, the last matching Gitignore-compatible pattern decides
the result. A leading `!` re-includes a path excluded by a lower or earlier scope rule but
never overrides a hard safety boundary. Nested checked-out `.gitignore` files apply
relative to their directory and override ancestor files according to Git's documented
precedence.

Do not read `.git/info/exclude`, a global Git ignore file, `.ignore`, or `.rgignore`.
Slygentify applies `.gitignore` files present in the selected checkout to its inspection
scope. Because the Git index is outside the inspection boundary, Slygentify does not
claim that those files are committed or reproduce Git's tracked-file semantics. Record
the rule source and each topmost pruned scope. An intentional effective-scope exclusion
does not alone make a result partial; it makes the result partial when the omitted scope
could affect a requested conclusion.

Built-in convenience exclusions remain limited to paths with strong dependency/cache
semantics. Do not exclude ambiguous names such as `vendor`, `build`, `dist`, `generated`,
`examples`, or `third_party` solely by name.

Default traversal may report safe metadata but does not read content from:

- VCS internals, including `.git` entries and nested repository metadata;
- environment files such as `.env` and `.env.*`;
- private-key, certificate, and keystore containers such as `*.key`, `*.pem`, `*.p12`,
  `*.pfx`, `*.jks`, `*.keystore`, and `*.kdbx`;
- authentication files such as `.netrc`, `.npmrc`, `.pypirc`, Git credential files,
  container credential configuration, Poetry `auth.toml`, and Yarn authentication
  configuration;
- common SSH, cloud, Kubernetes, and package-manager credential stores; or
- Terraform state, recognizable local plan files, state backups, and `.terraform`
  working data.

This registry is defense in depth, not a complete secret detector. Normal diagnostics,
logs, generated artifacts, structured output, and provenance never reproduce raw
inspected content. Errors identify only the operation and safe relative target.

A trusted user may authorize content access to one exact in-root path for one invocation.
The interface shows the path and effect before access. Patterns, directory-wide consent,
repository configuration, and retained consent do not satisfy this requirement. The
override permits analysis only; it does not permit raw content disclosure or network
transmission.

### Component and workspace boundaries

A component is an evidence-backed repository-relative directory from which a distinct
development or validation workflow can operate. Directory conventions alone never
establish a component.

Evidence is evaluated in this order of strength:

1. an explicit maintainer component declaration establishes a verified declaration but
   not independent operational verification;
2. a successfully parsed workspace membership declaration establishes strong evidence
   for its workspace root and members;
3. a successfully parsed project or package manifest establishes a component root;
4. a recognized legacy or unsupported manifest may establish a generic or unknown
   candidate; and
5. lockfiles, test configuration, CI files, source directories, Dockerfiles, Makefiles,
   and README files corroborate but never establish a boundary alone.

Emit at most one component per repository-relative root path in 1.0. Workspace roots and
independently evidenced members are both components. Co-located Python and
JavaScript/TypeScript evidence describes one mixed component. This is detector policy,
not a JSON Schema uniqueness constraint. The schema permits a later compatible or
explicitly versioned policy to represent additional same-path components.

Component identifiers are document-scoped and deterministic. The 1.0 primary component
at a path retains an identifier derived from a domain separator and the normalized path.
If a later policy adds another component at the same path, it adds a stable discriminator
without changing the existing primary identifier.

A `pyproject.toml` containing only tool configuration is insufficient Python component
evidence. Accepted `[project]`, `[build-system]`, manager-specific project, or workspace
data supplies stronger evidence. A valid `package.json` establishes a
JavaScript/TypeScript package boundary even if a private unpublished package omits name
or version.

Workspace patterns resolve relative to the declaring root, remain within the selected
repository, never follow links, and require the expected member manifest. Workspace
exclusions override matching membership declarations from the same workspace. Missing,
invalid, overlapping, cyclic, out-of-root, or contradictory workspace evidence remains
visible instead of being silently resolved.

Recognized unsupported manifests such as Cargo workspaces, `go.work`, or Maven modules
may establish generic components without claiming first-class ecosystem inspection.
Templates are never interpolated or executed. If placeholders prevent safe parsing, keep
the manifest as evidence and classify the boundary as unknown unless configuration
declares it. If no sufficient evidence exists, return zero components and an unknown
finding instead of fabricating a generic root component.

Exact Python and JavaScript/TypeScript manifests, managers, workspace dialects,
frameworks, and tooling remain owned by pre-public planning item, pre-public planning item, and pre-public planning item.

### `slygentify.toml` contract

There is exactly one team configuration at the selected repository root named
`slygentify.toml`. Slygentify does not search parents or descendants for another
configuration and does not add a separate Slygentify ignore file.

The initial schema is:

```toml
schema_version = 1

[scan]
ignore = [
  "generated/**",
  "!generated/schema.json",
]

[[scan.components]]
path = "services/api"
ecosystem = "python"
kind = "application"

[scan.limits]
max_depth = 256
max_entries = 1000000
max_file_bytes = 268435456
max_total_bytes = 17179869184
max_elapsed_seconds = 1800
max_open_files = 256
max_memory_bytes = 2147483648
```

The schema fields are:

- `schema_version`: required integer, exactly `1` for this schema;
- `scan.ignore`: optional ordered array of Gitignore-compatible UTF-8 strings;
- `scan.components`: optional array of component declarations;
- `scan.components[].path`: required safe repository-relative POSIX directory path;
- `scan.components[].ecosystem`: optional non-empty extensible identifier;
- `scan.components[].kind`: optional non-empty extensible identifier; and
- each `scan.limits` field: optional positive integer in the unit implied by its name or
  the exact string `"unlimited"`.

Omitted limit fields use the zero-configuration default. Omitted `scan`, `ignore`,
`components`, or `limits` values mean no override and serialize canonically as absent,
not as `null`.

Configuration is strict UTF-8 TOML and rejects duplicate or unknown keys, unknown schema
versions, invalid scalar types, empty identifiers, and invalid patterns. It does not
support imports, includes, URLs, commands, environment interpolation, plugin loading, or
host-absolute values.

Configured paths preserve repository spelling and serialize with `/`. Reject absolute
paths, drive or UNC prefixes, NULs, `.` segments other than the whole root value, `..`
segments, paths that escape after platform validation, links, non-directories, and nested
repository targets. The root component path is `.`.

A configured component declaration creates or modifies the effective component at the
same path. Its source kind identifies it as a maintainer declaration. Slygentify verifies
the presence and exact configured statement without claiming that the ecosystem, kind,
or operational behavior was independently verified. Conflicting detected evidence is
retained and reported rather than erased by precedence.

Invocation arguments override corresponding configuration fields. Configuration
overrides checked-out Gitignore and built-in convenience rules but not hard safety rules.
Environment variables do not participate in configuration precedence.

Configuration major migrations are explicit. A newer program may read an older supported
major through a documented migration adapter, but `scan` never rewrites configuration.
An unsupported or invalid configuration fails before traversal with an actionable error,
states that no repository files changed, and identifies the safe relative configuration
path without reproducing values.

### `.slygentify/state.json` contract

`.slygentify/state.json` is committed, deterministic, regenerable provenance. It is a
derived artifact and never supplies current repository facts. `scan` remains read-only
and never creates or updates it. A later explicitly mutating workflow owns state writes
and must provide a review path.

The initial logical JSON shape is:

```json
{
  "schema_version": 1,
  "producer_version": "0.1.0",
  "configuration": {
    "location": "slygentify.toml",
    "sha256": "..."
  },
  "effective_limits": [
    {
      "name": "max_elapsed_seconds",
      "default": 60,
      "requested": "unlimited",
      "effective": "unlimited",
      "source": "configuration"
    }
  ],
  "inputs": [
    {
      "id": "...",
      "source_kind": "manifest",
      "location": "services/api/pyproject.toml",
      "locator": "project.name",
      "sha256": "...",
      "value_sha256": "...",
      "rule_id": "...",
      "rule_version": 1
    }
  ],
  "derivations": [
    {
      "subject_id": "...",
      "claim_code": "...",
      "classification": "verified",
      "evidence_ids": ["..."]
    }
  ],
  "artifacts": [
    {
      "location": "AGENTS.md",
      "sha256": "...",
      "evidence_ids": ["..."]
    }
  ],
  "completion": "complete",
  "skipped_scopes": []
}
```

The example illustrates field meaning and canonical types; the checked-in JSON Schema
created by the dependent implementation is normative.

State fields are:

- `schema_version`: required integer state-schema major, initially `1`;
- `producer_version`: required Slygentify package version;
- `configuration`: optional safe location and SHA-256 digest of the exact raw
  configuration bytes;
- `effective_limits`: required ordered records of default, requested, effective, and
  source values;
- `inputs`: ordered evidence-source records with deterministic identity, safe location,
  optional semantic locator, source digest, optional canonical-value digest, and
  detector/rule identity;
- `derivations`: ordered subject/claim/classification relationships to evidence;
- `artifacts`: ordered generated-artifact locations, digests, and evidence relationships;
- `completion`: required closed `complete` or `partial` value; and
- `skipped_scopes`: ordered explicit omitted-scope records compatible with the scan model.

Use SHA-256 over exact raw source-file bytes. A `value_sha256`, when present, hashes the
canonical UTF-8 JSON representation of the parsed value and permits precise drift checks
without persisting the value. Semantic locators use format-appropriate stable forms such
as TOML dotted keys or RFC 6901 JSON Pointers.

Identifiers derive from domain-separated normalized identity inputs, never from content
digests, timestamps, absolute paths, random UUIDs, traversal order, or machine state.
Content changes update digests without unnecessarily replacing component or evidence
identity.

State never contains timestamps, source contents, arbitrary raw manifest values,
credentials, host-absolute paths, environment-derived data, or network data. It uses the
same UTF-8, LF, final-newline, finite-number, fixed-field-order, and stable-array-order
rules as ADR 0004.

Same-major state readers ignore additive unknown object properties. Producers forbid
undeclared fields and validate immediately before writing. Unsupported-major, malformed,
oversized, stale, or digest-mismatched state is ignored as authority and produces an
actionable diagnostic. It never blocks a fresh read-only scan and never silently replaces
current evidence.

State migrations are explicit and non-destructive. Read-only operations may interpret a
supported older state version in memory but do not rewrite it. An explicit mutating
operation writes only the canonical current schema after presenting the proposed change.

### Authorized command execution

Public 1.0 may provide a separate explicit `doctor` verification capability. Its final
CLI, JSON, Python, diagnostic, and exit behavior remains owned by pre-public planning item and may not weaken
these controls.

Each execution requires selection of the exact command for that invocation. Authorization
is bound to the reviewed argument vector, command evidence, component working directory,
and repository snapshot. A changed bound input stops execution and requires new
authorization. Invoke the argument vector directly without adding a shell.

Run the command in a disposable writable copy containing only approved in-root inputs.
Excluded sensitive content is absent unless each required exact path received content
authorization. The real repository, other host filesystems, host credentials, inherited
sensitive environment variables, and interactive standard input are unavailable.
Results and artifacts are not copied back automatically.

The execution sandbox defaults remain five elapsed minutes, two CPUs, 2 GiB memory, 64
processes, 2 GiB writable storage, and 1 MiB each of captured standard output and error.
Trusted execution overrides remain explicit and bound to the invocation. Terminate the
complete process tree on timeout, bound and redact output, mark truncation, and remove the
disposable workspace after result collection.

Network access is denied by default and requires separate authorization for the same
command. It never grants credentials or relaxes filesystem, process, or resource
isolation. If the backend cannot enforce filesystem, credential, process, resource,
cleanup, and network controls, execution is unavailable and fails actionably.

### Untrusted contribution CI

Gitea Actions, GitHub Actions, and GitLab CI/CD retain one invariant: untrusted
contributions do not receive secrets, protected credentials, write-capable tokens,
protected runners, privileged host/container access, or reusable trusted workspaces.

Before a platform processes an untrusted contribution, maintainers record dated,
inspectable evidence for trigger/token/secret semantics; disposable runner lifecycle,
mounts, and cleanup; absence of privileged mode and host sockets; cache and artifact
separation by trust; restricted documented egress; and protected workflow-change review.

Untrusted and privileged release jobs remain separate. Trusted jobs do not consume
untrusted caches, workspaces, or artifacts without independently verified provenance.
Repository dependency metadata cannot silently broaden egress. Public or fork-based CI
remains disabled or human-gated until platform evidence demonstrates the invariant.

### Verification contract

Dependent implementations must demonstrate:

- out-of-root and cyclic links, reparse points, mounts, special entries, changed
  identities, nested repositories, invalid names, and unreadable paths do not escape,
  block, or make results appear complete;
- deterministic work budgets produce deterministic partial results;
- elapsed-time and host-resource exhaustion produce explicit honest partial results
  without claiming byte-identical evidence prefixes;
- configuration can lower, raise, and disable each resource budget automatically and
  relaxed values produce the required warning, diagnostic, and provenance;
- scanner-accounted memory follows its published deterministic ledger and fails with a
  structured partial result when possible;
- checked-out Gitignore precedence, negation, nested rules, configured re-inclusion, and
  skipped-scope reporting match the documented effective-scope policy;
- sensitive names and representative values do not appear in ordinary output, logs,
  state, generated artifacts, or uploads;
- workspace declarations cannot escape the root, follow links, or silently resolve
  contradictions;
- component identities and all serialized ordering are deterministic;
- invalid configuration fails before traversal without mutation, while invalid state is
  never trusted and does not block a fresh scan;
- default inspection launches no process and makes no network request;
- authorization is invalidated by a changed command or repository snapshot;
- sandbox fixtures cannot reach host files, credentials, persistent workspaces, or the
  network unless network was separately authorized;
- execution time, process, memory, storage, and output bounds fail closed; and
- each enabled CI platform passes credential, runner, cache, artifact, workflow-change,
  and egress abuse scenarios.

Observable behavior receives Doorstop requirements, test specifications, and source links
when implemented. This proposed decision does not claim that those capabilities exist.

## Consequences

The common path remains bounded, local, read-only, network-free, and non-executing. The
configuration and state formats are portable, reviewable team artifacts containing no
host paths, timestamps, raw evidence values, or credentials. One root configuration and
one derived state artifact keep the initial surface small.

Large repositories can commit appropriate limits once and use them without recurring
confirmation. Warnings and provenance make relaxed limits visible. This improves utility
for monster repositories but deliberately allows untrusted repository text to request
excessive resource consumption. A malicious checkout can disable elapsed or memory
budgets, hang inspection, or exhaust the host. The warning is not a technical mitigation.

Scanner-accounted memory provides deterministic graceful handling for the allocations it
tracks but is not total process containment. Host OOM, allocator/native overhead, parser
defects, and unsupported platform facilities remain residual risks. A later isolated
worker may add an OS-enforced ceiling without changing the logical public budget.

Useful timeout prefixes improve diagnosis on large repositories but narrow the absolute
determinism promise. Automation that requires byte-identical output must use sufficient
elapsed and host resources to complete or must rely on deterministic work budgets.

Checked-out Gitignore support aligns with repository expectations and avoids common
noise, but Slygentify cannot establish whether an ignore file is committed and can omit
a tracked file that still matches an ignore rule because it does not read the Git index.
Configured re-inclusion and explicit skipped-scope provenance contain but do not eliminate
this mismatch.

One component per root path gives stable 1.0 behavior for mixed repositories. Some rare
co-located independent units cannot be represented separately until the detector policy
is relaxed. Avoiding a schema uniqueness constraint keeps that evolution possible.

Strict configuration catches mistakes early but makes newer configuration incompatible
with an older reader until the tool is upgraded. Open same-major state readers support
additive producer evolution, while closed producer validation prevents Slygentify from
emitting undeclared fields.

Optimistic handle validation is not an atomic repository snapshot. Concurrent additions
or changes may evade portable detection. Network filesystems, bind mounts, unsupported
reparse behavior, credentials with unrecognized names, explicit sensitive reads, and
environmental failures remain visible residual risks rather than eliminated threats.

## Alternatives considered

### Keep ADR 0003's lower-only repository limits

Requiring a trusted invocation for every increase better protects users from resource
requests controlled by an untrusted checkout. It was rejected because it creates
recurring friction for the large repositories where Slygentify is expected to provide
particular value and prevents a team from committing one reproducible operating profile.

### Require confirmation for relaxed configuration

Per-run or first-run confirmation would preserve committed requests while adding a human
trust boundary. It was rejected by maintainer direction. The selected warning records the
risk without blocking interactive or automated use.

### Product hard ceilings

Hard ceilings bound malicious configuration but eventually make legitimate repositories
unsupported. They were rejected. Defaults remain conservative; valid configuration and
invocation overrides have no product-defined ceiling.

### RSS polling or OS-kill memory limits

RSS polling covers more allocations but is approximate and nondeterministic. An OS process
limit provides stronger containment but is platform-specific and may kill the process
before it emits a structured result. Both were rejected as the 1.0 public memory budget in
favor of deterministic scanner accounting. OS isolation remains a later defense-in-depth
option.

### Discard all findings after timeout

Discarding accumulated work could make timed-out results more uniform. It was rejected
because it removes useful bounded evidence from the very large repositories motivating
configurable limits.

### Absolute determinism after environmental exhaustion

This is not achievable while returning the useful prefix completed before an
environment-dependent deadline. It was replaced with strict determinism for completed
and deterministic-work-bound results plus stable partial-result semantics for
environmental boundaries.

### Follow in-root links or descend into nested repositories

Both improve apparent coverage but enlarge race, containment, cycle, attribution, and
secret risk. Separate root selection is clearer and safer.

### Ignore checked-out Gitignore rules or read the Git index

Ignoring Gitignore wastes budgets on dependencies and generated artifacts. Reading the
index could distinguish tracked ignored files but violates the VCS-internal boundary and
adds private repository-state complexity. The selected scope policy records its known
semantic limitation.

### Directory-convention or manifest-per-component identity

Directory conventions fabricate boundaries. One component per manifest duplicates
co-located mixed projects and makes identity churn when manifests are added. One
evidence-backed root component is the smaller 1.0 contract.

### A separate `.slygentifyignore`

Another ignore file could isolate Slygentify scope from Git semantics. It was rejected
because ordered rules in the existing root configuration provide the needed override
without another discovery and precedence surface.

### Environment interpolation, imports, or hierarchical configuration

These features improve reuse but make configuration depend on host-private or external
state and enlarge containment and secret boundaries. They are unnecessary for the
initial committed team contract.

### Use provenance state as a cache or authority

This could accelerate scans and preserve corrections implicitly. It was rejected because
stale or attacker-modified derived state could replace current evidence. Maintainer
declarations live in configuration; state remains reproducible provenance.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
