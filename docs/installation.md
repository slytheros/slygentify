# Installation

## Current availability

Slygentify `1.0.0rc1` is not yet published on production PyPI. Begin from an existing reviewed source checkout;
the commands in the later [post-publication section](#after-a-genuine-pypi-publication)
are examples of approved future installation paths, not currently available downloads.

| Area | Current status |
| --- | --- |
| Maturity | Public 1.0 release candidate `1.0.0rc1` |
| Distribution | Reviewed source checkout only; no PyPI release |
| Python | CPython 3.11 through 3.14 |
| Platforms | Ubuntu 24.04 x64, Windows 2025 x64, and macOS 15 arm64 |
| Repository | A selected target must be inside a local Git repository |
| Git executable | Optional; unavailable tracked-path discovery can make a result partial |
| Maintenance | Current development version, best effort, with no service-level agreement |

See the repository
[support policy](https://github.com/slytheros/slygentify/blob/develop/SUPPORT.md) for
maintenance and reporting terms.

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

## After a genuine PyPI publication

!!! warning "Future installation only — do not run these commands yet"

    Slygentify has no PyPI release. Do not use the following commands until a release
    exists and its provenance has been verified on PyPI. A placeholder package or name
    reservation is not a Slygentify release.

    ```console
    git clone https://github.com/slytheros/slygentify.git
    python -m pip install slygentify
    pipx install slygentify
    uv tool install slygentify
    uvx slygentify --help
    ```

Release documentation will replace this warning with the exact verified version and
artifact hashes after the supported installation matrix passes.
