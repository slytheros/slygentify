# Slygentify documentation

Slygentify `1.0.0rc1` is a public 1.0 release candidate for building bounded,
evidence-backed repository operating maps and safe agent guidance. It implements `init`,
`scan`, `map`, and static `doctor`. It is not yet published on production PyPI.

## Start here

1. [Install from a reviewed source checkout](installation.md).
2. [Explore your first repository](tutorials/first-repository.md) with a safe own-repository
   path or the deterministic CLI and Python fixture walkthrough.
3. Learn the [claim vocabulary and repository model](concepts.md).
4. Review the [local-first safety boundary](safety.md) before selecting a nonstandard Git
   executable or applying generated guidance.

## Task guides

- [Scan a repository](guides/scan.md) for a complete human or canonical JSON report.
- [Initialize guidance](guides/init.md) after reviewing an exact dry-run.
- [Map task context](guides/map.md) as bounded canonical JSON.
- [Assess managed knowledge](guides/doctor.md) for people or automation.
- [Troubleshoot safe recovery](guides/troubleshooting.md) when inspection is partial or a
  managed-artifact action is refused.

## Reference

- [CLI reference](cli.md)
- [Python API reference](api.md)
- [Scan Python API](api/scan.md)
- [Doctor Python API](api/doctor.md)
- [Map Python API](api/map.md)
- [Initialization Python API](api/initialization.md)
- [JSON Schema reference](schemas.md)
- [`slygentify.toml` and generated state](configuration-and-provenance.md)
- [Mixed repository composition](mixed-repositories.md)
- [Python inspection](python-inspection.md)
- [JavaScript and TypeScript inspection](javascript-inspection.md)
- [Inspection accounting](inspection-accounting.md)

The public site deliberately excludes internal acceptance evidence and architecture
decision history. Those tracked maintainer records are not user capability claims.

## Project policies

The repository root contains the changelog, support and security policies, contributor
guide, Apache-2.0 license, and project overview. See the
[maintainer release process](releasing.md) for human-gated publication and recovery, and
[migration guidance](migration.md) for package and JSON compatibility rules.
