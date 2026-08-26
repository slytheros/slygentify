# Migration and compatibility guidance

Slygentify is currently `0.1.0` and has no public PyPI release history. There is no
released-version migration to perform yet. Future changes will be recorded in the root
changelog without recreating private development chronology.

## Package versions

After 1.0, package releases follow Semantic Versioning:

- patch releases contain fixes and security updates;
- minor releases contain compatible additions and announced deprecations; and
- major releases may remove or change public package interfaces.

Only documented names exported from `slygentify` are public Python compatibility
surfaces. A replacement is documented before deprecation. Deprecated public Python names
remain available with `DeprecationWarning` throughout 1.x and are removed no earlier than
the next package major.

## JSON Schema versions

Scan, map, doctor, and initialization state have separately identified schema-major-1
documents. Package and wire major versions do not advance together automatically.
Readers accept documented same-major additive fields while producers emit only canonical
declared fields.

Deprecated JSON fields remain accepted throughout their schema major. Removal requires
the next applicable schema major, an explicit reader or migration path, and updated
guidance. Never edit a canonical document in place without preserving the original for
recovery and comparison.

## Upgrade procedure

1. Read the changelog and the relevant schema notes before upgrading.
2. Preserve existing configuration, generated artifacts, and machine-readable results.
3. Install the exact reviewed release in an isolated environment.
4. Validate stored JSON with the new reader and rerun scan or doctor for fresh evidence.
5. Review an init dry-run before regenerating managed guidance.
6. Roll forward with a new version if a published release is defective.

Published versions and filenames are immutable. A defective but intact release is
normally yanked with a reason, then replaced by a corrected version. Exact pins may still
select a yanked release. Public history, tags, and versions are never rewritten or reused.
