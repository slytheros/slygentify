# Explore your first repository

Use this tutorial to build and interpret a safe operating map, narrow it to one task,
review proposed agent guidance, and assess managed knowledge. Start with your own Git
repository when you want a read-only first pass. Use the checked-in deterministic fixture
when you need exact CLI and Python outcomes, including a known partial result.

## Path A: inspect your own Git repository

Choose a local Git repository you may inspect. Scan, map, doctor, and an init dry-run
are read-only; do not run `slygentify init` without `--dry-run` unless you have reviewed
the proposed files and want them created.

From the repository root, run:

```console
slygentify scan .
slygentify map . --scope .
slygentify init . --dry-run
slygentify doctor .
```

The scan can be `complete` or `partial`. A partial result is still useful when you
review its diagnostics and skipped scopes before relying on missing evidence. Map writes
one canonical JSON document to standard output. Doctor may report unmanaged guidance
until you decide to apply a reviewed initialization plan; a warning or error finding
returns exit 1 while still producing its trustworthy report. See
[troubleshooting](../guides/troubleshooting.md) when a result needs recovery.

## Path B: use the deterministic tutorial fixture

The fixture is a small fictional Python and JavaScript repository with one intentionally
malformed manifest. Slygentify will report that manifest as a partial inspection
boundary. This is expected: a partial result remains useful when its diagnostics and
skipped scopes explain what could not be inspected.

### Prepare a disposable copy

Copy `docs/examples/tutorial-repository` outside the Slygentify checkout and initialize
the copy as a Git repository. Git establishes the repository boundary; Slygentify does
not execute commands declared by the fixture.

### POSIX shell

```sh
cp -R docs/examples/tutorial-repository /tmp/slygentify-tutorial
git -C /tmp/slygentify-tutorial init --quiet
```

### PowerShell

```powershell
Copy-Item docs/examples/tutorial-repository $env:TEMP/slygentify-tutorial -Recurse
git -C $env:TEMP/slygentify-tutorial init --quiet
```

Set `path/to/tutorial-repository` below to that copied directory.

### 1. Scan the repository

The CLI presents a complete human report:

```console
slygentify scan path/to/tutorial-repository
```

The Python API returns the same normalized result:

```python
from slygentify import scan_repository

result = scan_repository("path/to/tutorial-repository")
print(result.completion)
for diagnostic in result.diagnostics:
    print(diagnostic.disposition, diagnostic.code, diagnostic.message)
```

Expected outcome: completion is `partial`; valid root, web, and demo components are still
present, while a diagnostic identifies `packages/broken/package.json`. The demo component
is inferred to be auxiliary because it lives below `examples/`. Review diagnostics and
skipped scopes before relying on evidence from the omitted boundary. In the human
report, the diagnostic is the problem and the unknown JavaScript component finding is
nested beneath it as related context. Correct the malformed JSON or intentionally
exclude that file if it is outside the intended inspection scope.

The checked-in [representative scan JSON](../examples/representative-scan.json) is the
canonical machine-readable result for this fixture. Use `--format json` or
`dump_scan_json` when automation needs that interface.

### 2. Map one task

Map the logical file you intend to change, even though it does not exist in the fixture:

```console
slygentify map path/to/tutorial-repository \
  --scope packages/web/src/app.ts \
  --section orientation --section workflows \
  --section architecture --section boundaries \
  --max-bytes unlimited > map.json
```

```python
from slygentify import map_repository

projection = map_repository(
    "path/to/tutorial-repository",
    scope="packages/web/src/app.ts",
    sections=("orientation", "workflows", "architecture", "boundaries"),
    max_bytes="unlimited",
)
print(projection.navigation.owner)
```

Expected outcome: `navigation.owner` identifies `packages/web`, while the projection
retains the repository boundary and the evidence needed for selected records. Follow a
child path with another map call when the first projection points to a deeper component.
The [representative map JSON](../examples/representative-map.json) shows the exact
canonical document.

### 3. Review initialization without writing

Dry-run prints proposed `AGENTS.md`, ownership state, and a concise provenance summary
without creating files; add `--show-state` to inspect exact state JSON:

```console
slygentify init path/to/tutorial-repository --dry-run
```

```python
from slygentify import plan_initialization

plan = plan_initialization("path/to/tutorial-repository")
print(plan.agents_markdown)
print(plan.state_json.decode("utf-8"))
print(plan.can_apply)
```

Expected outcome: the plan is reviewable and applicable, but no files change. Check that
the proposed guidance is concise, evidence-backed, and appropriate before deciding
whether to apply it. If you want to practice application, do so only in the disposable
copy with `slygentify init path/to/tutorial-repository` or `apply_initialization(plan)`.

### 4. Assess managed knowledge

Doctor compares fresh evidence with configuration, generated guidance, and managed
provenance without changing or executing the repository:

```console
slygentify doctor path/to/tutorial-repository
```

```python
from slygentify import doctor_repository

assessment = doctor_repository("path/to/tutorial-repository")
for diagnostic in assessment.diagnostics:
    print(
        diagnostic.severity,
        diagnostic.disposition,
        diagnostic.code,
        diagnostic.remediation,
    )
```

Expected outcome before applying initialization: doctor reports unmanaged guidance and a
`doctor.inspection.partial` warning for `packages/broken/package.json`. The warning says
the package boundary and declarations were omitted and recommends correcting or
intentionally excluding that file; it does not suggest changing an unrelated resource
limit. Its result is still trustworthy within the reported boundary. The
[representative doctor JSON](../examples/representative-doctor.json) contains the exact
canonical result.

After applying managed guidance, rerun `slygentify doctor` after structural, tooling, or
workflow changes. When it reports drift, review `init --dry-run` before explicit
regeneration. For an existing human-owned `AGENTS.md`, preview visible-section adoption
with `slygentify init PATH --adopt --dry-run`.

## What to do next

- Use the [scan guide](../guides/scan.md) to investigate partial results in your own repository.
- Use the [configuration reference](../configuration-and-provenance.md) to declare known
  components or tighten resource limits.
- Use the [JSON Schema reference](../schemas.md) when consuming canonical output.
- Review the [safety boundary](../safety.md) before selecting a nonstandard Git executable.
