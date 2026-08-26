# ADR 0010: Static doctor diagnostics and automation contract

## Status

Accepted

## Context

ADR 0002 includes `doctor` in the public 1.0 workflow and requires its default operation
to remain local, read-only, network-free, and non-executing. ADR 0005 defines a
conditional ceiling for any future command execution: exact per-invocation consent,
snapshot binding, a disposable copy, filesystem and credential isolation, bounded
processes and resources, denied network by default, cleanup, and fail-closed
unavailability when every control cannot be enforced. It leaves the diagnostic, public
interface, and exit contracts to pre-public planning item in pre-public approval record.

pre-public planning item in pre-public approval record reviewed the current scan, provenance, initialization, model,
CLI, and serialization behavior at commit
`0415c26e633fd326153e53135e6f437d358a817f`. The verified repository facts include:

- current deterministic provenance can detect configuration, evidence, detector,
  component, tooling, path, and generated-artifact drift without executing a project
  command;
- current scan results already provide reusable `Repository`, `Evidence`, and
  `SkippedScope` meanings plus bounded partial-result accounting;
- initialization already distinguishes managed, unmanaged, human-edited, missing,
  recoverable, invalid, and unsafe artifact states without overwriting user content; and
- no accepted cross-platform backend currently demonstrates every ADR 0005 execution
  control.

The underlying repository-knowledge drift problem has direct empirical support. A
peer-reviewed study of more than 3,000 GitHub projects found an outdated code-element
reference in 28.9% of the most popular projects at the time of analysis and in 82.3% at
some point in their history; affected references remained outdated for years on average
([Tan, Wagner, and Treude](https://doi.org/10.1007/s10664-023-10397-6)). Agent context
files also contain volatile operational knowledge: a study of 2,303 files from 1,925
repositories found test procedures in 75.9%, implementation details in 70.8%, and
architecture guidance in 68.1%, with the files evolving through frequent small additions
([Agent READMEs](https://arxiv.org/abs/2511.12884)).

Evidence that context files improve agent outcomes is mixed. One paired study of 124
pull requests reported lower median runtime and output-token use with `AGENTS.md`, while
not performing a comprehensive correctness evaluation
([Lulla et al.](https://arxiv.org/abs/2601.20404)). A separate evaluation of 138 tasks
from 12 repositories found no statistically significant task-success improvement and
more than 20% higher inference cost, although agents generally followed the instructions
([Gloaguen et al.](https://arxiv.org/abs/2602.11988)). A two-agent ablation over 288 runs
also found no measurable correctness improvement
([Khatri](https://arxiv.org/abs/2607.27250)).

The research inferred that doctor diagnostics concern the trustworthiness of current
agent-facing knowledge, while scan diagnostics concern inspection problems and
boundaries. A separate result and diagnostic type therefore avoids retroactively giving
every schema-major-1 scan diagnostic a CI severity. It also inferred that a provenance
digest change is not necessarily semantic drift, a human edit is not necessarily wrong,
and a literal attributable command is not necessarily safe.

The exact human wording and grouping still require corpus review. Static parsers cannot
establish runtime shell semantics, installed dependency behavior, hooks, plugins,
network needs, credential use, safety, or success. These unknowns constrain the claims
doctor may make; they do not justify project-command execution in public 1.0. No direct
causal evidence yet establishes that stale agent context files reduce task success or
that doctor improves agent outcomes. The bounded 1.0 value hypothesis is earlier
detection of invalidated operational knowledge and safer maintainer review, not making
coding agents intrinsically more capable.

## Decision

Adopt a **static-only, deterministic doctor contract** for public 1.0. Doctor assesses
current repository evidence, configuration, provenance, and generated artifacts without
executing project commands or changing the repository.

### Severity and claim classification

Doctor has exactly three severities:

| Severity | Meaning | Exit influence |
| --- | --- | --- |
| `info` | A useful observation that does not itself require correction to restore trustworthy managed knowledge. | None |
| `warning` | Verified drift or material uncertainty that requires review but does not prevent a trustworthy result. | Exit 1 |
| `error` | Invalid, unsafe, or missing managed state that prevents reliance on an affected contract or artifact. | Exit 1 |

Severity remains independent of ADR 0004's closed `verified`, `inferred`, `recommended`,
and `unknown` claim classification. Doctor adds no fatal severity, numeric severity,
confidence, or readiness score. A failure that prevents a trustworthy result is an input
or operational outcome rather than a repository diagnostic.

### Stable diagnostic catalog

Codes are stable opaque identifiers. Consumers compare the complete code and do not
derive semantics by splitting it. Each code has one fixed severity and problem
classification in public 1.0:

| Code | Severity | Classification | Contract |
| --- | --- | --- | --- |
| `doctor.configuration.invalid` | error | verified | Root configuration is malformed, unsupported, or violates its schema; validation stops before traversal. |
| `doctor.state.invalid` | error | verified | Provenance state is malformed, unsupported, oversized, or violates an invariant and cannot be used as authority. |
| `doctor.state.stale` | info | verified | Valid recorded provenance differs from current inputs or rule versions, while normalized knowledge and generated artifacts remain unchanged. A refresh is useful but trust is not reduced. |
| `doctor.evidence.missing` | warning | unknown | Previously recorded evidence is missing, unreadable, excluded, or unsafe, so dependent knowledge cannot be reverified. |
| `doctor.component.drift` | warning | verified | Canonical component identity, path, role, facets, or relationships differ from recorded provenance. |
| `doctor.tooling.drift` | warning | verified | Canonical manager, runtime, framework, validation-command, or CI-tool records differ from recorded provenance. |
| `doctor.path.missing` | warning | verified | A currently referenced safe in-root operational path is absent or is now an unsafe entry. |
| `doctor.artifact.missing` | error | verified | Valid state claims ownership of a managed artifact whose safe regular target is absent. |
| `doctor.artifact.diverged` | warning | unknown | Current artifact bytes match neither the recorded digest nor fresh deterministic generation and may contain a human edit. |
| `doctor.artifact.stale` | warning | verified | Current artifact bytes match the recorded digest but fresh deterministic generation differs. |
| `doctor.guidance.unmanaged` | info | unknown | Guidance is present without valid managed ownership, or no managed guidance exists where it could be initialized. |
| `doctor.inspection.partial` | warning | unknown | A fresh inspection reached a deterministic safety, policy, or resource boundary. |
| `doctor.command.unverifiable` | warning | unknown | An authoritative or previously attributable validation command became dynamic, external, unsupported, or unattributable, preventing reliance on managed command knowledge. |

Every diagnostic states the observed problem, its effect, and a safe remediation when
one exists. It cites only bounded evidence summaries and safe relative locations; it
never reproduces raw state, arbitrary source values, secrets, or human guidance.

### Evaluation, completion, and de-duplication

Doctor evaluates the smallest trustworthy layers in this order:

1. Resolve and validate the caller-selected target and repository identity.
2. Validate root configuration without traversing when configuration is invalid.
3. Parse state as bounded untrusted data and never use invalid state as authority.
4. Perform one fresh bounded scan using current evidence.
5. Compare canonical inputs, rule versions, derivations, components, tooling, paths,
   and artifact bytes.
6. Emit deterministic diagnostics and completion, then derive the CLI exit status.

`complete` means every applicable static doctor check completed within its supported
boundary. `partial` means at least one applicable check was omitted or bounded. Every
partial result identifies all known omitted boundaries through diagnostics or
`SkippedScope` records. An invalid configuration produces a partial result and stops
traversal. Invalid state does not block a safe fresh scan, but the result remains partial
for comparisons that require valid state. Unmanaged or absent managed guidance may be a
complete informational result.

Doctor prefers the most specific diagnostic for one changed contract. Component,
tooling, path, evidence, or artifact diagnostics suppress `doctor.state.stale` for the
same underlying change. Artifact missing, stale, and diverged are mutually exclusive for
one artifact. Invalid state suppresses ownership conclusions derived from that state,
including a duplicate `doctor.guidance.unmanaged`, while independent fresh-inspection
diagnostics remain reportable. `doctor.inspection.partial` describes a partial fresh
scan; it is not added merely because invalid configuration or state already explains a
partial doctor result. `doctor.state.stale` is informational only when current normalized
knowledge and generated artifact bytes are unchanged; any loss of trustworthy comparison
is represented by the applicable specific warning, error, or partial-result diagnostic.

### Python and JSON contracts

The future public immutable types follow ADR 0004's frozen standard-library dataclass
policy and are exported directly from `slygentify`:

- `DoctorDiagnostic` contains `id`, opaque `code`, `severity`, `classification`, optional
  `subject_id`, optional safe repository-relative `location`, separate `problem` and
  `effect`, optional `remediation`, and ordered `evidence_ids`. At least one of
  `subject_id` and `location` is required.
- `DoctorResult` contains integer `schema_version`, initially `1`; `producer_version`;
  `completion` as `complete` or `partial`; one existing `Repository`; and ordered tuples
  of existing `Evidence`, `DoctorDiagnostic`, and existing `SkippedScope` values.

Diagnostic identifiers are deterministic. Canonical diagnostic order is code, subject
or the empty string, location or the empty string, then identifier. Evidence and skipped
scopes retain ADR 0004's canonical ordering. Evidence references cannot dangle, and
identical supported inputs, effective configuration, repository content, and producer
version produce identical result bytes except for ADR 0005's explicitly reported
environmental boundaries.

The future supported entry points are:

```python
def doctor_repository(path=".", *, git_executable=None) -> DoctorResult: ...
def validate_doctor(value: object) -> DoctorResult: ...
def load_doctor_json(data: str | bytes) -> DoctorResult: ...
def dump_doctor_json(result: DoctorResult) -> bytes: ...
def doctor_json_schema() -> dict[str, object]: ...
```

`DoctorInputError` is the stable failure type for invalid caller-selected targets,
objects, or JSON. `DoctorOperationalError` is the stable failure type when an OS,
serialization, internal, or environmental failure prevents a trustworthy result.
Human-readable exception wording and undocumented attributes are not machine contracts.

The future normative wire artifact is a checked-in Slygentify-owned Draft 2020-12
`doctor-v1` JSON Schema. It uses ADR 0004's UTF-8 without BOM, LF, final newline, finite
values, fixed field order, canonical collection order, bounded parsing, closed producer
validation, and same-major additive unknown-property reader policy. Human summaries and
counts are derived presentation rather than duplicate wire state.

### CLI and automation contract

The future command is:

```console
slygentify doctor [PATH] --format text|json [--git-executable PATH]
```

It never prompts and has these stable exit meanings:

| Exit | Meaning | Output |
| ---: | --- | --- |
| 0 | A trustworthy result contains only informational diagnostics or no diagnostics. | Requested human report or one canonical JSON document on stdout. |
| 1 | A trustworthy complete or partial result contains at least one warning or error. | Requested human report or one canonical JSON document on stdout. |
| 2 | Invalid CLI usage or a caller-selected target prevented a repository result. | Actionable error on stderr and no result on stdout. |
| 3 | An operational or internal failure prevented a trustworthy result. | Actionable error on stderr and no result on stdout. |

JSON exits 0 and 1 write exactly one canonical `DoctorResult` to stdout and do not copy
result diagnostics to stderr. Invalid repository-owned configuration or state is a
result diagnostic and exit 1, not caller misuse. A bounded condition represented
honestly as a partial result is exit 1, not exit 3. Human wording and layout remain
evolvable; structured fields, codes, Python names, and exit meanings are compatibility
surfaces.

### Static command boundary

Static doctor may verify that a literal command is present in an approved source with
exact provenance, belongs to a supported in-root component working directory, retains
an explicit safe regular in-repository path where applicable, and remains consistent
with current direct tooling, manager, runtime, and CI evidence. It may report that a
declaration changed, disappeared, moved, or became unverifiable.

Doctor emits `doctor.command.unverifiable` only when a command is explicitly authoritative
in supported evidence, was previously attributable in valid provenance, or its loss of
attribution prevents reliance on managed command knowledge. A dynamic, external, or
unsupported command does not produce a warning merely because Slygentify cannot model
it. Unrelated and never-supported command declarations remain outside the diagnostic
surface.

Doctor cannot establish shell expansion, quoting, pipeline, redirection, substitution,
or other unsupported platform semantics; dynamic expressions or external includes;
ambient `PATH` selection; installed dependency or plugin behavior; network or credential
requirements; safety; idempotence; determinism; absence of effects; or runtime success.
It uses terms such as literal, attributable, contained, supported static form, and
unverifiable. It never emits a `command.safe` fact.

Default doctor remains local, read-only, network-free, and performs no project-command
execution. ADR 0007's fixed bounded Git tracked-path lookup remains the sole automatic
process exception. An explicit `--git-executable` or `git_executable` value retains only
its existing exact-executable authorization and warning; it never selects a project
validation command.

Public 1.0 provides no project-command execution option. Any later execution feature
requires separate research and a decision demonstrating every ADR 0005 backend control;
warning-only direct host execution remains prohibited.

### Compatibility and deferred policy

Within public 1.x, adding a new code or optional field is additive. Renaming a code,
changing its established meaning, severity, classification, exit influence, required
fields, or canonical order, or changing an exit meaning is breaking. Adding another
severity or claim classification is also breaking. Exact human wording is not stable.

Public 1.0 has no repository-configured suppressions, severity overrides, configurable
failure threshold, trusted policy channel, readiness score, numeric confidence,
automatic remediation, or project-command execution. These surfaces are deferred until
representative use establishes a need and a separate decision defines their trust and
compatibility boundaries.

Before the public 1.0 compatibility freeze, pre-public planning item in pre-public approval record must calibrate every rule
against realistic changed and clean-control repositories. Each verified diagnostic must
remain factually exact, each warning must identify a concrete loss of trust or required
maintainer review, and clean controls must not warn merely because Slygentify lacks a
parser. A rule that produces unactionable or parser-limitation noise is removed, narrowed,
or demoted before release through an explicit human-reviewed contract update. After
public 1.0, the compatibility rules above apply.

### Required acceptance scenarios

Dependent implementation and verification must cover at least these observable cases:

- clean managed state and current generated bytes: complete, exit 0;
- absent or unmanaged human guidance: informational unknown, no mutation, exit 0;
- malformed or unsupported configuration: partial configuration error, no traversal,
  exit 1;
- malformed or unsupported state: state error, safe fresh inspection where possible,
  partial, exit 1;
- provenance-only change without semantic or artifact drift: informational state-stale,
  exit 0;
- component, tooling, evidence, or referenced-path drift: the specific diagnostic
  without duplicate generic staleness, exit 1;
- bounded fresh inspection: partial with skipped-scope accounting, exit 1;
- missing, human-diverged, or deterministically stale managed artifact: the applicable
  mutually exclusive artifact diagnostic, no write, exit 1;
- literal attributable command: no safety claim and no command diagnostic;
- unrelated dynamic or unsupported command: no warning solely for parser limitations;
- authoritative or previously attributable command becomes unverifiable: unknown
  warning, exit 1;
- invalid caller target or option: no result, stderr, exit 2;
- operational failure that prevents a trustworthy result: no result, stderr, exit 3;
  and
- JSON with findings: one canonical document on stdout and exit 1.

## Consequences

Doctor can provide deterministic, CI-usable drift assessment using repository mechanisms
that already exist or are already accepted. A separate diagnostic model can carry
severity and structured problem/effect/remediation data without changing the established
scan schema. Including skipped scopes keeps partial doctor results evidence-complete.

The four exit outcomes let automation distinguish clean assessment, actionable findings,
caller error, and tool or environment failure without parsing human text. Stable codes,
severity, ordering, fields, exceptions, and exits create an explicit 1.x maintenance
obligation. Later empirical severity calibration may therefore require a breaking change
rather than a silent adjustment.

Static-only validation preserves the accepted effect boundary and avoids introducing an
unavailable or misleading sandbox dependency. It cannot prove that project commands are
safe or successful, so users must perform runtime verification in separately authorized
environments. A future trusted policy or execution capability requires another decision
rather than an incremental weakening of this contract.

The empirical evidence supports repository-knowledge drift as a genuine maintenance
problem and shows that agents act on context-file instructions. It does not demonstrate
that context files generally improve correctness or that this doctor contract will
improve task completion. Doctor is therefore evaluated as a knowledge-integrity and
maintainer-review control. Its rules must earn their compatibility cost through pre-public planning item's
precision and actionability review before public 1.0.

This ADR defines future public behavior but does not claim that `doctor` is implemented.
Doorstop requirements, test specifications, schemas, implementation, and user-facing
availability documentation belong to issues #32 through #34 and must be delivered
together with observable behavior.

## Alternatives considered

### Include opt-in project-command execution in public 1.0

Execution could confirm behavior in one selected environment, but no accepted backend
currently demonstrates every ADR 0005 isolation, resource, network, cleanup, and
cross-platform control. It is deferred rather than exposed as an unavailable or weaker
feature.

### Direct host execution with argv, reduced environment, timeout, and warning

Avoiding a shell reduces one injection class, but the executable and descendants could
still access files, credentials, processes, and networks. This alternative violates ADR
0005 and is rejected.

### Reuse or extend `ScanResult.Diagnostic`

This would assign CI severity and new structured meanings to an existing schema-major-1
type that deliberately has no severity contract. A separate result preserves scan
compatibility and the distinction between inspection boundaries and operating-knowledge
trust.

### Omit skipped scopes from `DoctorResult`

Diagnostics alone could summarize partial inspection, but they cannot retain the
existing deterministic effective-limit, consumption, and omitted-scope accounting. This
would make a partial result less inspectable than the scan that produced it and is
rejected.

### Collapse exits to 0, 1, and 2

This familiar shape would combine caller input problems with tool or environmental
failure. CI would need to parse unstable error text to distinguish remediation, so the
four-outcome contract is selected.

### Always exit zero and require structured-result parsing

This avoids opinionated CI failure but silently passes when automation neglects to parse
the document. It is rejected for the default automation contract.

### Configurable thresholds, suppressions, or repository severity overrides

These improve local policy flexibility but make exit behavior repository-dependent and
allow untrusted configuration to hide invalid state or weaken CI. They are deferred
until a separately trusted policy channel is justified.

## Approval record

This decision was accepted before the public cutover. Detailed pre-public approval evidence is retained in a private archive.
