# Python inspection

Use this reference when you need to determine exactly which Python repository evidence
Slygentify recognizes, how it classifies that evidence, and which inferences it excludes.

Slygentify reports narrow facts from static repository evidence. It does not import a
target project, execute configuration or commands, resolve dependencies, contact a
network service, or select a preferred tool.

| Area | Supported evidence | Reported claim |
| --- | --- | --- |
| Components | `[project]`, `[build-system]`, `[tool.poetry]`, or `[tool.uv.workspace]` in `pyproject.toml`; legacy `setup.cfg` with `[metadata]` and `[options]` | A Python package or workspace boundary is declared. `setup.py` alone is only an unknown generic package candidate. Unrendered template paths remain unknown rather than current components. A component beneath an exact lowercase `test`, `tests`, `example`, `examples`, `docs`, `template`, or `templates` path segment is inferred as auxiliary from its existing manifest evidence. Same-root generic or JavaScript evidence produces one component with a Python facet and a `mixed` summary. |
| Metadata | PEP 621 name, Python constraint, dependencies, optional dependencies, scripts, GUI scripts, and PEP 735 dependency groups | The field, normalized direct dependency name, scope, or entry-point name is declared. Dynamic fields are not evaluated. |
| Managers | `uv.lock`, `uv.toml`, `[tool.uv]`, uv workspaces, Poetry metadata/groups/lock, and component-local `requirements*.txt` or `constraints*.txt` | Each manager or membership declaration is present. Safe uv memberships also produce directed `workspace-member` relationships. Ordinary requirements entries are direct declarations; constraints and recognizable generated `pip-compile` output remain manager evidence without direct-dependency claims. Coexisting configuration and requirements evidence is retained without a conflict; competing lock families at one root produce an actionable conflict diagnostic. |
| Runtime | `project.requires-python`, Poetry's Python dependency, component-root `.python-version`, and literal `actions/setup-python` values or static matrix entries | Each supported range or interpreter selection is independently declared. Exact selections are checked against parseable supported ranges; compatible and incomparable values are not called conflicts. No combined effective range is calculated. |
| Tools | pytest, coverage/pytest-cov, Ruff, Black, Flake8, pycodestyle, mypy, Pyright, `tox.ini`/tox TOML, pre-commit, and `conftest.py` convention evidence | Configuration, direct-dependency, or low-strength convention evidence is present; installation and use remain unclaimed. Multiple configuration locations conflict only at the same supported component root. |
| Frameworks | Direct FastAPI, Flask, Django, SQLAlchemy, and Alembic declarations; `alembic.ini` configuration | The component directly declares the named dependency or has Alembic configuration. Runtime activation remains unclaimed. |
| CI | Literal Gitea/GitHub `run` steps and GitLab script/run fields, including bounded in-root `include:local` files | The attributable command text is declared. Static scalar, object-axis, and `matrix.include` Python selections are retained with exact locators. Literal GitHub/Gitea checkout paths constrain component attribution; expression-only commands and ambiguous or external checkout scopes are not presented as component commands. External and dynamic includes or expressions remain unknown and are never fetched. |

TOML evidence uses dotted locators, requirements use line locators, and workflow YAML
uses JSON-Pointer-style locators. Commands are shown in the complete default report,
the interactive explorer, and JSON, and are escaped for safe presentation.
Whitespace-delimited requirements comments are not
part of the PEP 508 declaration. A recognizable credential literal is withheld and
produces an explicit unknown finding and diagnostic; the diagnostic asks the user to
review whether the text is intentional test data and recommends a secret reference only
when it is sensitive. Source expressions and variable
references are retained because they do not disclose a literal value.

Malformed, unreadable, unsafe, or budget-exhausted supported evidence makes the result
partial. Conflicting but fully inspected declarations remain a complete result because
Slygentify preserves them instead of choosing one.

A runtime conflict is reported only when an exact interpreter selection is demonstrably
outside a parseable supported range. Its diagnostic lists the component, competing values,
source paths and locators, explains the incompatibility, and suggests aligning the selection
or intentionally updating the supported range.

Source-backed diagnostics retain the evidence path even when they also identify a
component. Malformed, unsupported, dynamic, redacted, and conflict messages identify
the relevant locator where available and explain the next corrective or review action.

Public 1.0 intentionally excludes setup.py execution, source-import inference, dynamic
configuration, PDM, Pipenv, Hatch, Conda, Rye, lock resolution, marker evaluation,
recursive requirements directives, transitive framework inference, Dockerfile or README
heuristics, and external CI includes. Unsupported evidence remains generic or unknown.
