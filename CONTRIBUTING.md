# Contributing to Slygentify

Slygentify accepts human-authored and agent-assisted contributions. Contributions must
remain reviewable, evidence-based, and conservative around repository and infrastructure
effects.

## Before starting

Work from an issue whose dependencies and decision gates are complete. Use the issue to
keep the intended outcome, acceptance criteria, and permitted effects visible throughout
the change. Report new information that changes the approved scope instead of silently
expanding it.

### Agent Definition of Ready

Agent-assisted implementation is ready when all of the following are true:

- The issue states a concrete user outcome and observable acceptance criteria.
- Dependencies are closed and the issue is marked `Status/Ready` or explicitly authorized
  by a maintainer.
- Every `Gate/Human Confirmation` or `Gate/ADR Required` dependency has been approved and
  merged by an authorized human.
- Expected repository writes, command execution, network access, and external-system
  effects are identified and authorized.
- Security, compatibility, documentation, migration, and test implications are understood
  well enough to keep the work bounded.

Agents may prepare research, implementation, tests, documentation, and review material.
Agents do not approve decisions, merge pull requests, publish releases, or close human
gates.

For scan changes, treat ADR 0007's Git lookup as a narrow reviewed exception, not general
permission to execute repository code. Automatic discovery may invoke only the fixed
bounded Git arguments and must reject a repository-contained executable. Tests or manual
checks that pass `--git-executable` authorize that exact file; a repository-contained or
otherwise nonstandard selection is unsandboxed code with potentially arbitrary file,
process, credential, and network effects. Record that authorization and the selected
test executable in review evidence without publishing its path in scan output.

## Development workflow

Create a short-lived `feature/`, `fix/`, `docs/`, or `chore/` branch from `develop` and
open a pull request back to `develop`. `main` receives only reviewed release-promotion pull
requests from `develop`. Direct and force pushes to either protected branch are prohibited.

Install the locked development environment with:

```console
uv sync --locked --all-groups
```

Run the local quality checks with:

```console
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run doorstop
uv run --locked mkdocs build --strict
uv run pre-commit run --all-files
uv --preview-features audit-command audit --locked
uv build --no-sources
```

The dependency audit requires network access. The other core checks, including the
documentation build, remain local. MkDocs extracts the public Python reference from
source without importing Slygentify. Do not weaken or suppress an unrelated check to
make a contribution pass.

## Requirements and traceability

Externally observable behavior belongs in the Doorstop `REQ` document under
`requirements/requirements`. Tests specifications belong in the child `TST` document under
`requirements/test-specifications`.

Map applicable implementation functions and classes with `@implements("REQ...")`. Mark
every collected pytest test with one or more existing specifications using
`@pytest.mark.verifies("TST...")`. Keep each item's `references` synchronized with the
corresponding source keyword, then run:

```console
uv run doorstop -e
```

Do not add normative requirements for an aspiration that is not implemented by the same
change. `doorstop review` and `doorstop clear` are item-specific maintainer approval
actions; do not apply them blanket-wide to make a check pass.

## Pull requests and review

Complete the pull request template with linked issues, requirements and test
specifications, commands and results, side effects, security, compatibility,
documentation, and ADR impact. Preserve the distinction between verified facts,
inferences, recommendations, and unknowns.

Every pull request uses a merge commit and its remote source branch is deleted after
merge. A distinct human reviewer approves when available. During the current solo
maintainer phase, the maintainer reviews the complete change and successful checks, adds
a `Human self-review:` acknowledgement, and personally merges it. Agents do not perform
these governance actions.

### Agent Definition of Done

Agent-assisted implementation is done only when all of the following are true:

- The approved outcome and acceptance criteria are satisfied without unrelated changes.
- Implementation, reviewed requirements, test specifications, and traceability links agree.
- Observable success, failure, partial-result, and boundary cases have deterministic tests.
- Repository writes, command execution, network access, and external-system effects are
  complete, bounded, and reported accurately.
- Security and compatibility consequences are addressed; migrations or residual risks are
  documented where applicable.
- User, contributor, architecture, and ADR documentation is updated when behavior or a
  durable decision changed.
- Every required local and repository-automation check passes without weakening
  unrelated gates.
- The final diff and test evidence are ready for human review and merge.
