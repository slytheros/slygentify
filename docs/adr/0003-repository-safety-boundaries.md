# ADR 0003: Repository safety boundaries

## Status

Superseded by ADR 0005

## Context

Slygentify treats repositories as potentially hostile input. Repository inspection can
cross filesystem boundaries, disclose credentials, consume unbounded resources, or
mislead users when partial analysis is presented as complete. Executing a discovered
command adds a stronger boundary: repository-controlled code may attempt to reach the
host filesystem, credentials, network, or other workloads. Public-contribution CI adds
the same risks at runner and platform scale.

The pre-public planning item threat model in pre-public approval record assessed these risks on 2026-08-15 and
recommended a local, read-only, network-free, non-executing, metadata-first, bounded
default. It also recommended explicit authorization for additional effects and
equivalent demonstrated isolation before any supported CI platform processes untrusted
contributions. This ADR converts that recommendation into an implementation boundary.

The current `slygentify init` implementation remains narrower than this decision. It
does not provide general traversal, command execution, or the future public CI
capability described here. Downstream requirements and tests must be added when those
behaviors are implemented.

## Decision

Adopt the **Fail-closed bounded repository safety contract**.

### Default effect boundary

Core inspection and default `doctor` operation are local, read-only, network-free, and
non-executing. Content reads, repository writes, command execution, and network access
are separate effects. Authorization for one effect never authorizes another.

Repository configuration is untrusted input. It may describe repository facts and may
tighten safety limits, but it cannot authorize sensitive content reads, command
execution, network access, or weaker isolation.

### Filesystem traversal

Resolve the user-selected repository root once, show the resulting root to the user,
and hold or revalidate an equivalent stable filesystem identity during inspection.
Every candidate entry must be proven to remain within that root before it is read.
Descendant symbolic links are reported as links and are never followed.

Traversal uses `lstat`-equivalent metadata inspection and reads only regular files.
Devices, FIFOs, sockets, unreadable entries, entries whose identity changes before
open, and paths whose containment cannot be proven are skipped with concise evidence.
Archive contents are not expanded during default traversal. A skip is not an error by
itself, but it makes the result partial whenever the skipped scope could affect a
requested conclusion.

The default inspection budget is:

| Resource | Default | Counting rule |
| --- | ---: | --- |
| Maximum depth | 64 | The selected root is depth 0. |
| Entries examined | 100,000 | Count every directory entry considered, including skipped entries. |
| Bytes per file | 4 MiB | Count raw bytes made available for inspection or parsing. |
| Aggregate bytes read | 256 MiB | Sum raw bytes read across the operation. |
| Elapsed time | 60 seconds | Measure with a monotonic clock. |
| Concurrent open files | 64 | Count repository file handles held concurrently. |

All effective values must be positive and representable on the host platform.
Repository-controlled configuration may only lower them. A trusted, explicit
invocation may raise individual limits without a product-defined ceiling; the
operation must display and record the effective overrides before work begins. Host and
operating-system limits still apply and may cause an earlier partial result.

Reaching a limit stops the affected work safely and returns a deterministic partial
result that names the limit, its effective value, the consumed amount, and the known
skipped scope. Partial results never claim exhaustive inspection.

### Sensitive content

Default traversal may report safe metadata, such as the existence and entry kind, but
must not read content from:

- VCS internals, including `.git` entries and nested repository metadata;
- environment files such as `.env` and `.env.*`;
- private-key and certificate containers such as `*.key`, `*.pem`, `*.p12`, and
  `*.pfx`;
- authentication files such as `.netrc`, `.npmrc`, `.pypirc`, and private SSH keys;
  or
- common SSH, cloud, Kubernetes, and package-manager credential stores.

Downstream research may extend this conservative list but may not silently weaken it.
Because filenames and patterns cannot identify every credential, normal diagnostics,
logs, generated artifacts, and structured output must not reproduce raw inspected
content. Errors identify the operation and safe relative target without including
values or host-private paths.

A trusted user may authorize content access to one exact in-root path for one
invocation. The interface must show the path and effect before access. Patterns,
directory-wide consent, repository configuration, and consent retained for a later
invocation do not satisfy this requirement. The override permits analysis only; it
does not permit raw content disclosure or transmission.

### Authorized command execution

Public 1.0 may provide a separate, explicit `doctor` verification capability for
repository commands under this contract. pre-public planning item defines its final CLI, JSON, Python,
diagnostic, and exit behavior, but may not weaken these controls.

Each execution requires the user to select the exact command for that invocation.
Authorization is bound to the reviewed argument vector, command evidence, component
working directory, and repository snapshot. If any bound input changes before launch,
execution stops and requires new authorization. Slygentify invokes the argument vector
directly without adding a shell. A selected tool may itself use a shell inside the
sandbox; that does not extend the sandbox boundary.

The command runs in a disposable writable copy containing only approved, in-root
inputs. Excluded sensitive content is absent unless each required path received its
own content authorization. The real repository, other host filesystems, host
credentials, inherited sensitive environment variables, and interactive standard
input are unavailable. Results and artifacts are not copied back automatically.

The sandbox defaults are:

| Resource | Default |
| --- | ---: |
| Elapsed time | 5 minutes |
| CPU allocation | 2 CPUs |
| Memory | 2 GiB |
| Process count | 64 |
| Writable storage | 2 GiB |
| Captured standard output | 1 MiB |
| Captured standard error | 1 MiB |

Trusted execution overrides must be explicit, visible, and included in the result.
The complete process tree is terminated on timeout. Captured output is bounded,
redaction-safe, and marked when truncated. The disposable workspace is removed after
result collection, including after failure.

Network access is denied by default. Enabling it requires a separate authorization for
the same command invocation and never grants credentials or relaxes filesystem,
process, or resource isolation. If an available backend cannot enforce filesystem,
credential, process, resource, cleanup, and network controls, command execution is
unavailable and fails with an actionable diagnostic. A warning or timeout without
technical isolation is not an acceptable fallback.

### Untrusted contribution CI

Gitea Actions, GitHub Actions, and GitLab CI/CD share one invariant: an untrusted
contribution must not receive secrets, protected credentials, write-capable tokens,
protected runners, privileged host or container access, or reusable trusted
workspaces.

Before a platform processes an untrusted contribution, maintainers must record dated,
inspectable evidence for:

- pull or merge request trigger, token, and secret semantics;
- disposable runner lifecycle, executor, host mounts, and cleanup;
- absence of privileged mode, host sockets, and protected runner access;
- cache and artifact namespaces separated by trust level;
- explicit egress rules restricted to documented dependency and advisory endpoints;
  and
- protected human review of workflow changes.

Untrusted and privileged release jobs remain separate. A trusted job must not consume
an untrusted cache, workspace, or artifact without independently verified provenance.
Repository-controlled dependency metadata must not silently broaden egress. Public or
fork-based CI remains disabled or human-gated until the platform-specific evidence
demonstrates this invariant.

pre-public planning item implements and verifies the current Gitea controls. GitHub and GitLab require
equivalent evidence before later enablement. Applying the common contract to all three
platforms is not a claim that Slygentify currently integrates with them.

### Verification contract

Downstream implementations must demonstrate:

- out-of-root and cyclic links, special entries, changed identities, and unreadable
  paths do not escape, block, or make results appear complete;
- every inspection limit produces deterministic bounded partial results;
- sensitive names and representative values do not appear in ordinary output, logs,
  generated artifacts, or uploads;
- default inspection launches no process and performs no network request;
- authorization is invalidated by a changed command or repository snapshot;
- sandbox fixtures cannot reach host files, credentials, persistent workspaces, or the
  network unless network was separately authorized;
- time, process, memory, storage, and output bounds fail closed; and
- each enabled CI platform passes credential, runner, cache, artifact, workflow-change,
  and egress abuse scenarios.

Observable behavior receives Doorstop requirements, test specifications, and source
links when implemented. This decision does not create claims that those capabilities
already exist.

## Consequences

The default workflow remains useful without trusting repository code. Safe skips and
partial results are visible rather than silently reducing coverage. Trusted users may
raise inspection budgets for large repositories, but doing so can still exhaust host
resources because the product imposes no hard ceiling.

Sensitive exact-path consent supports unusual layouts without allowing a repository to
grant itself broad access. It adds interaction and automation complexity and cannot
guarantee discovery of every secret.

Command verification is an optional capability, not a portable promise that every
installation can execute commands. Fail-closed isolation can require an external or
platform-specific backend and may make execution unavailable. Disposable copies add
time and storage cost, while the prohibition on automatic write-back keeps verification
separate from repository mutation.

The common CI contract reduces platform drift but requires separate deployment
evidence. Existing workflow configuration alone is insufficient evidence of runner,
token, cache, artifact, or egress isolation.

Before 1.0, implementation interfaces may change subject to the accepted product
contract. At 1.0, pre-public planning item and pre-public planning item define compatibility for the public Python, JSON, and
CLI surfaces. The same safety contract applies to public and private repositories;
private status is not treated as a security control. This public decision creates no
commitment concerning proprietary capabilities.

## Residual risks

Residual risks include operating-system, sandbox, runner, or CI-provider compromise;
filesystem races not eliminated by the chosen platform primitives; credentials not
recognized by exclusions; user-selected limits that exhaust the host; parser defects;
explicitly authorized network misuse; malicious output that evades redaction; and any
content or execution effect the user deliberately authorizes. These risks must remain
visible in implementation guidance and cannot be represented as eliminated.

## Alternatives considered

### Fixed budgets or product hard ceilings

Fixed budgets are simpler and more reproducible, while hard ceilings bound even trusted
requests. Both were rejected because they would make legitimate large-repository work
impossible. Repository input still cannot raise defaults; only a visible trusted
invocation can do so.

### Broad sensitive-path overrides

Pattern or directory consent is more convenient but can authorize unrelated credentials
added later. It was rejected in favor of exact-path, single-invocation consent.

### Direct or warning-only host execution

Running in the real repository offers maximum tool compatibility. Timeouts, reduced
environments, and warnings do not prevent malicious code from accessing the host or
network, so this alternative was rejected.

### Persistent or repository-controlled execution consent

Stored consent helps automation but can outlive the reviewed command or be modified by
an attacker. Execution therefore requires a fresh selection bound to the current
command and snapshot.

### Network access by default during execution

Default network access helps tools fetch missing dependencies but creates an immediate
exfiltration channel. It was rejected in favor of a separate authorization enforced by
the sandbox.

### A Gitea-only CI contract

A current-platform-only policy would be smaller but would repeat the security decision
during the planned GitHub migration and could leave GitLab behavior inconsistent. A
common invariant with platform-specific evidence was selected instead.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
