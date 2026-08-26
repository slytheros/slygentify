# ADR 0011: Public migration, release, and support policy

## Status

Accepted

## Context

Public 1.0 requires a sanitized GitHub repository with green public CI, verified wheel
and source-distribution publication on PyPI, artifact provenance, and fresh-environment
installation checks under ADR 0002. ADR 0001 defines the current Gitea branch and
release governance, while ADR 0004 defines the stable Python and JSON compatibility
surface. The public cutover must extend those decisions without exposing private
planning history or weakening their human approval boundaries.

pre-public planning item recorded dated research in pre-public approval record. The current private repository has
collaboration records and rationale that are not suitable for public disclosure, and
ordinary GitHub import paths cannot preserve Gitea issue authorship, numbering, and
chronology faithfully. The research also found no existing tags or releases to migrate.
PyPI Trusted Publishing can replace a long-lived package credential with an exact
GitHub OIDC identity, but it does not remove the need to protect the publishing
workflow, release tags, environment, and human release authority.

The `slygentify` distribution name appeared unregistered on PyPI when checked on
2026-08-21, but that observation is not a reservation. Windows and macOS behavior is
also unverified until the proposed public matrix passes. These and the irreversible
nature of public disclosure require an explicit cutover and recovery policy before
dependent public-release work begins.

## Decision

Approve a **sanitized single-root GitHub snapshot, trusted PyPI publication, and
tested-matrix latest-1.x support**.

### Public repository boundary and cutover

- Freeze a reviewed Gitea `develop` commit and export only its tracked tree into a
  separate staging area. Remove `.gitea` and apply only an explicit, reviewable
  sanitization manifest.
- Reject the export if it contains internal hostnames, private issue or pull-request
  identifiers or backlinks, commercial rationale, secrets, unexpected binaries, or
  files outside the approved public allowlist.
- Create `https://github.com/slytheros/slygentify` with one root commit. Point `main`
  and `develop` at that commit and make `develop` the default branch.
- Do not migrate or reproduce private commit history, issues, pull requests, comments,
  numbering, chronology, tags, or releases. Create only newly authored, public-safe
  issues and milestones without private backlinks or mappings.
- Preserve the private Gitea repository unchanged and read-only as the authoritative
  pre-public archive. The private EE repository and its roadmap remain on Gitea unless
  a separate decision explicitly changes that boundary.
- Keep the frozen source commit, archive digest, sanitization evidence, and
  transformation manifest private. Public content must not reveal the private-to-public
  mapping.

Before public announcement, a failed cutover returns authority to unchanged Gitea and
discards the unpublished GitHub attempt. After public announcement or any PyPI
publication, recovery proceeds forward: never rewrite public history or tags and never
reuse a published version or filename. Yank a defective but intact release with a
reason and publish a correction. A partial PyPI upload may be completed only by a
separately approved recovery run that verifies every existing digest and attestation,
offers only the missing file, and fails on any mismatch. Delete public artifacts only
for exceptional legal, malware, or secret-exposure cases.

### GitHub governance and continuous integration

- Protect `main`, `develop`, and release tags with rulesets that require pull requests,
  current successful checks, merge commits, and blocked direct pushes, force pushes,
  and protected-ref deletion.
- Keep required approvals at zero while there is only one human maintainer. The
  maintainer records a complete self-review and personally merges. Require a distinct
  human approval when another maintainer is regularly available.
- Agent credentials may create working branches and pull requests but receive no
  protected-branch approval, merge, tag, environment-approval, release, or security-
  disclosure authority.
- Use full-commit-SHA action pins and read-only default workflow permissions. Do not use
  `pull_request_target`, write-capable default tokens, repository secrets in untrusted
  jobs, privileged containers, or self-hosted runners.
- Require approval before every external contributor's workflow run. Review workflow,
  action-pin, lockfile, build-system, and packaging changes before granting it.
- Require these 12 continuously supported test contexts:
  - Ubuntu 24.04 x64 with CPython 3.11, 3.12, 3.13, and 3.14;
  - Windows 2025 x64 with CPython 3.11, 3.12, 3.13, and 3.14; and
  - macOS 15 arm64 with CPython 3.11, 3.12, 3.13, and 3.14.

These exact runner and interpreter combinations define the supported public platform
matrix. Other environments may work but are not promised. CPython 3.11 remains in the
matrix throughout public 1.x. A final CPython 3.15 release may be added in a compatible
1.x release only after the complete matrix and artifact-installation checks pass.

### Package and release process

- Keep the distribution, import, and command name `slygentify`. Use SPDX package
  metadata for Apache-2.0 and publish truthful project URLs and classifiers.
- Apply Semantic Versioning after 1.0: patch releases carry fixes and security updates,
  minor releases carry compatible additions and deprecations, and major releases carry
  breaking package changes.
- Tag protected `main` releases with SemVer tags. Validate
  `v1.0.0-rc.1` against package version `1.0.0rc1` and `v1.0.0` against `1.0.0`, and
  reject every tag/version mismatch.
- Claim the PyPI name only with the first genuine `1.0.0rc1`; never upload a placeholder
  or reservation artifact.
- Configure a pending PyPI Trusted Publisher for owner `slytheros`, repository
  `slygentify`, workflow `release.yml`, and environment `pypi`. Store no long-lived PyPI
  token.
- Build exactly one source distribution and one `py3-none-any` wheel from the protected
  tag. Check metadata and archives, then install those exact immutable files in every
  supported matrix context.
- Pass the same files through a full-SHA-pinned GitHub artifact-attestation step and a
  separate minimal Ubuntu publish job using a full-SHA-pinned PyPI publishing action.
  Grant elevated `id-token: write` only to the jobs that require OIDC or attestation.
- Protect the `pypi` environment with selected `v*` tags, required reviewer
  `slytheros`, self-approval during the solo-maintainer phase, and disabled administrator
  bypass. An authorized human retains tag, environment-approval, and GitHub-release
  publication authority.
- After upload, verify the two expected filenames and hashes, the Trusted Publisher
  identity, PyPI provenance, and fresh installations before the human publishes the
  GitHub release containing the exact files and checksums.
- Rehearse the complete process on TestPyPI with a distinct workflow filename,
  environment, and publisher identity so the production trust path cannot be exercised
  accidentally.

### Maintenance, security, and deprecation

- Support only the latest public 1.x release unless the maintainer explicitly opens a
  maintenance branch. Patch and security maintenance are best-effort, with no guaranteed
  response or remediation service-level agreement.
- Enable private vulnerability reporting. Coordinate embargoed fixes through draft
  security advisories and request a CVE when warranted. An authorized human controls
  disclosure and publication; agents may prepare evidence and fixes but may not approve,
  disclose, merge, tag, or publish them.
- Preserve ADR 0004's compatibility contract. Deprecated public Python names remain
  available through 1.x and emit `DeprecationWarning`. Deprecated JSON fields remain
  accepted throughout schema major 1, while producers emit only the documented
  replacement after its transition.
- Announce a documented replacement before deprecation. Remove a deprecated package
  interface only in the next package major, and remove a wire interface only in the
  next applicable schema major with an explicit reader or migration path and a migration
  guide. Package and wire major versions remain independent.

## Consequences

The single-root export gives the public project a clear provenance and disclosure
boundary, but users receive no public pre-cutover engineering history or historical
collaboration records. The private archive remains necessary to interpret that history.
Public history and published package files become effectively irreversible after
cutover.

Trusted Publishing and attestations reduce long-lived credential and artifact-identity
risk, but they concentrate release authority in the GitHub account, protected workflow,
tags, and environment. A solo maintainer remains an account-recovery and separation-of-
duties risk. Standard hosted runners also retain unrestricted public-network egress.

The 12-job matrix creates a concrete, continuously verifiable support boundary and
increases CI time and maintenance cost. Windows and macOS support remains unverified
until the first complete matrix passes. Supporting CPython 3.11 for all of 1.x may
eventually constrain dependencies and extend maintenance work.

Latest-only, best-effort maintenance bounds the solo maintainer's backport and response
obligations, but users receive no guaranteed fix timeline or older-minor security window.
ADR 0004's long deprecation period protects 1.x consumers at the cost of retaining
compatibility paths until the next major.

The PyPI name may be claimed before the genuine release candidate. Exact action SHAs,
ruleset identifiers, environment configuration, filenames, hashes, and dry-run evidence
remain implementation-time facts and must be recorded by dependent work.

This ADR changes repository and release policy only. It does not immediately change a
runtime API, wire schema, supported command, or package artifact.

## Alternatives considered

### Mirror, importer, or rewritten private history

A direct mirror or importer would retain more source history, and a rewrite could try to
remove selected private content. These approaches were rejected because the repository
history itself contains non-public planning context and a missed reference would become
an irreversible disclosure.

### Selective historical issue migration

Recreating selected issues would provide more public context. It was rejected because
ordinary migration cannot preserve original authorship, numbering, timestamps, or
chronology faithfully and could disclose private mappings. New public-safe issues are
less misleading.

### Gitea-only or GitHub-source-only 1.0

Either would reduce cutover work. Both were rejected because ADR 0002 requires the
sanitized GitHub repository, public CI, verified PyPI artifacts, provenance, and fresh
supported-environment installations for public 1.0.

### Placeholder upload or long-lived PyPI credential

A placeholder could attempt to reserve the name, and a token or manual upload could be
simpler initially. They were rejected because placeholder artifacts are not genuine
releases and published filenames cannot be reused, while a long-lived credential creates
an avoidable secret and weaker publisher identity.

### Broader platform support

Claiming Linux, Windows, and macOS generally, or promising only interpreter versions,
would offer a wider compatibility message. It was rejected because the exact hosted
runner matrix is the boundary that the project can continuously and reproducibly verify.

### Routine older-minor or long-term maintenance

Maintaining the previous minor or a fixed support window would give users a longer
upgrade period. It was rejected for public 1.x because it imposes parallel backport,
testing, release, and security-response work that the solo maintainer cannot currently
promise.

### Removal before the next applicable major

Removing a deprecated interface after a time window would reduce compatibility code.
It was rejected because ADR 0004 already commits supported Python names through 1.x and
supported JSON fields through schema major 1.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
