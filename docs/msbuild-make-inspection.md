# Static Visual Studio, MSBuild and Make inspection

Slygentify recognizes C++ project boundaries and build tooling from local source evidence.
It does not load Visual Studio, evaluate MSBuild or Make, follow imports, select an active
configuration, execute recipes, or infer preferred commands.

## C++ projects

A safely parsed `.vcxproj` with an MSBuild `Project` root establishes a project boundary
and the `msbuild` ecosystem facet. Both the standard MSBuild XML namespace and an
unqualified root are supported. Literal `ProjectName` and `PlatformToolset` properties,
and `LanguageStandard`/`LanguageStandard_C` declarations in property or compiler settings,
are reported with indexed XML element locators. These are declarations in that specific
file, not effective settings, installed tools, language use throughout a directory, or
proof of a successful build. Source files are not read.

Conditions on a declaration or any enclosing element, and declarations inside
`Choose`/`When`/`Otherwise` or `Target`, retain conditional/deferred wording. Different
unconditional values for the same field within one property or compiler-metadata scope
retain a conflict without selecting a winner. Project properties, compiler defaults and
individual compiler-item elements are separate scopes. Different source items and a
compiler default with a per-item override do not create conflicts. Item selectors and
possible overlaps between separate elements are not evaluated. Different configurations
and different project files remain separate source scopes.
Imports and executable extensions remain unresolved; `.props`, `.targets`, SDKs and
`Directory.Build.*` are not loaded. Property (`$(...)`), metadata (`%(...)`) and item
(`@(...)`) expressions remain unresolved; their values and sensitive literals are withheld.

Multiple projects in one directory retain separate file evidence in one component,
with an explicit file-attribution limitation. Existing CMake, Python, JavaScript and
configured evidence remains attached, with stable path-based component identity.

## Solutions

Classic `.sln` and XML `.slnx` files contribute literal C++ project membership.
Supported `.slnx` entries are `Project Path` elements directly inside `Solution` or
logical `Folder` elements. Folder names do not change path resolution or create
components. Other solution configuration remains unresolved. Non-C++ projects are
outside this inspection feature.

Project paths resolve relative to the solution file, accepting Windows separators and
in-root relative parent navigation. Absolute, drive-qualified, UNC, dynamic, sensitive
and escaping references are rejected. Only projects already validated through the bounded
repository catalog can become members. Exact paths take precedence. When no validated
exact target exists, a unique case-insensitive match among catalogued project paths can
resolve to a validated project; the finding reports both the declared spelling and actual
catalogued spelling. Ambiguous matches remain unknown, including collisions with invalid
or unreadable catalogued projects. A resource-limited, incomplete lookup cannot establish
a unique match. Matching never opens an alternate path or expands inspection scope.
Missing, ignored, unsafe or invalid targets retain limitations.

At least one validated C++ member establishes a workspace boundary at the solution's
directory. Member relationships connect directory components, with all membership evidence
retained. A project beside its solution retains a membership finding without a
self-relationship. Solutions without validated C++ members retain unresolved evidence
rather than creating a workspace.

## Makefiles

`GNUmakefile`, `makefile` and `Makefile` contribute Make tooling evidence. They do not
establish a component, ecosystem facet or C/C++ language claim. Evidence belongs to the
nearest independently established component, including a configured boundary. Without an
owner, evidence remains repository-scoped with an ownership limitation.

Variables, includes, computed targets and recipes remain uninterpreted. No include is
opened and no recipe or shell expansion is executed. Presence does not select a Make
dialect, default target, preferred command or working build.

## Safety and interfaces

Normal containment, sensitive-file, ignore, tracked-file, link and resource guards apply.
XML is parsed from bytes with encoding-aware handling; DTD declarations are rejected
before entity expansion. Excessive nesting and malformed XML/solution entries produce
partial results while independent evidence remains available. Parser allocations and
retained records consume the memory budget; parsing and traversal check the deadline.
Classic solutions support UTF-8 (including BOM) and BOM-marked UTF-16.

Canonical evidence appears in scan text, interactive exploration and JSON. Map places
identity, toolset and standard declarations in orientation, Make tooling in architecture,
and unresolved findings in boundaries. Initialization fingerprints inspected files and
retains derivations; doctor detects supported component/tooling drift without changing
human guidance. Public Python interfaces and JSON schema versions are unchanged.

See Microsoft's [C++ project structure reference](https://learn.microsoft.com/en-us/cpp/build/reference/vcxproj-file-structure?view=msvc-170)
and [solution persistence project](https://github.com/microsoft/vs-solutionpersistence)
for the build systems' broader evaluation and solution semantics.
