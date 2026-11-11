# Static Meson, Autotools and Bazel inspection

Slygentify recognizes declared project identities, component boundaries, language
evidence and local composition. It does not execute Meson, m4, Autoconf, Automake or
Starlark, read generated build introspection, verify effective configuration, or fetch
dependencies. Dependency and workflow extraction for these build systems is deferred.

## Meson

A direct, unconditional `project()` with a literal name establishes a project component
and the `meson` ecosystem facet. Supported optional versions, positional language
strings and literal language arrays remain source declarations. `add_languages()` also
contributes language declarations. Conditional/deferred declarations retain that context
without establishing unconditional boundaries or active language use. File presence
alone does not establish a project or C/C++ use.

Literal `subproject()` references resolve only to independently recognized projects
already in the guarded catalog, under `subprojects` or a literal `subproject_dir`.
`subdir()` identifies ordinary catalogued `meson.build` fragments without creating
components. Computed references, unresolved directories, wraps and external subprojects
remain limitations. No wrap file is fetched or interpreted. Multiple project declarations
retain all identity evidence and prevent selecting an effective subproject directory.

## Autoconf and Automake

A direct, unconditional `AC_INIT` with a literal, bracket-quoted package name in
`configure.ac` establishes a package component and the `autotools` facet. A supported
literal version is retained separately. Direct `AC_PROG_CC` and `AC_PROG_CXX` markers
declare C and C++ compiler checks, respectively; they do not establish available
compilers or successful compilation. Literal `AC_CONFIG_SUBDIRS` members resolve only
to safely catalogued packages with supported `configure.ac` declarations.

Comments, m4 quotations and macro bodies are kept separate from declarations. Macro
definitions, nested macro expansion, includes and shell expressions are not evaluated.
Conditional declarations retain uncertainty. Generated `configure`, `Makefile.in`,
`aclocal.m4` and external macro files are not interpreted.
Shell `case` arm boundaries are recognized structurally without evaluating patterns or
commands. Declarations inside arms remain conditional; independent literal declarations
after `esac` remain available, including local subpackage references.

`Makefile.am` contributes literal `SUBDIRS` and `*_SOURCES` declarations, including
continuations and append assignments. Ordinary fragments do not establish boundaries.
Literal subdirectories may identify existing child packages or catalogued fragments.
Source suffix `.c` declares C source type; `.C`, `.cc`, `.cpp`, `.cxx` and `.c++` declare
C++ source type. Headers alone establish neither. Source files are not read, and source
existence, target activation, assignment precedence, conditional branches and generated,
extra or distribution-only source participation are not evaluated. Variables,
substitutions, unsafe paths and sensitive assignment values remain unresolved.

Fragment evidence belongs to the nearest existing component, including configured and
other-ecosystem components. Without an owner it remains repository-scoped with an
ownership limitation. A generic Autotools file alone does not establish C/C++ use.

## Bazel

Safely inspected `MODULE.bazel`, `REPO.bazel`, `WORKSPACE` and `WORKSPACE.bazel` markers
establish Bazel root components. `BUILD` and `BUILD.bazel` establish package boundaries,
including empty files. Literal `module(name=..., version=...)` and `workspace(name=...)`
declarations retain identity evidence. Malformed contents remain explicit partial
results without erasing known marker presence. A file alone establishes no C/C++ claim.

Packages belong to their nearest catalogued Bazel root, without crossing nested roots.
Root and package evidence at the same path share one workspace component and do not
create self-relationships. Competing `BUILD`/`BUILD.bazel`, `MODULE.bazel`/`REPO.bazel`,
or legacy workspace files retain ambiguity rather than selecting an effective file.

Supported explicit C/C++ rule names are `cc_binary`, `cc_library`, `cc_test`, `cc_import`
and `cc_shared_library`. Slygentify recognizes unshadowed legacy calls and literal
imports from `@rules_cc//cc:defs.bzl` or the corresponding per-rule `.bzl` labels,
including literal aliases. Reports identify the declared call/import spelling. Loaded
implementations, repository mappings, overrides and compilation remain unverified.
Arbitrary loads, macros, shadowed names, computed target names and deferred calls remain
unresolved. Targets never become components; source lists are not interpreted.

## Safety and canonical interfaces

Inspection preserves containment, sensitive-file, ignore, tracked-file, link and resource
guards. Parsing and retained records consume the memory budget and check the deadline.
Inputs are strict UTF-8. Unsupported or malformed syntax remains explicit; independently
retained declarations survive failures. Names, values and locators are evidence of source
declarations, not effective settings or verified execution.

Routine syntax outside this boundary-focused inspection is summarized once per owning
component and limitation category, citing a representative source. Every contributing
file retains separate evidence and initialization provenance. Specific computed values,
unsafe or missing references, conflicts and malformed inputs retain individual
diagnostics. Summaries keep repeated capability notices from displacing useful known
facts in the default bounded map.

Scan text, interactive exploration and JSON expose the same canonical records. Map
places identity and language declarations in orientation and unknowns in boundaries.
Initialization fingerprints inspected files and retains derivations; static doctor
compares component, language, version and membership evidence while preserving human
guidance. Existing Python APIs and JSON schema versions are unchanged.

See the [Meson function reference](https://mesonbuild.com/Reference-manual_functions.html),
[Autoconf manual](https://www.gnu.org/software/autoconf/manual/autoconf.html),
[Automake manual](https://www.gnu.org/software/automake/manual/automake.html) and
[Bazel package model](https://bazel.build/concepts/build-ref) for the build systems'
broader evaluation semantics.
