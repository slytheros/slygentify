# Scan a repository

Use scan when you need a fresh operating map of an unfamiliar repository.

## Human report

```console
slygentify scan path/to/repository
```

The complete text report groups orientation, workflows, architecture, automation,
concerns, inspection boundaries, and source provenance by component. A successful report
is `complete` or `partial`; partial completion is not an operational failure.

Within **Needs attention**, **Problems & next steps** comes first. When an unknown finding
and a diagnostic describe the same target and source evidence, the finding appears once
as related context under that problem. Remaining records appear under **Unknowns to
confirm**, **Cautions**, or **Recommendations**. Issue counts describe repository
conditions; record counts show that the canonical findings and diagnostics were retained.

An abridged result can look like:

```text
Repository: .
Completion: complete

Working tasks
- Verified: tests use pytest.
  Evidence: pyproject.toml [tool.pytest.ini_options]
```

Text layout and wording are not an automation contract.

## Canonical JSON

```console
slygentify scan path/to/repository --format json > scan.json
```

Successful standard output contains only one canonical `scan-v1` document. Diagnostics
that are part of a trustworthy result stay in that document; operational errors use
standard error and a non-zero exit. See the validated
[minimal scan document](../examples/scan.json).

## Python

```python
from slygentify import dump_scan_json, scan_repository

result = scan_repository("path/to/repository")
print(result.completion)
document = dump_scan_json(result)
```

`scan_repository` returns an immutable `ScanResult`. Passing `git_executable` explicitly
has the trusted-code meaning described in [safety boundaries](../safety.md).

## Investigate a partial result

Review `diagnostics` and `skipped_scopes` before acting on missing evidence. Tight
configured limits, unavailable automatic Git, unreadable files, and environmental
exhaustion can produce partial results without weakening safety controls.

Each diagnostic states what went wrong, what evidence or knowledge was omitted, and a
safe next step when one exists. Resource-limit advice names the exact limit only when
that limit was reached; malformed input instead asks you to correct or intentionally
exclude the exact file.

## Next steps

- Adjust or tighten supported limits in the [configuration reference](../configuration-and-provenance.md).
- Review how partial work is measured in [inspection accounting](../inspection-accounting.md).
- Use [map](map.md) for bounded context or [doctor](doctor.md) to assess managed knowledge.
