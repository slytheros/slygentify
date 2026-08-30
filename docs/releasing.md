# Human-gated release process

This is the maintainer runbook for preparing, rehearsing, publishing, verifying, and
recovering a Slygentify package release. Slygentify has not yet published a public PyPI
release. Do not use this procedure to reserve the name or publish a placeholder.

Publication has four distinct human boundaries: a reviewed promotion from `develop` to
`main`, creation of an immutable release tag, approval of the selected package-index
environment, and publication of the GitHub release. Automation prepares and verifies
evidence; it does not perform any of those human decisions.

## Prepare the release

1. Work from a ready release-candidate issue. Update the private version source and move
   the intended entries from `Unreleased` into a dated changelog section.
2. Run every locked local check in `CONTRIBUTING.md`. Review the wheel, source distribution,
   metadata, documentation, compatibility impact, security impact, and known limitations.
3. Merge the reviewed feature work into `develop`, then open and review a release-promotion
   pull request from `develop` to `main`. Use a merge commit; do not push directly.
4. Confirm all required checks are current on the exact `main` commit. Create either
   `vX.Y.Z-rc.N` for package version `X.Y.ZrcN` or `vX.Y.Z` for package version `X.Y.Z`.
   Never move, delete, or reuse a release tag.

The production workflow starts on the tag but waits at the protected `pypi` environment.
Do not approve it yet.

## Rehearse on TestPyPI

Dispatch `release-testpypi.yml` against the tag itself, not a branch:

```console
gh workflow run release-testpypi.yml --ref vX.Y.Z-rc.N -f mode=publish
```

Review the workflow before approving `testpypi`. The run must build exactly one wheel and
one source distribution, attest them, install each local file in all 12 supported contexts,
verify TestPyPI filenames, hashes, and Trusted Publisher provenance, and install the
published version through both `uv tool` and `pipx` in 24 fresh installer contexts. Each
tool manager receives the exact hash-bound wheel URL advertised by TestPyPI after its
filename, size, and digest match the release manifest. PyPI is then the sole dependency
index; TestPyPI never participates in dependency resolution. Record the run URL and the
`release-manifest.json` and `SHA256SUMS` evidence for the release-candidate issue.

TestPyPI and PyPI have separate accounts, pending publisher records, workflow filenames,
and GitHub environments. A successful rehearsal cannot authorize production publication.

## Publish to PyPI

After the release-candidate evidence and human go/no-go review are complete, inspect the
pending production run and approve the `pypi` environment. The publish job receives only a
short-lived OIDC credential and uploads only the files admitted by the preflight job. No
PyPI token belongs in repository, organization, or environment secrets.

Wait for every post-publication job. Verify that PyPI exposes exactly the two manifest
filenames and SHA-256 hashes, that both provenance objects identify
`https://github.com/slytheros/slygentify`, and that the 24-context `uv tool`/`pipx`
fresh-install matrix passes.

An authorized human may then create the GitHub release for the existing tag and attach the
exact wheel, source distribution, and `SHA256SUMS` from the workflow artifact. Include the
verified workflow run, supported matrix, known limitations, and migration notes. Do not
rebuild or substitute files locally.

## Retry and partial recovery

- If a transient failure occurs before any upload, rerun the same workflow run. If source,
  version, or packaging must change, prepare a new version and new tag instead.
- If both matching files reached the index but later verification failed transiently,
  dispatch `release.yml` against the tag with `mode=verify-only`. It uploads nothing.
- If exactly one file reached the index, obtain a separate human approval and dispatch the
  tag with `mode=recover-partial`. The preflight rebuilds the immutable pair, verifies the
  existing digest, and stages only the missing file. Any unexpected filename or digest
  mismatch stops the run. `skip-existing` is intentionally not used.
- If a defective but intact release is public, yank it with a reason and publish a fixed
  version. Published versions, filenames, tags, and public history are never rewritten.

## Suspected compromise

Stop release approvals and disable the affected GitHub environment and PyPI Trusted
Publisher. Preserve workflow logs, manifests, checksums, provenance, audit events, and
the suspect artifacts. Revoke affected GitHub sessions or credentials, review changes to
workflows, action pins, rulesets, environments, package ownership, and publisher identity,
and coordinate the investigation through GitHub private vulnerability reporting or a
draft security advisory.

Yank an intact affected release with a reason and recover forward with a reviewed new
version. Request deletion only for exceptional legal, malware, or secret-exposure cases;
do not delete evidence as routine rollback. An authorized human controls disclosure,
tagging, environment approval, PyPI actions, and GitHub release publication.
