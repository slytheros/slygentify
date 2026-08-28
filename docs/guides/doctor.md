# Assess managed repository knowledge

Use doctor to compare current static repository evidence with configuration, generated
guidance, and managed provenance. Doctor is read-only and never executes discovered
validation commands.

## Human report

```console
slygentify doctor path/to/repository
slygentify doctor path/to/repository --verbose
```

Default text includes the resolved repository, completion, severity counts, and every
diagnostic's classification, stable code, target, problem, effect, and remediation.
Verbose mode adds evidence references, one evidence appendix, and every skipped scope.
If fresh inspection is partial, doctor emits one `doctor.inspection.partial` warning for
each distinct cause instead of one umbrella warning. Each warning keeps the exact safe
location and cause-specific action. In verbose and JSON output, synthetic provenance
evidence identifies the originating scan code or resource-boundary reason.

```text
Repository: .
Completion: complete
Diagnostics: 0 errors, 0 warnings, 0 info
```

Human wording and layout may evolve and must not be parsed by automation.

## Canonical JSON

```console
slygentify doctor path/to/repository --format json > doctor.json
```

JSON and `--verbose` cannot be combined. See the validated
[minimal doctor document](../examples/doctor.json).

## Python

```python
from slygentify import doctor_repository, dump_doctor_json

result = doctor_repository("path/to/repository")
for diagnostic in result.diagnostics:
    print(diagnostic.severity, diagnostic.code, diagnostic.remediation)
document = dump_doctor_json(result)
```

## Exit status for automation

| Exit | Meaning |
| ---: | --- |
| 0 | A trustworthy result has no warning or error diagnostics. |
| 1 | A trustworthy complete or partial result has a warning or error. |
| 2 | Usage or caller-selected input prevented a result. |
| 3 | An operational or internal failure prevented a trustworthy result. |

Result exits write the requested report to standard output with empty standard error.
Exits 2 and 3 emit no result and report the failure on standard error.

## CI recipes

Use canonical JSON when automation needs the report. Preserve the exit status so a
finding (1) remains distinct from invalid input (2) and an operational failure (3).

### POSIX shell

```sh
set +e
slygentify doctor . --format json > doctor.json
doctor_status=$?
set -e

case "$doctor_status" in
  0) echo "Doctor found no actionable diagnostics." ;;
  1) echo "Doctor findings are recorded in doctor.json." >&2; exit 1 ;;
  2) echo "Doctor invocation or target was invalid." >&2; exit 2 ;;
  3) echo "Doctor could not produce a trustworthy result." >&2; exit 3 ;;
esac
```

### PowerShell

```powershell
& slygentify doctor . --format json > doctor.json
$doctorStatus = $LASTEXITCODE

switch ($doctorStatus) {
    0 { Write-Output "Doctor found no actionable diagnostics." }
    1 { Write-Error "Doctor findings are recorded in doctor.json."; exit 1 }
    2 { Write-Error "Doctor invocation or target was invalid."; exit 2 }
    3 { Write-Error "Doctor could not produce a trustworthy result."; exit 3 }
}
```

## Next steps

- Review an [initialization dry-run](init.md) before adopting or regenerating guidance.
- Investigate partial evidence with the [scan guide](scan.md#investigate-a-partial-result).
- Use the [configuration/state reference](../configuration-and-provenance.md) to resolve
  configuration, provenance, or ownership findings.
