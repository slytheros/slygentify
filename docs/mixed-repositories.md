# Mixed repository composition

Use this reference when one repository contains multiple ecosystems, nested components,
workspace members, auxiliary projects, or overlapping component relationships.

Slygentify emits at most one component for each repository-relative path. Its identifier
continues to derive from that path, so adding a newly recognized ecosystem facet does not
replace the component identity.

Each component lists its sorted ecosystem `ecosystems`. The singular `ecosystem` remains
the same value when one facet is present and becomes `mixed` when two or more facets are
present. Co-located evidence is composition, not a conflict: a root containing valid
Python, JavaScript, and unsupported project evidence retains all three facets.

`Component.role` remains `unknown` unless the component path contains an exact lowercase
segment named `test`, `tests`, `example`, `examples`, `docs`, `template`, or `templates`.
Those components are retained with role `auxiliary` and an inferred
`composition.auxiliary-component` finding tied to the same manifest evidence; the text
and interactive reports place them in secondary navigation rather than suppressing them.

Relationships are directed and deterministic:

- `contains` connects the nearest component ancestor to its direct component descendant;
  it is inferred from evidence-backed paths.
- `workspace-member` connects a parsed workspace root to each verified member and retains
  the workspace declaration evidence.

Both relationships may connect the same pair because filesystem containment and declared
workspace membership are different facts. Multiple distinct workspace parents are all
retained and produce an actionable overlap diagnostic; no owner is selected silently.

Generic engineering evidence remains deliberately narrow. A static CMake `project(...)`
or `idf_component_register(...)` marker may establish a generic project boundary. A valid
unique-key UTF-8 `.kicad_pro` JSON object may establish a generic engineering-project
boundary, and sibling `.kicad_sch` and `.kicad_pcb` files corroborate it. Those artifacts
alone do not establish a component. None of these observations claims first-class CMake,
ESP-IDF, or KiCad support, installation, runtime use, or build success.

Ambiguous CMake or KiCad boundaries identify the exact path that can be declared with
`[[scan.components]]` in the root `slygentify.toml`. Scan loads and applies those
declarations today, retaining configured and detected evidence together. Component
declarations do not change workspace membership; if a component matches multiple
workspace roots, narrow or exclude the overlapping workspace declarations instead.

See the complete [`slygentify.toml` reference](configuration-and-provenance.md) for path
rules, conflict behavior, and examples.
