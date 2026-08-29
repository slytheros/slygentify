# Slygentify

Slygentify helps people and coding agents answer practical questions before working in
an unfamiliar local Git repository:

- What applications, packages, and workspace members are present?
- Which runtimes, package managers, tools, frameworks, and CI commands are declared?
- Which component owns the file or path involved in a planned change?
- Which repository facts are missing, conflicting, unsupported, or unsafe to infer?
- Does managed coding-agent guidance still match the current repository?

It builds a bounded, evidence-backed operating map from static repository evidence
instead of guessing from conventions or executing discovered project commands.
First-class ecosystem inspection covers Python and JavaScript/TypeScript repositories,
including mixed repositories and workspaces.

Slygentify is a public 1.0 release candidate (`1.0.0rc1`). It is not yet published on
PyPI.

## What Slygentify helps with

| When you need to... | What Slygentify provides | Command and interface |
| --- | --- | --- |
| Explore or onboard to an unfamiliar repository | A component-organized view of identity, declared working tasks, architecture, automation, concerns, and inspection boundaries | `slygentify scan PATH` for a complete human-readable report, or add `--interactive` for a full-screen terminal explorer |
| Investigate a large or mixed repository interactively | A keyboard-accessible component tree with search, record and classification filters, evidence-first detail, raw record JSON, and a glossary | `slygentify scan PATH --interactive` |
| Give an agent or automation a complete repository result | Deterministic, versioned JSON containing the retained evidence, findings, diagnostics, and skipped scopes | `slygentify scan PATH --format json` |
| Prepare an agent for one planned change | Bounded JSON context for a logical path, including its owning component and selected orientation, workflow, architecture, automation, or boundary records | `slygentify map PATH --scope FILE` |
| Establish durable repository guidance for coding agents | A reviewable plan for concise root `AGENTS.md` guidance and deterministic provenance | `slygentify init PATH --dry-run`, followed by `slygentify init PATH` only after review |
| Detect stale or unsafe managed guidance | A fresh comparison of configuration, repository evidence, generated guidance, and managed provenance | `slygentify doctor PATH` |
| Investigate incomplete or inconsistent repository metadata | Explicit diagnostics for malformed, conflicting, unsupported, skipped, or resource-limited evidence instead of silent guesses | `slygentify scan PATH` or `slygentify doctor PATH` |

Human-readable interfaces are designed for exploration and review. Coding agents and
automation can consume canonical versioned JSON from `scan`, `map`, and `doctor`, or use
the corresponding Python APIs.

## What Slygentify understands

Slygentify statically inspects supported manifests, workspace declarations, lock and
configuration files, and CI workflows. Depending on the ecosystem and available
evidence, it can surface:

- component and workspace boundaries and their relationships;
- declared runtimes, package managers, direct dependencies, and entry points;
- configured development tools and directly declared frameworks;
- setup, run, test, lint, format, and build commands declared in project metadata or CI;
- conflicting declarations, malformed supported files, skipped scopes, and important
  unknowns.

Every conclusion remains classified as verified, inferred, recommended, or unknown, and
its supporting evidence remains inspectable. A declared command is evidence that the
command appears in repository configuration; it is not proof that the command works or
that the team prefers it.

Slygentify does not analyze application business logic, build a symbol or call graph,
decide which source files an issue requires changing, resolve dependencies, evaluate
dynamic configuration, or execute and verify discovered commands. Sandboxed command
verification, cloud services, and runtime CodeGraph or forge integrations are not
implemented.

## Quickstart

Install Slygentify from a reviewed source checkout as described in the
[installation guide](docs/installation.md). Every command accepts a directory inside a
local Git repository.

Start with the complete human-readable scan report:

```console
slygentify scan path/to/repository
```

For a searchable full-screen view intended for people working at a terminal:

```console
slygentify scan path/to/repository --interactive
```

For a coding agent, script, or other automation, select canonical JSON or narrow the
result to the logical path involved in a task:

```console
slygentify scan path/to/repository --format json > scan.json
slygentify map path/to/repository --scope src/example.py
```

When you want durable coding-agent guidance, review the exact proposed artifacts before
allowing a write. After initialization, use doctor following structural, tooling, or
workflow changes:

```console
slygentify init path/to/repository --dry-run
slygentify init path/to/repository
slygentify doctor path/to/repository
```

`init` is the only implemented command above that writes repository files. It writes
`AGENTS.md` and `.slygentify/state.json` only after planning and revalidation. Existing
unmanaged or human-edited guidance is preserved by default; ordinary `init` prints a
paste-ready section and exits 4 so it can be merged manually. Use `--adopt --dry-run` to
preview management of one visible Slygentify section while preserving surrounding human
guidance. `--replace` is an explicit destructive choice and does not create a backup or
merge. See the [initialization guide](docs/guides/init.md) for ownership, recovery, and
state-schema behavior.

The Git executable is optional. Without it, tracked-path discovery may be unavailable
and an otherwise useful result can be `partial`; diagnostics explain what was omitted.

See the [first-repository tutorial](docs/tutorials/first-repository.md) for a runnable
walkthrough, the [troubleshooting guide](docs/guides/troubleshooting.md) for safe
recovery, and the [CLI guide](docs/cli.md) for complete command behavior. The optional
root `slygentify.toml` is described in the
[configuration reference](docs/configuration-and-provenance.md).

## Effects and safety

Core inspection is local, read-only, bounded, contained within the selected Git root,
and network-free. Slygentify does not import the target project, execute commands it
discovers, read sensitive paths by default, follow descendant symbolic links, or upload
repository content.

Automatic scans, including the fresh scan used by doctor, may invoke one fixed, bounded
Git `ls-files` lookup to retain tracked manifests that checked-out ignore rules hide.
`--git-executable PATH` is a separate, explicit authorization for that exact executable.
It is trusted unsandboxed code and may have arbitrary file, process, credential, or
network effects; inspect nonstandard or repository-contained executables before
selecting one. Doctor does not execute discovered validation commands.

A `complete` scan completed within the supported inspection boundary; it is not proof
that every repository fact is known. A `partial` scan succeeded but omitted or limited
some evidence. Diagnostics and skipped scopes explain the boundary. Diagnostic
dispositions distinguish problems, trustworthy limitations, and notices without changing
claim classification, completion, or exit behavior. Findings preserve
whether claims are verified, inferred, recommended, or unknown.

The detailed contracts are in [interaction design](docs/interaction-design.md),
[inspection accounting](docs/inspection-accounting.md), and the accepted
[safety ADRs](docs/adr/README.md).

## Documentation

Start with the [first-repository tutorial](docs/tutorials/first-repository.md), or use the
[documentation index](docs/index.md) to find a focused guide or reference:

- [installation](docs/installation.md)
- [first repository tutorial](docs/tutorials/first-repository.md)
- [concepts and claim classes](docs/concepts.md)
- [safety boundaries](docs/safety.md)
- [task guides](docs/index.md#task-guides)
- [troubleshooting](docs/guides/troubleshooting.md)
- [CLI commands](docs/cli.md)
- [`slygentify.toml` and provenance](docs/configuration-and-provenance.md)
- [Python and JSON APIs](docs/api.md)
- [mixed-repository composition](docs/mixed-repositories.md)
- [Python inspection](docs/python-inspection.md)
- [JavaScript and TypeScript inspection](docs/javascript-inspection.md)
- [acceptance measurement](docs/acceptance.md)
- [maintainer release process](docs/releasing.md)
- [architecture decisions](docs/adr/README.md)
- [support](SUPPORT.md), [security](SECURITY.md), and [migration guidance](docs/migration.md)

## Development

Slygentify supports Python 3.11 through 3.14 and uses
[uv](https://docs.astral.sh/uv/) with a committed lockfile.

```console
uv sync --locked --all-groups
uv run pytest
uv run --locked mkdocs build --strict
uv run pre-commit run --all-files
uv build --no-sources
```

The pytest suite enforces 100% statement and branch coverage. The complete locked command
set, Doorstop traceability workflow, branch policy, effects review, and Definition of
Ready/Done are in [CONTRIBUTING.md](CONTRIBUTING.md). Repository automation runs quality,
documentation, supported-Python test, packaging, and dependency-vulnerability workflows;
the dependency audit requires network access.

Contributions use short-lived branches from `develop` and reviewed pull requests back to
`develop`. `main` receives reviewed release promotions. Coding agents may prepare work
and evidence but do not approve or merge pull requests, publish releases, or close human
gates.

Slygentify is licensed under the [Apache License 2.0](LICENSE).
