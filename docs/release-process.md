# Post-1.0 release process design

This design replaces an operator-orchestrated release with a repository-owned checklist.
This document is the design record to review alongside proposed ADR 0014; it is not an
authorization to publish a package or alter a protected ref.

## Current process and evidence budget

| Stage | Producer and consumer | Authoritative evidence | Retention / failure handling |
| --- | --- | --- | --- |
| Candidate preparation | Maintainer; promotion reviewer | version source, changelog, PR checks | Git history; a changed candidate needs a new review |
| Local quality and corpus checks | Maintainer; corpus reviewer | external sanitized reports and review matrices | local only; rerun from pinned corpus |
| Promotion and tag | Human; tag workflow | merged `main` SHA and annotated tag | Git; tags are never moved or reused |
| TestPyPI / PyPI | GitHub workflow; environment reviewer | immutable workflow bundle, manifest, checksums, attestations | workflow artifact retention; registry is immutable |
| Public release | Human; users | existing tag and workflow-produced assets | GitHub Release; repair forward or yank |

Raw scans, generated guidance, local corpus paths, corpus trees, and multi-megabyte
matrices have no permanent repository consumer. They remain local or bounded workflow
artifacts. The permanent release record is a compact manifest: immutable inputs,
tool versions, aggregate outcomes, evidence hashes, workflow URLs, reviewer decisions,
and known limitations. A fact is copied only when its destination has a named consumer.

## Future lifecycle

1. A ready issue selects a version and release delta; the maintainer freezes `develop`
   and cuts a temporary release branch.
2. The checklist runs reusable local checks and the formal, initialization,
   supplemental, and scaling corpus phases. Each phase emits canonical external evidence.
3. The maintainer reviews only bounded semantic packets, recording a decision and packet
   digest in the release issue. The runner reads, never writes, that record.
4. A reviewed release branch is merged to `main`; the runner verifies the exact merge
   SHA. A human creates and dereferences an annotated tag, then back-merges `main` to
   `develop` and deletes the release branch.
5. Hosted workflows remain authoritative for package builds, attestations, environment
   approval, TestPyPI/PyPI verification, and the supported install matrix. TestPyPI is
   never a dependency index; the candidate wheel is hash-bound and dependencies resolve
   from PyPI only.
6. Failed candidates consume their tag/version, are back-merged, and are replaced by a
   fix through `develop`. Partial publication follows the existing one-missing-file
   recovery workflow. A failed post-publication verification is an incident, not
   permission to rebuild or overwrite immutable artifacts.

## Human gates

Each packet states one permitted decision, the evidence digest, acceptance/rejection
criteria, recovery, and the exact resume command. The maintainer records a GitHub issue
comment containing the gate name, packet digest, decision, identity, date, and notes.
The runner must verify that comment in an explicit forge phase before accepting the next
dependent phase. It never merges a PR, tags, approves `testpypi` or `pypi`, uploads,
or creates a GitHub Release.
