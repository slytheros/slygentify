# Slygentify

Slygentify is a local-first developer tool that helps people and coding agents
understand and operate unfamiliar software repositories safely. It builds a bounded,
evidence-backed operating map instead of guessing from conventions or executing
discovered project commands.

Slygentify is a public 1.0 release candidate (`1.0.0rc1`). The implemented commands are:

- `slygentify init` — plan or create concise, component-aware root guidance.
- `slygentify scan` — inspect a repository and return a human or versioned JSON report.
- `slygentify map` — emit fresh, task-scoped JSON context for a logical path.
- `slygentify doctor` — assess managed repository knowledge without executing it.

Sandboxed command verification, cloud services, and runtime CodeGraph or forge
integrations are not implemented.

## Quickstart

Slygentify is not yet published on PyPI. Install it from a reviewed source checkout as
described in the [installation guide](docs/installation.md). Every command accepts a
directory inside a local Git repository. The Git executable is optional: without it,
tracked-path discovery may be unavailable and an otherwise useful result can be
`partial`.

Choose the command that matches your goal:

| Goal | Command | Effects and result |
| --- | --- | --- |
| Understand an unfamiliar repository | `slygentify scan PATH` | Read-only human report or canonical JSON. |
| Get bounded context for one task | `slygentify map PATH --scope PATH` | Read-only canonical JSON projection. |
| Preview root agent guidance | `slygentify init PATH --dry-run` | Read-only exact proposed artifacts. |
| Check managed guidance against fresh evidence | `slygentify doctor PATH` | Read-only report; warnings can produce exit 1. |

For a safe first pass, inspect the repository, narrow the context, and review guidance
before deciding whether to create it:

```console
slygentify scan path/to/repository
slygentify map path/to/repository --scope src/example.py
slygentify init path/to/repository --dry-run
slygentify doctor path/to/repository
```

`doctor` can report unmanaged guidance before initialization; that is a finding to
review, not evidence that inspection failed. If the dry-run is appropriate and you want
Slygentify to write the two managed artifacts, apply it explicitly:

```console
slygentify init path/to/repository
```

Initialization writes `AGENTS.md` and `.slygentify/state.json` only after planning and
revalidation. Existing unmanaged or human-edited guidance is preserved by default;
ordinary `init` prints a paste-ready Slygentify section and exits 4 so a user can merge it
manually. `--replace` is an explicit destructive choice and does not create a backup or
merge.

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
some evidence. Diagnostics and skipped scopes explain the boundary. Findings preserve
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
