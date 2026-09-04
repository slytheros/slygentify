# Architecture decision records

Architecture decision records (ADRs) capture decisions that materially constrain future
development, are costly to reverse, or are likely to be questioned again. Routine
implementation details do not need an ADR.

Accepted ADR bodies preserve the decision and context at the time of acceptance. They
may describe target behavior beyond the current release. Use the current
[user documentation](../index.md), source, tests, and requirements for implementation
status; supersede an ADR rather than rewriting its history.

## Workflow

1. Copy [`template.md`](template.md) to the next four-digit filename and give it a concise
   kebab-case title.
2. Keep the status `Proposed` while alternatives and consequences are being reviewed.
3. Link the decision issue and the pull request that carries the ADR.
4. Only an authorized human may change a decision-gated ADR to `Accepted` and merge it.
5. Do not rewrite an accepted decision when policy changes materially. Add a new ADR and
   mark the prior record `Superseded` with a link to its replacement.

Supported statuses are `Proposed`, `Accepted`, `Superseded`, and `Rejected`. Agents may
prepare proposed records and approval material, but they do not approve decisions, merge
pull requests, or close human gates.

## Index

| ADR | Status | Decision |
| --- | --- | --- |
| [ADR 0001](0001-repository-governance.md) | Accepted | Repository governance |
| [ADR 0002](0002-public-1.0-product-contract.md) | Accepted | Public 1.0 product contract |
| [ADR 0003](0003-repository-safety-boundaries.md) | Superseded | Repository safety boundaries |
| [ADR 0004](0004-stable-python-json-interfaces.md) | Accepted | Stable Python and JSON interfaces |
| [ADR 0005](0005-repository-inspection-configuration-and-provenance.md) | Accepted | Repository inspection, configuration, and provenance |
| [ADR 0006](0006-scan-pipeline-extension-boundaries.md) | Accepted | Scan pipeline and extension boundaries |
| [ADR 0007](0007-git-backed-tracked-path-discovery.md) | Accepted | Git-backed tracked-path discovery |
| [ADR 0008](0008-editable-agents-artifact-ownership.md) | Superseded | Editable AGENTS.md artifact ownership and regeneration |
| [ADR 0009](0009-thin-agents-and-task-scoped-operating-maps.md) | Accepted | Thin AGENTS.md and task-scoped operating maps |
| [ADR 0010](0010-static-doctor-diagnostics-and-automation-contract.md) | Accepted | Static doctor diagnostics and automation contract |
| [ADR 0012](0012-visible-managed-guidance-sections.md) | Superseded | Visible managed guidance sections |
| [ADR 0011](0011-public-migration-release-and-support-policy.md) | Accepted | Public migration, release, and support policy |
| [ADR 0013](0013-generated-artifact-recovery.md) | Accepted | First-class generated-artifact recovery |
| [ADR 0014](0014-post-1.0-release-checklist.md) | Proposed | Post-1.0 resumable release checklist |
