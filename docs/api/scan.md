# Scan Python API

Use the scan API for a fresh, bounded operating map of a local Git repository.

## Scan a repository

`scan_repository(path=".", *, git_executable=None)` resolves the nearest containing Git
repository and returns an immutable `ScanResult`. It does not import the target project,
execute discovered commands, contact the network, or write repository files.

An automatic fixed `git ls-files --cached --full-name -z` lookup may retain tracked
manifests that checked-out ignore rules would hide. When automatic Git is unavailable,
rejected, or fails, scan returns a `partial` result with
`inspection.git-tracked-paths-unavailable` and `git_tracking_unavailable` evidence when
it can otherwise inspect safely. A selected `git_executable` overrides `PATH`, must be
an existing regular executable, and is trusted unsandboxed code; invalid explicit input
raises `ScanError` before traversal. See [safety boundaries](../safety.md#fixed-git-lookup).

The explicit value accepts a string or path-like value, is expanded and resolved relative
to the caller's current directory, and has no configuration-file or environment-variable
alias. Identity is revalidated immediately before launch, though a cross-platform
path-replacement race remains. Without an override, an automatically resolved executable
whose canonical target is inside the repository is rejected.

`complete` means the supported inspection boundary finished; it does not prove every
repository fact is known. `partial` preserves inspected evidence while diagnostics and
skipped scopes identify omissions. Operational failures raise `ScanError`.

`ScanResult` contains normalized `Repository`, `Component`, `ComponentRelationship`,
`Evidence`, `Finding`, `Diagnostic`, and `SkippedScope` values. Direct constructors are
for trusted typed values only. Component identifiers derive from paths and relationship
identifiers derive from their kind and endpoints.

## Read and write scan JSON

`dump_scan_json(result)` returns deterministic UTF-8 `scan-v1` bytes. Use
`scan_json_schema()` for a fresh packaged Draft 2020-12 schema mapping. Use
`validate_scan(value)` or `load_scan_json(data)` for untrusted input; invalid values or
documents raise `ScanValidationError`, a `ScanError` subclass.

Readers reject duplicate keys, byte-order marks, invalid UTF-8, non-finite numbers, and
inputs that exceed documented size, depth, collection, or graph limits. Schema-major-1
readers accept additive unknown object fields; current producers emit only declared
canonical fields. See [JSON Schema reference](../schemas.md) and the
[scan task guide](../guides/scan.md).

The loader rejects documents larger than 128 MiB, nesting deeper than 32, UTF-8 strings
larger than 4 MiB, collections with more than 100,000 entries, and object graphs larger
than 5,000,000 nodes.

## Result interpretation

`ScanResult` contains normalized repository, component, evidence, finding, diagnostic,
skipped-scope, and relationship values. Components can expose more than one ecosystem
facet; `ecosystem` is `mixed` when multiple facets coexist. Component roles and directed
relationships remain evidence-backed. Exact ecosystem recognition belongs in the
[Python inspection](../python-inspection.md),
[JavaScript and TypeScript inspection](../javascript-inspection.md), and
[mixed repository composition](../mixed-repositories.md) references.

Schema-major-1 documents produced before mixed composition can omit `ecosystems` and
`relationships`; those produced before auxiliary-role classification can also omit
`role`. Current readers derive the legacy singular facet, an empty relationship
collection, and the default `unknown` role.
