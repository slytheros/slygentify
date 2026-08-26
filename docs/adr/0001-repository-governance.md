# ADR 0001: Repository governance

## Status

Superseded by ADR 0011 for the public repository

## Context

Slygentify accepts contributions authored with assistance from coding agents and is
expected to grow from a solo-maintainer project into a small team. The repository
needs a reviewable integration path, enforceable continuous-integration checks, and
a release policy without adding release-process overhead that the project does not
yet need.

The repository currently has protected `develop` and `main` branches and Gitea
Actions jobs that validate code quality, the supported Python versions, packaging,
and locked dependencies. Governance must make these checks mandatory before a
protected branch changes and preserve a human decision at the merge gate.

## Decision

### Branches and pull requests

- `develop` is the protected integration branch. Short-lived `feature/`, `fix/`,
  `docs/`, and `chore/` branches target it through pull requests.
- `main` is the protected release branch. Releases are promoted through a
  `develop`-to-`main` pull request; it is not a direct feature target.
- Every pull request is merged with a merge commit. Rebase, squash, and direct
  merges to protected branches are not part of the normal workflow.
- Force pushes and direct pushes to `develop` and `main` are prohibited. Authors
  may force-push an unprotected working branch when needed; any changed pull-request
  head invalidates the prior review.
- Delete the remote source branch after merge. Keep protected branches and active
  investigation branches; contributors remove their local branches when finished.

### Review and required checks

Gitea branch protection for both protected branches requires these successful status
checks on the current pull-request head:

- `Code quality`
- `Tests (Python 3.11)`
- `Tests (Python 3.12)`
- `Tests (Python 3.13)`
- `Tests (Python 3.14)`
- `Package`
- `Dependency vulnerabilities`

Both branch rules block rejected or outstanding official reviews, dismiss stale
approvals after a changed pull-request head, and require the head branch to be up to
date with its base before merge. Gitea administrators must follow these rules. Merge
permissions remain limited to authorized human maintainers.

When a distinct human reviewer is available, their approval is required before merge.
When no second human is available, the sole maintainer reviews the complete pull
request and its successful checks, leaves a `Human self-review:` acknowledgement
describing that review, and personally performs the merge. Gitea's required-approval
setting remains zero in this shared-account phase because it cannot apply a
different rule to a sole maintainer without also blocking that maintainer's pull
request. Agents do not approve, merge, or close governance gates.

### Versioning and releases

- Tag releases from `main` as `v0.x.y` using Semantic Versioning.
- Before `1.0.0`, version identifiers communicate release scope only; they make no
  backward-compatibility commitment. Use prerelease suffixes such as `-rc.1` only
  for release candidates.
- Do not create release branches by default. Create `release/x.y` only after a
  published minor line requires supported maintenance while newer development
  continues.
- Draft release notes from labeled pull requests merged since the previous `v*`
  tag. Valid release-note labels are `breaking`, `feature`, `fix`, `security`,
  `documentation`, and `internal`; unlabeled pull requests are excluded.
- An authorized human reviews, edits as needed, and publishes generated release
  notes. This review removes private details, secrets, and prematurely disclosed
  security information before a public release.

## Consequences

The two-stage model makes `main` a clearly identifiable release history and adds a
second, intentional review and CI pass for each promotion. It also adds a promotion
pull request and keeps two protected-branch configurations synchronized.

This decision keeps release branches out of the normal path, avoiding backport and
stabilization overhead until supporting an older public release requires it. Merge
commits retain the relationship between each change branch and each release
promotion, at the cost of a less linear history.

The shared Gitea maintainer account is a procedural, not technical, boundary: an
agent with that account's credentials could act as the maintainer. Until the project
moves to public GitHub, instructions and human operation prohibit that behavior.
At migration, create a restricted agent account that may create working branches and
pull requests but is excluded from protected-branch approval and merge permissions;
then require one approval from a distinct human account in Gitea.

For private releases, release notes remain internal and the same human review checks
for confidential details. For public releases, the review is also a publication gate;
the release owner must omit non-public security details and any information that was
not intended for public disclosure.

## Alternatives considered

### Protected `main` only

A single protected branch would reduce promotion overhead. It was rejected because
the project wants an explicit release-ready branch and a distinct integration path.

### Release branches for every version

Release branches would support parallel stabilization. They were rejected as
premature because there is no supported older release line and the associated
backport process would add unnecessary work.

### Squash or rebase merges

Squash merges would make the history shorter, and rebase merges would make it
linear. Both were rejected in favor of merge commits because preserving pull-request
and `develop`-to-`main` promotion topology is more useful to this project.

### Mandatory second-human review for every pull request

This would provide stronger separation of duties, but it would block the current
solo maintainer. The documented self-review rule retains a human check while keeping
CI mandatory; it is revisited when a second maintainer is regularly available.
