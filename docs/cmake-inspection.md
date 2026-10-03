# Static CMake inspection

Slygentify reads CMake source as repository evidence. It never configures a project,
loads a toolchain, expands CMake expressions, resolves dependencies, or runs a build.
Declarations describe source text, not effective build configuration or successful work.

## Project identity and build selections

An unconditional literal `project(...)` establishes a project boundary. Existing generic
boundary evidence remains attached. Explicit `C` or `CXX` languages or supported literal
standard declarations add the `cmake` ecosystem facet to that component. Omitting
`LANGUAGES` does not establish C/C++ use, even though CMake itself has default languages.
`LANGUAGES NONE` can describe a header-only project; a literal `cxx_std_*` compile feature
can supply its C++ declaration evidence. ESP-IDF markers retain generic boundary support.

Supported source declarations include:

- literal project names and explicit languages in either `project` signature;
- `cmake_minimum_required(VERSION ...)`, including literal policy ranges;
- numeric `CMAKE_C_STANDARD` and `CMAKE_CXX_STANDARD` variable selections;
- literal target `C_STANDARD`/`CXX_STANDARD` properties through `set_property(TARGET ...)`
  and `set_target_properties(...)`;
- literal `c_std_*` and `cxx_std_*` selections through `target_compile_features(...)`;
- `find_package(...)` dependency requests, including literal versions and modifiers;
- `find_package(GTest ...)`, `find_package(Catch2 ...)`, `include(GoogleTest)`,
  `include(Catch)`, `include(CTest)`, `enable_testing()` and `add_test(...)` test evidence;
- safely readable `.clang-format` and `.clang-tidy` configuration presence.

Standard selections retain directory or target scope. Different targets can declare
different standards without implying a project-wide conflict. Tool and testing evidence
does not prove installation, runtime use, test execution, or build success.

Dependency requests retain literal arguments even when other arguments are dynamic.
For example, `find_package(ortools 9.15.6755 EXACT CONFIG REQUIRED PATHS "${prefix}")`
retains the written name, version and modifiers; the dynamic path is withheld and marked
unresolved. Dynamic versions remain unknown rather than being expanded. Requests with
dynamic package names or credential-shaped/host-specific values remain withheld.

The parser recognizes a leading UTF-8 BOM, multiline calls, line/bracket comments,
quoted/bracket arguments, and escapes. Malformed project option/value pairs leave
language selections unresolved. Literal declarations inside conditions, loops, functions, and macros remain
verified source observations with explicit conditional/deferred wording. They may never
take effect and do not establish unconditional boundaries. Variables, generator
expressions, unsupported argument forms, and malformed syntax remain unknown or diagnostic.
Includes and user-defined commands are not evaluated.

## Subdirectories and composition

Literal `add_subdirectory(...)` references resolve only through the bounded repository
catalog. Ordinary build directories remain part of their owning project. A child becomes
a component only when independent project evidence supports its boundary. Unconditional
references between established components produce `cmake-subdirectory` relationships;
conditional references remain source declarations.
Unsupported trailing arguments leave the reference unresolved rather than establishing
a relationship; supported forms allow an optional binary directory and the
`EXCLUDE_FROM_ALL`/`SYSTEM` flags.

Missing, excluded, escaping, linked, unreadable, and cyclic references produce explicit
limitations. Inspection never follows a reference around containment, ignore, sensitive
content, or resource guards. Co-located Python/JavaScript and generic facets remain intact.
CMake declarations and auxiliary files inside independently established nested components
belong to those components rather than an outer CMake project.

## Shared presets

`CMakePresets.json` follows normal working-tree inspection rules, just like Python and
JavaScript manifests. Git tracking is not required; tracked ignored files retain the
existing bounded Git discovery behavior. `CMakeUserPresets.json` is excluded from CMake
inspection.

For preset format versions 1–12, Slygentify surfaces named configure presets, build/test
presets from version 2, and workflow presets from version 6. It retains hidden status and
directly written configure-preset generators and safe relative toolchain-file selections
(from version 3). JSON Pointer locators identify each observation. A toolchain selection
does not authorize reading or executing the selected file.
Escaped lone surrogates in JSON strings are invalid preset input and produce a partial
result instead of interrupting the scan.

Inheritance, conditions, macro expansion, includes, package presets, and unsupported
versions remain explicit limitations. Literal selections are reported even when another
construct prevents determining their effective value. Preset names are declarations,
not synthesized runnable or preferred commands.

## Workflows and operating maps

Supported GitHub Actions, Gitea Actions and GitLab CI files contribute safely attributable
literal commands. Checkout ownership and working directories constrain attribution;
workflow-level run directories apply unless a job or step overrides them. Repeated
GitLab local includes are inspected once; only references to active ancestors are cycles.
external/dynamic scopes and includes remain unresolved. Credential-shaped literals are
withheld, including CMake credentials written as separate key/value arguments.
GitLab named `run` steps contribute their literal `script` values; reusable steps remain
unresolved. Jobs named `pages` are inspected as jobs. Commands are declared evidence and are never executed or promoted to preferred
workflows.

`scan` text, interactive exploration and JSON share these canonical records. `map` places
identity/language/version/standard declarations in orientation, presets in workflows,
dependencies/tools in architecture, CI commands in automation, and unknowns/limitations
in boundaries. Initialization captures deterministic provenance; doctor compares fresh
component and tooling declarations while preserving human guidance.

See the [CMake language reference](https://cmake.org/cmake/help/latest/manual/cmake-language.7.html)
and [preset format](https://cmake.org/cmake/help/latest/manual/cmake-presets.7.html) for CMake's
evaluation semantics. Dependency managers, other build systems, arbitrary `.cmake`
includes, deeper ESP-IDF inspection, and effective-build verification are not supported
by this stage.
