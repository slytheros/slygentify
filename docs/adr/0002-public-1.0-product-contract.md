# ADR 0002: Public 1.0 product contract and success criteria

## Status

Accepted — approved in pre-public approval record on 2026-08-15 and merged through pre-public approval record;
command surface amended by ADR 0009

## Context

Slygentify currently provides a conservative `slygentify init` vertical slice. It
does not yet provide repository scanning, richer ecosystem inspection, a doctor
command, stable machine-readable interfaces, or a public 1.0 release. Those
capabilities remain proposed until their dependent decisions, requirements, and
implementations are completed.

The public 1.0 product contract needs to identify a concrete user and outcome, bound
the capabilities that Slygentify will claim, and define evidence-based release gates.
The contract must preserve the distinctions between verified facts, inferences,
recommendations, and unknowns established by the interaction-design contract.

pre-public planning item recorded its research findings and supplied the named acceptance repositories.
An authorized human approved the contract in pre-public approval record on 2026-08-15, and the
ADR was merged through pre-public approval record. The contract authorizes the target product boundary;
it does not claim that unimplemented capabilities already exist.

An interim research synthesis provides directional evidence rather than statistical
validation. Its working hypothesis is that small teams already using coding agents
will adopt a local tool when it reduces repeated orientation mistakes and review
burden through concise, verified, task-relevant guidance without imposing a new
documentation system or sending repository data away. The associated questionnaire
may remain open to refine priorities and language; the product-contract decision does
not require statistical certainty from that convenience sample.

## Decision

Approve the **Full trustworthy agent-readiness 1.0 contract**.

### User, context, and outcome

The primary user is a maintainer in a small software team working in an existing
repository. The repository may contain one or more Python, JavaScript/TypeScript, or
unsupported components. The maintainer wants coding agents to receive accurate,
reviewable repository guidance without first adopting a hosted service or allowing
Slygentify to execute repository code by default.

The intended outcome is a trustworthy local workflow that:

1. inspects repository evidence and exposes its complete normalized model through
   `scan`;
2. selects bounded task-scoped operating context through `map`;
3. bootstraps concise, component-aware agent guidance through `init`; and
4. diagnoses stale, contradictory, incomplete, or unsafe operating knowledge through
   `doctor`.

The operating map emphasizes the choices an agent repeatedly needs to get right:
which project systems are authoritative, how code should be navigated, which commands
form the validation gates, and which systems, data, or paths are outside the permitted
scope. It preserves maintainer corrections for facts that repository inspection alone
cannot establish reliably.

Success means that supported facts are reported with inspectable evidence, ambiguous
or unsupported facts remain visibly uncertain, outputs are deterministic, default
inspection has no undeclared effects, and the public release satisfies the acceptance
and publication gates below. On designated representative tasks, a fresh agent using
the operating map must select the correct authoritative system, navigation path,
validation commands, and access boundaries and must identify the evidence for those
choices.

### Supported capability boundary

- Discover repository and component boundaries only from inspectable evidence.
  Conventional-looking directories without sufficient evidence are not silently
  promoted to components.
- Represent ambiguous or unsupported areas as unknown instead of guessing.
- Preserve explicit maintainer corrections and authority declarations with provenance.
  A declaration may be verified as the maintainer's recorded instruction without
  implying that its operational effect has been independently verified.
- Provide generic multi-component inspection and first-class Python and
  JavaScript/TypeScript inspection.
- For the first-class ecosystems, cover the approved matrix of manifests, package
  managers, runtime constraints, workspaces, declared commands, common tooling, and
  framework evidence. The acceptance corpus must exercise at least two approved
  Python frameworks and two approved JavaScript/TypeScript frameworks. pre-public planning item,
  pre-public planning item, and pre-public planning item select the exact public capability matrix.
- Keep `scan` local, read-only, network-free, and non-executing.
- Keep `doctor` read-only and non-executing by default. ADR 0003 authorizes a
  separate, explicit verification mode only within its fail-closed sandbox and
  consent boundaries. pre-public planning item defines the diagnostic and public interface contract.
- Keep repository writes within explicitly mutating operations. `init` must provide
  a review path and preserve user-owned content under the ownership and regeneration
  policy selected by pre-public planning item.
- When a traversal limit prevents a complete result, return an explicit, inspectable
  partial result with skipped or unknown scope. Do not hang or silently truncate.

The exact model types, command options, JSON schemas, Python entry points, framework
list, and diagnostic codes remain subject to their existing downstream research and
human decision gates. ADR 0003 defines the traversal and authorized-execution safety
boundaries those interfaces must preserve.

### Public interface and compatibility boundary

At 1.0, the supported machine contracts are:

- CLI command names, documented options, and exit semantics;
- explicitly selected, versioned JSON documents; and
- named public Python entry points and types.

The selected contracts remain compatible through the 1.x line under the versioning
and deprecation rules approved by pre-public planning item and pre-public planning item. Human-readable wording, layout,
and styling are not machine interfaces and may improve without a compatibility
release when their meaning and documented behavior remain intact.

Before 1.0, current CLI behavior and Python symbols may change to support the approved
architecture. Such changes must preserve repository data, remain explicit in release
notes or migration guidance when material, and continue to meet existing requirements
until those requirements are deliberately revised.

### Non-goals

Public 1.0 does not promise:

- cloud analysis, telemetry, or a required network service;
- automatic remediation of repository findings;
- a large or exhaustive generated repository document;
- broad or implicit command execution;
- exhaustive language, framework, or build-system support;
- deep semantic code graphs or general program analysis;
- hosted dashboards, organization management, or commercial capabilities; or
- graphical IDE or web workflows.

Commercial positioning, proprietary requirements, and private-roadmap commitments do
not form part of this public contract.

## Acceptance repositories and measurements

pre-public planning item records candidate repositories in its research evidence. Once that research
supplies the named corpus, the implementation adds a versioned manifest, expected
facts, and its test harness under `tests/acceptance/`. Keeping those artifacts beside
the executable acceptance tests avoids creating empty infrastructure before the
inputs exist.

Final approval requires exactly 20 named public repositories at immutable commits:

- five Python repositories;
- five JavaScript/TypeScript repositories;
- five mixed multi-component repositories; and
- five generic or unsupported repositories.

The future manifest records each source URL, commit identifier, category, selection
rationale, license metadata, expected facts, and approved framework coverage. CI may
use network access to fetch the pinned inputs during an explicit preparation phase.
Slygentify itself is then run against those local inputs without network access; input
acquisition is not product runtime behavior. The third-party repositories are not
vendored into the Slygentify repository.

The release-candidate evidence must demonstrate:

- **Verified precision of 100 percent:** every claim classified as Verified matches
  the reviewed expected fact and cites inspectable repository evidence. A single
  unsupported Verified claim fails the gate.
- **Supported-fact recall of at least 95 percent:** at least 95 percent of applicable
  expected facts in the approved capability matrix are emitted. A supported fact
  reported only as unknown or omitted counts as missed.
- **Honest uncertainty:** every inference exposes its basis, and unsupported or
  unconfirmed information is not presented as verified.
- **Determinism:** identical repository content, configuration, and Slygentify version
  produce identical versioned JSON and generated content.
- **Bounded completion:** every corpus repository completes successfully or returns a
  documented partial result identifying the applicable limit and skipped scope.
- **Default effect safety:** `scan` and default `doctor` perform no repository writes,
  network requests, or repository command execution; dry-runs perform no writes; and
  mutations remain limited to their declared targets.
- **Interface parity:** CLI, JSON, and Python surfaces represent the same normalized
  facts and diagnostics within their documented presentation differences.
- **Operating-map task correctness:** in every designated representative scenario, a
  fresh agent given the task and generated operating map selects the reviewed issue
  authority, code-navigation path, validation gates, and access boundaries, and cites
  the supporting evidence. This evaluates repository orientation, not general agent
  intelligence.
- **Release readiness:** the approved Python/platform matrix, security checks,
  packaging checks, fresh-install tests, and compatibility tests all pass.

Maintainer feedback is required evidence for pre-public planning item and informs the final decision. The
questionnaire may remain open as a continuing calibration channel; its convenience
sample is not treated as representative or as a numeric 1.0 release threshold.

## Publication gate

The release is not public 1.0 until:

- the sanitized public repository and its required CI are live on GitHub;
- the approved source and wheel artifacts carry the required signing or provenance;
- wheel and source-distribution installations pass in fresh supported environments;
- the approved artifacts are published and verified on PyPI; and
- an authorized human completes the final go/no-go decision in pre-public planning item.

## Consequences

The contract favors correctness over apparent breadth. Unknown results are acceptable;
incorrect Verified claims are not. This strengthens user trust but makes a single
misclassified factual claim release-blocking.

`init` is a bootstrap interaction, not the durable product value by itself. A generated
AGENTS.md without preserved corrections, claim provenance, and drift detection can
become a stale artifact whose apparent authority exceeds its evidence. The roadmap
therefore treats the normalized operating map and `doctor` as essential parts of the
same 1.0 workflow rather than optional follow-up features.

Framework-aware inspection and a 20-repository external corpus materially increase
implementation, review, CI, and maintenance cost. Fetching acceptance inputs also
depends on upstream availability even though scans remain offline and commits are
pinned. The release process must report acquisition failures separately from product
failures.

Stable CLI, JSON, and Python contracts create an ongoing 1.x compatibility obligation.
Keeping human-readable output outside that boundary preserves room to improve the user
experience. Allowing pre-1.0 refinement avoids freezing the current small vertical
slice prematurely.

Conditional doctor execution expands the security and consent surface. ADR 0003 keeps
it outside the default path and defines its enforceable safety boundary; pre-public planning item must
define compatible diagnostic, exit, and public interface behavior before it enters
1.0.

The public contract creates no commitment for proprietary features or private roadmap
items. Public documentation must continue to distinguish implemented capability from
this proposed target until each behavior is delivered and verified.

## Alternatives considered

### Python-only or scan-and-doctor-only 1.0

A narrower release would reduce schedule and compatibility risk. It is rejected because
the selected small-team workflow includes mixed repositories, generated agent guidance,
and parity across the four public commands.

### Automatic remediation or broad command execution

These could shorten the path from finding to change. They are rejected because they
would enlarge mutation and trust boundaries beyond the smallest safe 1.0 workflow.

### Guessed component boundaries

Directory conventions could increase apparent discovery coverage. They are rejected
because false boundaries would contaminate downstream guidance and conflict with the
evidence-first contract.

### Synthetic-only, live-unpinned, or vendored acceptance repositories

Synthetic fixtures alone provide weak real-world evidence. Live unpinned repositories
make results nondeterministic. Full vendoring increases repository size and licensing
overhead. The selected approach records immutable public inputs and fetches them during
explicit CI preparation.

### Relaxed Verified-claim accuracy

A balanced precision/recall threshold would permit known factual errors. It is rejected
because users cannot safely distinguish the permitted errors from correct Verified
guidance.

### Stable human-readable output

Treating wording and layout as machine contracts would discourage accessibility and
usability improvements. Stable structured output provides the automation surface
instead.

### Numeric usability release gate

A small quantitative maintainer sample would imply more statistical confidence than
the initial research supports. Structured maintainer feedback remains research input,
while release acceptance uses reproducible technical gates.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
