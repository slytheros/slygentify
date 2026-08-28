# Troubleshoot safe recovery

Use this guide when a trustworthy result is partial, a configuration prevents inspection,
or Slygentify refuses to change managed guidance. Do not weaken containment, sensitive
content, link, nested-repository, command, or network protections to work around a
finding.

## A scan, map, or doctor result is partial

Read `diagnostics` and `skipped_scopes` before treating missing evidence as absent. A
partial result can reflect an unreadable file, configured resource limit, environmental
exhaustion, or unavailable tracked-path discovery. Keep the result for the inspected
boundary, then correct the reported repository condition or use a tighter task scope.
Doctor reports each distinct partial cause separately. Follow the action attached to
that cause; do not raise a resource limit unless the diagnostic names the limit that was
actually reached.

See the [scan guide](scan.md#investigate-a-partial-result) and
[inspection accounting](../inspection-accounting.md) for the exact boundary semantics.

## A supported manifest is malformed

The diagnostic identifies the exact manifest and the declarations that could not be
established. Correct its syntax, encoding, duplicate keys, or required top-level shape,
then rerun the command. If the file is intentionally outside the repository knowledge
you want Slygentify to inspect, exclude that exact scope in root `slygentify.toml` and
rerun. This is not a resource-limit condition, so increasing a scan limit is irrelevant.

## Git tracked-path discovery is unavailable

Slygentify still requires a local Git repository, but it can inspect when the standard
Git executable is unavailable. The result becomes partial because tracked manifests
hidden by checked-out ignore rules may be omitted.

Restore a trusted Git executable on `PATH` and rerun the command. Use
`--git-executable` only after inspecting the exact selected file: it is trusted,
unsandboxed code and can have arbitrary effects. It authorizes only the fixed lookup,
not repository commands. See [safety boundaries](../safety.md#fixed-git-lookup).

## Configuration prevents a result

`slygentify.toml` is read only at the selected Git root. Malformed TOML, unknown keys,
unsafe paths, duplicate component paths, or invalid patterns fail before traversal.
Correct the reported root configuration error, then rerun the command. Do not move the
file to a parent, component, profile, or environment location: those locations are not
configuration sources.

See the [configuration reference](../configuration-and-provenance.md) for the accepted
shape and path rules.

## Init preserves existing guidance

For an unmanaged or human-edited safe regular `AGENTS.md`, ordinary init prints a
paste-ready Slygentify section, preserves the file, and exits 4. Paste the section where
it fits your existing guidance; this deliberate manual path does not create managed
provenance state. Run `slygentify init PATH --dry-run` to review the full candidate and
sidecar before deciding whether to replace the file.

Missing managed, malformed, and unsafe targets still fail closed. Do not use `--replace`
as routine recovery: it can discard a regular `AGENTS.md`, creates no backup, and never
permits unsafe entries.

When `.slygentify/state.json` is malformed, unsupported, oversized, unreadable, or an
unsafe filesystem entry, init cannot validate ownership of `AGENTS.md`. Upgrade and
retry `slygentify init PATH --dry-run` first. If a current build still rejects it, rename
the sidecar to a new non-existing backup name; do not delete or overwrite it. Review the
next dry-run and apply only if it reports a recoverable or otherwise expected safe
ownership state. `--replace` never overrides invalid-state protection.

If you intentionally want to replace a regular file, first retain the content you need,
review the dry-run with `--replace`, then choose the explicit apply command. See the
[init guide](init.md#apply-a-reviewed-plan) for ownership behavior.

## Doctor exits 1 in automation

Exit 1 means doctor produced a trustworthy complete or partial report with at least one
warning or error diagnostic. Read the report; use exits 2 and 3 to distinguish invalid
input from an operational failure. The [doctor CI recipes](doctor.md#ci-recipes) show
safe POSIX-shell and PowerShell handling while retaining canonical JSON.

## Next steps

- Return to the [first-repository tutorial](../tutorials/first-repository.md) for a safe
  first-use workflow.
- Use [scan](scan.md), [map](map.md), [init](init.md), or [doctor](doctor.md) once the
  reported condition is understood.
