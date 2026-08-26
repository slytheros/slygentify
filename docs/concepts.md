# Concepts

Slygentify turns bounded static repository evidence into an operating map. It does not
guess that common conventions are true and does not execute commands it discovers.

## Claims and evidence

Every conclusion retains one of four meanings:

| Classification | Meaning |
| --- | --- |
| Verified | Directly supported by inspectable repository evidence. |
| Inferred | Derived from evidence but not directly confirmed. |
| Recommended | Optional advice, not a repository fact. |
| Unknown | Missing or unconfirmed information. |

Evidence records identify repository-relative locations and concise observations. They
do not expose the selected repository's host-absolute path.

## Repositories and components

A scan selects the nearest containing Git repository. Components are evidence-backed
development units such as a root project, application, library, or workspace member.
A component can retain multiple ecosystem facets and explicit directed relationships.
Directory names alone do not establish a component.

## Complete and partial results

`complete` means inspection finished within the supported boundary. It does not prove
that every possible repository fact is known. `partial` means inspection succeeded but
some scope or evidence was omitted or limited. Diagnostics and skipped scopes describe
that boundary.

## Human and machine interfaces

Human-readable output is designed for people and may evolve. Automation selects
canonical versioned JSON or uses stable exit semantics. The package version, scan schema,
map schema, doctor schema, and initialization-state schema are related but independently
versioned compatibility surfaces.
