# Installation

## Current availability

Slygentify `1.0.0` is the first stable release on production PyPI. Prefer an exact
version for reproducible installation, and verify the published provenance and SHA-256
hashes against the corresponding GitHub release before use in a sensitive environment.

| Area | Current status |
| --- | --- |
| Maturity | Stable public release `1.0.0` |
| Distribution | PyPI wheel and source distribution with Trusted Publisher provenance |
| Python | CPython 3.11 through 3.14 |
| Platforms | Ubuntu 24.04 x64, Windows 2025 x64, and macOS 15 arm64 |
| Repository | A selected target must be inside a local Git repository |
| Git executable | Optional; unavailable tracked-path discovery can make a result partial |
| Maintenance | Current development version, best effort, with no service-level agreement |

See the repository
[support policy](https://github.com/slytheros/slygentify/blob/develop/SUPPORT.md) for
maintenance and reporting terms.

## Install from PyPI

For an isolated command installation:

```console
pipx install slygentify==1.0.0
```

Or with uv:

```console
uv tool install slygentify==1.0.0
```

For a virtual environment managed with pip:

```console
python -m pip install slygentify==1.0.0
```

Package installation executes the build and installation tooling and resolves declared
dependencies. Use a trusted package index and review the selected artifacts and
provenance for high-trust environments.

## Development checkout

From an existing reviewed checkout, install the locked environment with a current
[uv](https://docs.astral.sh/uv/) installation:

```console
cd slygentify
uv sync --locked
uv run slygentify --help
```

Installing the locked environment includes development, test, and documentation tools.

## Isolated command installation

To install a local checkout as an isolated command without its development dependencies:

```console
uv tool install .
slygentify --help
```

Or use a fresh virtual environment with pip:

```console
python -m venv .venv
python -m pip install .
slygentify --help
```

Installing a source checkout executes the package build backend and resolves declared
dependencies. Review the checkout and lock/build configuration first.

## Verify the installation

```console
slygentify --help
slygentify scan --help
slygentify init --help
slygentify map --help
slygentify doctor --help
```

Slygentify requires a local Git repository as its inspection boundary, but can inspect
when the Git executable is unavailable. In that case, its automatic fixed tracked-path
lookup may be unavailable and the result can be partial. Selecting
`--git-executable PATH` authorizes that exact file as trusted, unsandboxed code; it can
have arbitrary effects.

## One-shot execution

Run the exact stable version without retaining a tool installation:

```console
uvx --from slygentify==1.0.0 slygentify --help
```
