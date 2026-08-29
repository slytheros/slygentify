# Initialization Python API

Use the initialization API to review and, only after an explicit decision, apply concise
root `AGENTS.md` guidance with its deterministic ownership sidecar.

## Plan before writing

`plan_initialization(path=".", *, replace=False, adopt=False)` returns an immutable
`InitializationPlan` without writing. It includes ownership classification,
applicability, separate actions and exact bytes for `AGENTS.md` and
`.slygentify/state.json`, and actionable diagnostics.
`state_recovery` is `none`, `schema-upgrade`, or `state-rebuild`; the result repeats the
applied classification. Source digests in the plan ensure that artifact or invalid-state
changes between planning and application are rejected.
Each `InitializationDiagnostic` exposes a `problem` or `notice` disposition. Current
initialization conditions do not use `limitation`; operational failures remain problems.

Ordinary plans apply only to `new`, `clean-managed`, and `recoverable-state` ownership.
`replace=True` is required for unmanaged, human-edited, or missing managed artifacts
that lack a safe marked-section recovery. `adopt=True` can preserve unmanaged guidance
while rebuilding bounded invalid state. Neither option authorizes unsafe entries,
unbounded state, or a newer schema downgrade. The plan is the Python equivalent of
the CLI dry-run review surface. The full ownership vocabulary also includes
`unmanaged`, `human-edited`, `missing-managed-artifact`, `invalid-state`, and
`unsafe-entry`.

## Apply a reviewed plan

`apply_initialization(plan)` revalidates and performs local atomic writes, returning
`InitializationResult`. `InitializationError` exposes stable `code`,
`changed_locations`, and `recovery` attributes, including the bounded case where
guidance changed but sidecar creation did not.

The generated state is provenance, not a cached scan authority. Scan and map do not read
or write it; a fresh scan remains authoritative. See the [initialization task guide](../guides/init.md),
[configuration and provenance reference](../configuration-and-provenance.md), and
[state schema](../schemas.md).

Root `slygentify.toml` is applied by scans as documented in the configuration reference.
