# Changelog

All notable Slygentify changes will be recorded here. Package releases follow
[Semantic Versioning](https://semver.org/) from 1.0 onward. Package and JSON Schema
major versions are independent compatibility surfaces.

## Unreleased

- Add first-class state-v1 upgrades and bounded invalid-state rebuilds to `init`, using
  visible markers as a section-only recovery boundary while preserving surrounding text.
- Add explicit initialization recovery classifications, adoption and replacement
  fallbacks, forward-schema refusal, and condition-specific doctor remediation.
- Add explicit `problem`, `limitation`, and `notice` dispositions across scan, map,
  doctor, and initialization diagnostics while keeping schema-major-1 readers compatible.
- Route scan and explorer diagnostics into Problems & next steps, Limitations &
  explanations, and Notices with lossless pairing and neutral aggregate counts.
- Preserve distinct structured partial-scan causes through doctor so each malformed
  input, unavailable Git lookup, unsafe path, unresolved reference, or resource boundary
  receives an exact effect and safe next step.
- Rework scan and explorer attention output around disposition groups, related unknown
  context, explicit caution routing, and separate item/record counts while retaining
  every canonical record.
- Remove raw parser detail from diagnostics and make supported recovery guidance
  condition-specific, including intentional exclusion where appropriate.

## [1.0.0rc1] - 2026-08-27

- Add reproducible, human-gated PyPI and TestPyPI release workflows with provenance,
  twelve-context installation verification, fail-closed partial recovery, and a public
  maintainer runbook.
- Contain source and wheel distributions to the runtime package and release metadata, and
  keep acceptance-measurement helpers as development-only tooling.
- Derive package metadata and the public `slygentify.__version__` value from one private
  version source.
- Prepare accurate public installation, task, API, schema, support, security, and
  migration documentation.
- Add a CLI and Python first-use tutorial with CI-validated representative scan, map,
  and doctor output.
- Add a strict, local MkDocs documentation build with generated Python API reference.
- Adopt SPDX package metadata and public project links for the forthcoming GitHub
  repository.

Slygentify has not yet published a public PyPI release. This changelog does not invent
historical releases, tags, dates, or private development chronology.
