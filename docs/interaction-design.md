# Interaction Design

## Status and scope

This document is the interaction-design contract for Slygentify. It guides new and
changed user-facing behavior and should be read before designing a command, diagnostic,
generated artifact, or automation interface.

The principles are channel-neutral where practical, with concrete conventions for the
current command-line application and its use in automation. They do not define a future
graphical or web interface. They also do not claim that every described capability is
already implemented.

The contract is binding repository guidance, not a substitute for requirements. Add
Doorstop requirements and test specifications when a principle produces observable
implemented behavior. Record a deliberate departure from this guidance in the concrete
requirement that needs it or, for a consequential architectural decision, in an ADR.

## Principles

### 1. Begin with an observable user task

Design from the outcome a person needs in a real repository, not from a technology or
artifact that Slygentify could produce. State the user, context, desired outcome, and
meaning of success before adding an interaction. Prefer the smallest complete workflow
that satisfies that need, and validate it against representative repositories.

### 2. Earn trust through evidence

State only what the available evidence supports. Preserve the distinction between facts,
inferences, recommendations, and missing knowledge throughout analysis, presentation,
and generated artifacts. Make the basis of a conclusion inspectable without forcing all
supporting detail into the default output.

Do not imply that probabilistic or heuristic results are certain. Do not introduce a
numeric confidence score unless its interpretation is defined and its calibration can be
evaluated.

### 3. Keep users in control of their repositories and artifacts

Repository files and generated artifacts belong to the user. Make consequential effects
clear before they occur, provide a review path, preserve existing work, and support safe
correction. Prefer open, editable output that remains useful without Slygentify.

Invoking an explicitly mutating command can constitute consent for its documented
effect; surprising mutations within an apparently diagnostic operation cannot. A command
must not silently expand the scope of an authorized change.

### 4. Remain safe, local, and quiet by default

Treat repositories as potentially untrusted input. Core inspection operates locally and
does not execute discovered project code, invoke discovered project commands, contact a
network service, or disclose repository content merely because those actions might
improve a result. Scan's one automatic process exception is the fixed, bounded Git
tracked-path capability accepted by ADR 0007. An exact `--git-executable` value is a
separate user authorization for that selected executable and must carry a direct warning
when documented.

Additional effects must be intentional, bounded, and visible. Avoid output, prompts,
telemetry, or filesystem changes that are unrelated to the requested task.

### 5. Make the common path simple and reveal detail progressively

Provide a useful default without requiring knowledge of Slygentify's internal model.
Keep normal output concise, but make evidence, skipped work, and diagnostics available
when they help a user investigate or decide.

`slygentify scan` is a narrow exception to the concise-default rule. Its normal text
report is deliberately complete and lossless because a sparse summary and a separate
flat verbose listing make repository structure, provenance, and partial results harder
to follow. The report groups the same canonical records into component-first navigation
without merging or suppressing them. Progressive exploration remains available through
the opt-in full-screen interface, while redirected plain text, `NO_COLOR`, and explicit
JSON preserve non-interactive and assistive access. This presentation exception does not
authorize a pager or effects beyond scan's separately accepted Git tracked-path capability.

Scan presentation should lead with user questions rather than internal record types.
Component paths organize identity, working tasks, architecture, automation, and items
needing attention; complete source records remain available as provenance rather than a
primary workflow. Product terminology should be explained in the selected-record context
and in dismissible help, not as a permanent navigation branch that every user must pass.

Errors should identify the problem, say what was or was not changed, and give the next
useful action when recovery is possible. Do the hard work in the product rather than
transferring incidental complexity to the user.

### 6. Behave predictably for humans and automation

Follow established command-line conventions and use consistent terms across commands.
Similar inputs and effects should produce similar interactions. Repeated operations
should be safe and deterministic where the underlying repository state has not changed.

Human-readable output is for people, not an accidental data format. When structured
output is introduced, make it explicit, documented, and suitable for compatibility
management. Every interactive workflow must have a clear non-interactive path before it
is required in automation.

### 7. Design inclusively and improve from observed use

Meaning must not depend solely on color, Unicode symbols, cursor positioning, animation,
or an interaction that excludes keyboard or assistive-technology users. Prefer plain,
direct language and layouts that remain understandable in constrained terminals.

Seek points of exclusion and learn from people with different abilities, experience
levels, tools, and repository contexts. Revise the design using observed task outcomes
and failure patterns rather than engagement measures or intuition alone.

## Shared claim vocabulary

Use these concepts consistently in domain models, user interfaces, diagnostics, and
generated knowledge. Presentation may vary by channel, but the meanings must not drift.

| Classification | Meaning | Presentation obligation |
| --- | --- | --- |
| **Verified** | Directly supported by inspectable repository evidence. | Make the evidence or verification method available. |
| **Inferred** | Derived from evidence but not directly confirmed. | Show the basis and clearly retain uncertainty. |
| **Recommended** | A proposed action or improvement. | Present it as optional advice, never as repository fact. |
| **Unknown** | Missing or unconfirmed information. | Say that it is unknown rather than guessing or silently omitting it. |

For example, the presence of a command in `pyproject.toml` may be verified, while the
claim that it is the team's preferred workflow may remain inferred or unknown until
stronger evidence exists. A recommendation to add a missing check is not evidence that
the repository already supports it.

The labels above are semantic classifications, not required literal prefixes in every
line of output. A compact interface may express them through grouping or surrounding
language as long as the distinction remains unambiguous and accessible.

## Interaction boundaries

Treat these effects as separate capabilities:

| Effect | Default contract |
| --- | --- |
| **Read-only inspection** | May read relevant local repository metadata while respecting exclusions and secret boundaries. |
| **Repository write** | Occurs only through an explicitly mutating operation, with reviewable output or a dry-run where practical, and preserves unrelated work. |
| **Repository command execution** | Never follows automatically from command discovery. Scan may use only ADR 0007's fixed bounded Git capability; any explicitly selected executable is separately authorized trusted code. |
| **Network access** | Is optional, visible, and separate from the core local workflow; repository content is not transmitted implicitly. |

Diagnostic commands remain read-only unless the user explicitly selects a documented
mutation. Combining effect classes must not obscure their consequences. Report partial
completion and leave the repository in a known state when an operation cannot finish.

## Command-line conventions

- Default output summarizes the result and the next relevant action without decorative
  noise. Detailed evidence and diagnostics belong behind an explicit mode when added.
- Successful results and requested content go to standard output. Diagnostics and
  operational errors go to standard error. Operational failure returns a non-zero exit
  status.
- A dry-run validates the same applicable preconditions and shows the proposed effect
  without performing it. Dry-run output must be sufficient to review the change.
- Prompts must not unexpectedly block non-interactive use. If a command can prompt, it
  must also offer an explicit way to approve, decline, or fail without a terminal.
- Scripts must not be expected to parse decorative human-readable output. Future
  structured output must be explicitly selected and documented before it is treated as
  a compatibility surface.
- Color and styling may reinforce meaning but cannot carry it alone. Essential output
  must remain understandable as plain text, without terminal control sequences or
  special glyph support.
- Error messages name the failed operation and relevant target without exposing secrets.
  Where possible, they state whether changes occurred and provide a concrete recovery
  action.

## Representative interactions

These examples illustrate the contract rather than prescribe exact future wording or
promise unimplemented options.

### Verified and inferred findings

```text
Verified: Python requires version 3.11 or newer.
Evidence: pyproject.toml [project.requires-python]

Inferred: pytest is the primary test runner.
Basis: pytest is a development dependency and tests/ is configured as a test path.
Verify: confirm the project's documented development workflow.
```

The inference remains visibly different from the verified manifest value, and the user
can see how to resolve the uncertainty.

### Review before mutation

The implemented initialization command already provides a review path:

```console
slygentify init --dry-run
```

It validates initialization preconditions and prints the exact proposed `AGENTS.md`
without writing it. Running `slygentify init` performs the documented creation, while an
existing `AGENTS.md` entry is preserved rather than overwritten.

### Task-scoped operating context

The implemented map command keeps dynamic detail out of persistent agent instructions:

```console
slygentify map --scope src/slygentify/cli.py --section workflows --section boundaries
```

Success writes only canonical versioned JSON to standard output. Required context is
never silently truncated; optional omissions are counted, and partial source completion
remains explicit. The logical scope need not exist, enabling review before a planned file
is created. Begin at scope `.`, follow the explicit direct-child navigation references,
and rerun map at the selected component path until the owning component matches the task.
This is bounded component navigation rather than recursive code enumeration.

### Safe failure

An operation that cannot safely continue should make its state explicit:

```text
Error: refusing to overwrite existing entry: /repo/AGENTS.md
No files were changed.
Next: review the existing AGENTS.md before deciding how to update it.
```

This is a target interaction example, not a claim that the current command emits these
exact three lines.

## Feature review checklist

Before accepting new or changed user-facing behavior, confirm:

- The user, repository context, desired outcome, and success condition are concrete.
- Verified, inferred, recommended, and unknown information remain distinguishable.
- Evidence is inspectable and uncertainty is not overstated.
- Read, write, execution, and network effects are explicit and appropriately separated.
- Existing user work is preserved and consequential changes are reviewable.
- Default output is concise except for the documented lossless `scan` report; errors
  and deeper diagnostics are actionable.
- Human and non-interactive uses have predictable behavior.
- Meaning survives plain-text, no-color, keyboard, and assistive-technology use.
- Observable implemented behavior has linked requirements and tests.
- Any deliberate deviation from this contract is documented where the decision is made.

## Influences

This contract adapts ideas from the following sources to Slygentify's context. Referencing
them does not adopt any source wholesale or replace project-specific judgment.

- [Nielsen's usability heuristics](https://www.nngroup.com/articles/ten-usability-heuristics/)
- [GOV.UK Design Principles](https://www.gov.uk/guidance/government-design-principles)
- [Command Line Interface Guidelines](https://clig.dev/)
- [Guidelines for Human-AI Interaction](https://www.microsoft.com/en-us/research/articles/guidelines-for-human-ai-interaction-eighteen-best-practices-for-human-centered-ai-design/)
- [Microsoft Inclusive Design](https://inclusive.microsoft.design/)
- [Web Content Accessibility Guidelines 2.2](https://www.w3.org/TR/WCAG22/)
- [Local-first software principles](https://www.inkandswitch.com/essay/local-first/)
