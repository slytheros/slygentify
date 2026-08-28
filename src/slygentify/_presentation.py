"""Shared, side-effect-free presentation index and Rich scan report."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal, TypeAlias

from rich.console import Console
from rich.text import Text
from rich.tree import Tree

from slygentify._glossary import compact_claim_guide, glossary_entry
from slygentify.models import (
    Component,
    ComponentRelationship,
    Diagnostic,
    Evidence,
    Finding,
    Repository,
    ScanResult,
    SkippedScope,
)
from slygentify.traceability import implements

RecordKind = Literal[
    "repository",
    "component",
    "relationship",
    "finding",
    "evidence",
    "diagnostic",
    "skipped-scope",
]
ScanRecord: TypeAlias = (
    Repository | Component | ComponentRelationship | Finding | Evidence | Diagnostic | SkippedScope
)
ComponentSection = Literal[
    "What it is",
    "How to work on it",
    "Architecture",
    "Automation",
    "Needs attention",
]
ComponentNavigationRole = Literal["unknown", "auxiliary"]

_COMPONENT_SECTION_ORDER: tuple[ComponentSection, ...] = (
    "What it is",
    "How to work on it",
    "Architecture",
    "Automation",
    "Needs attention",
)
_SUBSECTION_ORDER = {
    "Identity & role": 0,
    "Runtime & package managers": 1,
    "Other observations": 2,
    "Setup": 10,
    "Run": 11,
    "Test": 12,
    "Lint & format": 13,
    "Build": 14,
    "Other tasks": 15,
    "Entry points": 20,
    "Frameworks": 21,
    "Dependencies": 22,
    "Tools": 23,
    "Relationships": 24,
    "CI workflows & commands": 30,
    "Problems & next steps": 40,
    "Cautions": 41,
    "Unknowns to confirm": 42,
    "Recommendations": 43,
    "Excluded or limited areas": 44,
}

# Reviewed finding codes whose verified fact is itself cautionary. Unknown and
# recommended records are routed by classification; diagnostics are always problems.
_CAUTION_FINDING_CODES = frozenset({"javascript.npm-lock-precedence"})


@dataclass(frozen=True, slots=True)
class RecordGroup:
    """Existing scan records placed under one user-facing destination."""

    section: str
    subsection: str
    records: tuple[ScanRecord, ...]


@dataclass(frozen=True, slots=True)
class AttentionProblem:
    """One diagnostic and any canonical unknown findings it explains."""

    diagnostic: Diagnostic
    related_unknowns: tuple[Finding, ...]


def attention_count_text(issue_count: int, record_count: int) -> str:
    """Return grammatically counted issue and canonical-record text."""
    issue_label = "issue" if issue_count == 1 else "issues"
    record_label_text = "record" if record_count == 1 else "records"
    return f"{issue_count} {issue_label}; {record_count} {record_label_text}"


def _path_parts(path: str) -> tuple[str, ...]:
    return () if path == "." else PurePosixPath(path).parts


def _tokens(value: str) -> frozenset[str]:
    return frozenset(token for token in re.split(r"[^a-z0-9]+", value.casefold()) if token)


def record_kind(record: ScanRecord) -> RecordKind:
    """Return the human presentation kind for a public scan record."""
    if isinstance(record, Repository):
        return "repository"
    if isinstance(record, Component):
        return "component"
    if isinstance(record, ComponentRelationship):
        return "relationship"
    if isinstance(record, Finding):
        return "finding"
    if isinstance(record, Evidence):
        return "evidence"
    if isinstance(record, Diagnostic):
        return "diagnostic"
    return "skipped-scope"


def record_classification(record: ScanRecord) -> str | None:
    """Return a claim classification when the record carries one."""
    if isinstance(record, (Finding, ComponentRelationship)):
        return record.classification
    return None


def _classification_text(classification: str) -> Text:
    styles = {
        "verified": "bold green",
        "inferred": "bold yellow",
        "recommended": "bold blue",
        "unknown": "bold magenta",
    }
    return Text(classification.upper(), style=styles[classification])


@implements("REQ019", "REQ033")
@dataclass(slots=True)
class ScanPresentation:
    """A lightweight, user-focused index over one immutable scan result."""

    result: ScanResult
    root: Path
    evidence_by_id: dict[str, Evidence] = field(init=False)
    subject_paths: dict[str, str] = field(init=False)
    findings_by_subject: dict[str, tuple[Finding, ...]] = field(init=False)
    component_children: dict[str | None, tuple[Component, ...]] = field(init=False)
    relationships_by_source: dict[str, tuple[ComponentRelationship, ...]] = field(init=False)
    diagnostics_by_subject: dict[str | None, tuple[Diagnostic, ...]] = field(init=False)

    def __post_init__(self) -> None:
        self.evidence_by_id = {item.id: item for item in self.result.evidence}
        self.subject_paths = {self.result.repository.id: self.result.repository.root}
        self.subject_paths.update({item.id: item.path for item in self.result.components})

        findings: defaultdict[str, list[Finding]] = defaultdict(list)
        for finding in self.result.findings:
            findings[finding.subject_id].append(finding)
        self.findings_by_subject = {subject: tuple(values) for subject, values in findings.items()}

        relationships: defaultdict[str, list[ComponentRelationship]] = defaultdict(list)
        for relationship in self.result.relationships:
            relationships[relationship.source_id].append(relationship)
        self.relationships_by_source = {
            subject: tuple(values) for subject, values in relationships.items()
        }

        diagnostics: defaultdict[str | None, list[Diagnostic]] = defaultdict(list)
        for diagnostic in self.result.diagnostics:
            diagnostics[diagnostic.subject_id].append(diagnostic)
        self.diagnostics_by_subject = {
            subject: tuple(values) for subject, values in diagnostics.items()
        }

        children: defaultdict[str | None, list[Component]] = defaultdict(list)
        for component in self.result.components:
            component_parts = _path_parts(component.path)
            parents = [
                candidate
                for candidate in self.result.components
                if candidate.id != component.id
                and len(_path_parts(candidate.path)) < len(component_parts)
                and component_parts[: len(_path_parts(candidate.path))]
                == _path_parts(candidate.path)
            ]
            parent_id = (
                max(parents, key=lambda item: len(_path_parts(item.path))).id if parents else None
            )
            children[parent_id].append(component)
        self.component_children = {
            parent: tuple(sorted(values, key=lambda item: item.path))
            for parent, values in children.items()
        }

    def children_of(
        self,
        component_id: str | None,
        *,
        role: ComponentNavigationRole | None = None,
    ) -> tuple[Component, ...]:
        """Return direct component children, optionally within one presentation role."""
        children = self.component_children.get(component_id, ())
        if role is None:
            return children
        return tuple(component for component in children if component.role == role)

    def component_roots(self, role: ComponentNavigationRole) -> tuple[Component, ...]:
        """Return the top-level components for one role-specific navigation tree."""
        return tuple(
            component
            for component in self.result.components
            if component.role == role
            and not any(
                ancestor.role == role
                and ancestor.id != component.id
                and (ancestor.path == "." or component.path.startswith(f"{ancestor.path}/"))
                for ancestor in self.result.components
            )
        )

    def evidence_citation(self, evidence_ids: tuple[str, ...]) -> str:
        citations = []
        for evidence_id in evidence_ids:
            evidence = self.evidence_by_id[evidence_id]
            locator = f" [{evidence.locator}]" if evidence.locator is not None else ""
            citations.append(f"{evidence.location}{locator}")
        return ", ".join(citations) if citations else "no source reference"

    def diagnostic_target(self, diagnostic: Diagnostic) -> str:
        """Return the component path or location that a diagnostic concerns."""
        if diagnostic.subject_id is not None:
            return self.subject_paths[diagnostic.subject_id]
        assert diagnostic.location is not None
        return diagnostic.location

    def _finding_search_text(self, finding: Finding) -> str:
        evidence = " ".join(
            f"{item.location} {item.locator or ''} {item.observation}"
            for item in (self.evidence_by_id[item] for item in finding.evidence_ids)
        )
        return f"{finding.code} {finding.summary} {evidence}".casefold()

    def _finding_destination(self, finding: Finding) -> tuple[ComponentSection, str]:
        tokens = _tokens(self._finding_search_text(finding))
        code_tokens = _tokens(finding.code)
        if finding.code in _CAUTION_FINDING_CODES:
            return "Needs attention", "Cautions"
        if finding.classification == "unknown":
            return "Needs attention", "Unknowns to confirm"
        if finding.classification == "recommended":
            return "Needs attention", "Recommendations"
        if "ci" in code_tokens:
            return "Automation", "CI workflows & commands"
        if code_tokens & {"command", "script"}:
            task_tokens = (
                ("Setup", {"bootstrap", "init", "install", "setup", "sync"}),
                ("Run", {"dev", "run", "serve", "start"}),
                ("Test", {"pytest", "test", "tests"}),
                ("Lint & format", {"eslint", "format", "lint", "prettier", "ruff"}),
                ("Build", {"build", "bundle", "compile", "package"}),
            )
            for label, candidates in task_tokens:
                if tokens & candidates:
                    return "How to work on it", label
            return "How to work on it", "Other tasks"
        if code_tokens & {"entry", "entrypoint", "bin"}:
            return "Architecture", "Entry points"
        if "framework" in code_tokens:
            return "Architecture", "Frameworks"
        if code_tokens & {"dependency", "dependencies"}:
            return "Architecture", "Dependencies"
        if "tool" in code_tokens:
            return "Architecture", "Tools"
        if code_tokens & {"runtime", "manager"}:
            return "What it is", "Runtime & package managers"
        if code_tokens & {
            "affiliation",
            "boundary",
            "component",
            "manifest",
            "metadata",
            "workspace",
        }:
            return "What it is", "Identity & role"
        return "What it is", "Other observations"

    def _finding_groups(self, subject_id: str, *, repository: bool) -> tuple[RecordGroup, ...]:
        grouped: defaultdict[tuple[str, str], list[ScanRecord]] = defaultdict(list)
        for finding in self.findings_by_subject.get(subject_id, ()):
            section, subsection = self._finding_destination(finding)
            display_section: str = section
            if repository and section in {"What it is", "Architecture"}:
                display_section = "At a glance"
            elif repository and section in {"How to work on it", "Automation"}:
                display_section = "Repository-wide workflows"
            grouped[(display_section, subsection)].append(finding)
        return self._ordered_groups(grouped)

    def finding_groups(self, subject_id: str) -> tuple[RecordGroup, ...]:
        """Return user-facing finding groups for one repository or component subject."""
        return self._finding_groups(
            subject_id,
            repository=subject_id == self.result.repository.id,
        )

    @staticmethod
    def _ordered_groups(
        grouped: dict[tuple[str, str], list[ScanRecord]],
    ) -> tuple[RecordGroup, ...]:
        root_order = {"At a glance": -2, "Repository-wide workflows": -1}
        return tuple(
            RecordGroup(section, subsection, tuple(grouped[(section, subsection)]))
            for section, subsection in sorted(
                grouped,
                key=lambda item: (
                    _COMPONENT_SECTION_ORDER.index(item[0])
                    if item[0] in _COMPONENT_SECTION_ORDER
                    else root_order.get(item[0], 99),
                    _SUBSECTION_ORDER.get(item[1], 99),
                    item[1],
                ),
            )
        )

    def component_groups(self, component_id: str) -> tuple[RecordGroup, ...]:
        grouped: defaultdict[tuple[str, str], list[ScanRecord]] = defaultdict(list)
        for group in self._finding_groups(component_id, repository=False):
            grouped[(group.section, group.subsection)].extend(group.records)
        grouped[("Architecture", "Relationships")].extend(
            self.relationships_by_source.get(component_id, ())
        )
        self._merge_attention_groups(
            grouped,
            component_id,
            self.diagnostics_by_subject.get(component_id, ()),
        )
        return self._ordered_groups({key: values for key, values in grouped.items() if values})

    def repository_groups(self) -> tuple[RecordGroup, ...]:
        grouped: defaultdict[tuple[str, str], list[ScanRecord]] = defaultdict(list)
        for group in self._finding_groups(self.result.repository.id, repository=True):
            grouped[(group.section, group.subsection)].extend(group.records)
        component_ids = {component.id for component in self.result.components}
        repository_diagnostics: list[Diagnostic] = []
        for subject_id, diagnostics in self.diagnostics_by_subject.items():
            if subject_id not in component_ids:
                repository_diagnostics.extend(diagnostics)
        self._merge_attention_groups(
            grouped,
            self.result.repository.id,
            tuple(repository_diagnostics),
        )
        grouped[("Inspection boundaries", "Excluded or limited areas")].extend(
            self.result.skipped_scopes
        )
        return self._ordered_groups({key: values for key, values in grouped.items() if values})

    def _merge_attention_groups(
        self,
        grouped: defaultdict[tuple[str, str], list[ScanRecord]],
        subject_id: str,
        diagnostics: tuple[Diagnostic, ...],
    ) -> None:
        """Pair a subject's unknowns with diagnostics and keep every record once."""
        unknown_key = ("Needs attention", "Unknowns to confirm")
        unknowns = [
            record for record in grouped.pop(unknown_key, ()) if isinstance(record, Finding)
        ]
        remaining = list(unknowns)
        problem_records: list[ScanRecord] = []
        for diagnostic in sorted(
            diagnostics,
            key=lambda item: (self.diagnostic_target(item), item.code, item.id),
        ):
            problem_records.append(diagnostic)
            related = [
                finding
                for finding in remaining
                if finding.subject_id == subject_id
                and (
                    finding.code == diagnostic.code
                    or bool(set(finding.evidence_ids) & set(diagnostic.evidence_ids))
                )
            ]
            problem_records.extend(related)
            remaining = [finding for finding in remaining if finding not in related]
        grouped[("Needs attention", "Problems & next steps")].extend(problem_records)
        grouped[unknown_key].extend(remaining)

    @staticmethod
    def attention_problems(group: RecordGroup) -> tuple[AttentionProblem, ...]:
        """Recover diagnostic/related-unknown issue groups from a presentation group."""
        assert group.subsection == "Problems & next steps"
        problems: list[AttentionProblem] = []
        diagnostic: Diagnostic | None = None
        related: list[Finding] = []
        for record in group.records:
            if isinstance(record, Diagnostic):
                if diagnostic is not None:
                    problems.append(AttentionProblem(diagnostic, tuple(related)))
                diagnostic = record
                related = []
            else:
                assert isinstance(record, Finding) and diagnostic is not None
                related.append(record)
        assert diagnostic is not None
        problems.append(AttentionProblem(diagnostic, tuple(related)))
        return tuple(problems)

    def attention_counts(self, groups: tuple[RecordGroup, ...]) -> tuple[int, int]:
        """Return user-visible issue and canonical-record counts for attention groups."""
        attention = tuple(group for group in groups if group.section == "Needs attention")
        issue_count = sum(
            len(self.attention_problems(group))
            if group.subsection == "Problems & next steps"
            else len(group.records)
            for group in attention
        )
        return issue_count, sum(len(group.records) for group in attention)

    def component_attention_counts(self) -> tuple[int, int]:
        issue_count = 0
        record_count = 0
        for component in self.result.components:
            issues, records = self.attention_counts(self.component_groups(component.id))
            issue_count += issues
            record_count += records
        return issue_count, record_count

    def evidence_groups(self) -> tuple[tuple[str, tuple[Evidence, ...]], ...]:
        grouped: defaultdict[str, list[Evidence]] = defaultdict(list)
        for evidence in self.result.evidence:
            grouped[evidence.location].append(evidence)
        return tuple((location, tuple(grouped[location])) for location in sorted(grouped))

    def iter_records(self) -> tuple[ScanRecord, ...]:
        return (
            self.result.repository,
            *self.result.components,
            *self.result.relationships,
            *self.result.findings,
            *self.result.evidence,
            *self.result.diagnostics,
            *self.result.skipped_scopes,
        )

    def record_search_text(self, record: ScanRecord) -> str:
        return f"{self.record_detail(record)} {self.record_json(record)}".casefold()

    def _evidence_detail(self, evidence_ids: tuple[str, ...], heading: str) -> list[str]:
        if not evidence_ids:
            return [heading, "- No supporting source was recorded."]
        lines = [heading]
        for evidence_id in evidence_ids:
            item = self.evidence_by_id[evidence_id]
            locator = f" [{item.locator}]" if item.locator is not None else ""
            lines.append(f"- {item.location}{locator}: {item.observation}")
            if item.verification_method is not None:
                lines.append(f"  Checked by: {item.verification_method}")
        return lines

    def record_json(self, record: ScanRecord) -> str:
        """Return the complete selected record as readable JSON."""
        return json.dumps(asdict(record), ensure_ascii=False, indent=2)

    def record_detail(self, record: ScanRecord) -> str:
        """Return the human-first detail shown before a record's raw JSON."""
        if isinstance(record, Repository):
            return "\n".join(
                (
                    f"Repository: {record.root}",
                    f"Kind: {record.kind}",
                    f"Scan status: {self.result.completion.upper()}",
                )
            )
        if isinstance(record, Component):
            return "\n".join(
                (
                    f"Component: {record.path}",
                    f"Kind: {record.ecosystem}/{record.kind}",
                    f"Role: {record.role}",
                    f"Ecosystems: {', '.join(record.ecosystems)}",
                    "",
                    *self._evidence_detail(record.evidence_ids, "Declared or observed from:"),
                )
            )
        if isinstance(record, ComponentRelationship):
            return "\n".join(
                (
                    f"{record.classification.upper()}: {record.kind} relationship",
                    f"From: {self.subject_paths[record.source_id]}",
                    f"To: {self.subject_paths[record.target_id]}",
                    "",
                    *self._evidence_detail(record.evidence_ids, "Based on:"),
                )
            )
        if isinstance(record, Finding):
            heading = "Verified from:" if record.classification == "verified" else "Based on:"
            return "\n".join(
                (
                    f"{record.classification.upper()}: {record.summary}",
                    "",
                    *self._evidence_detail(record.evidence_ids, heading),
                )
            )
        if isinstance(record, Evidence):
            locator = f" [{record.locator}]" if record.locator is not None else ""
            lines = [
                f"Source: {record.location}{locator}",
                f"Observation: {record.observation}",
                f"Source kind: {record.source_kind}",
            ]
            if record.verification_method is not None:
                lines.append(f"Checked by: {record.verification_method}")
            return "\n".join(lines)
        if isinstance(record, Diagnostic):
            target = (
                self.subject_paths.get(record.subject_id, "repository")
                if record.subject_id is not None
                else "repository"
            )
            location = record.location or "repository-wide"
            lines = [
                f"Diagnostic: {record.code}",
                f"Applies to: {target} @ {location}",
                f"Problem: {record.problem or record.message}",
            ]
            if record.effect is not None:
                lines.append(f"Effect: {record.effect}")
            if record.safety_rationale is not None:
                lines.append(f"Why no automatic repair: {record.safety_rationale}")
            if record.recovery is not None:
                lines.append(f"Next: {record.recovery}")
            lines.extend(("", *self._evidence_detail(record.evidence_ids, "Related sources:")))
            return "\n".join(lines)
        limit = ""
        if record.effective_limit is not None:
            limit = f"\nLimit: {record.effective_limit}; consumed: {record.consumed}"
        return (
            f"Inspection boundary: {record.scope}\n"
            f"Reason: {record.reason}\n"
            f"Omitted area: {record.omitted_scope}{limit}"
        )


def record_label(index: ScanPresentation, record: ScanRecord) -> Text:
    if isinstance(record, Repository):
        return Text(f"{record.root} - {record.kind}")
    if isinstance(record, Component):
        facets = ", ".join(record.ecosystems)
        return Text(
            f"{record.path} - {record.ecosystem}/{record.kind} "
            f"(facets: {facets}; role: {record.role})"
        )
    if isinstance(record, ComponentRelationship):
        label = Text.assemble(
            _classification_text(record.classification),
            f" {record.kind}: {index.subject_paths[record.source_id]} -> "
            f"{index.subject_paths[record.target_id]}",
        )
        if record.evidence_ids:
            label.append(f" - source: {index.evidence_citation(record.evidence_ids)}", style="dim")
        return label
    if isinstance(record, Finding):
        label = Text.assemble(_classification_text(record.classification), f" {record.summary}")
        label.append(f" [{record.code}]", style="dim")
        label.append(f" - source: {index.evidence_citation(record.evidence_ids)}", style="dim")
        return label
    if isinstance(record, Evidence):
        locator = f" [{record.locator}]" if record.locator is not None else ""
        label = Text(f"[{record.source_kind}] {record.location}{locator}: {record.observation}")
        if record.verification_method is not None:
            label.append(f" - checked by: {record.verification_method}", style="dim")
        return label
    if isinstance(record, Diagnostic):
        location = f" @ {record.location}" if record.location is not None else ""
        return Text(f"{record.code}{location}: {record.message}")
    limit = f", limit={record.effective_limit}" if record.effective_limit is not None else ""
    consumed = f", consumed={record.consumed}" if record.consumed is not None else ""
    return Text(
        f"{record.scope}: {record.reason} (omitted: {record.omitted_scope}{limit}{consumed})"
    )


def _add_empty(branch: Tree, values: object) -> None:
    if not values:
        branch.add(Text("none", style="dim"))


def _add_component_group(index: ScanPresentation, section: Tree, group: RecordGroup) -> None:
    if group.subsection == "Problems & next steps":
        problems = index.attention_problems(group)
        diagnostic_branch = section.add(
            Text(
                f"Problems & next steps ({attention_count_text(len(problems), len(group.records))})"
            )
        )
        by_target: defaultdict[tuple[str, str], list[AttentionProblem]] = defaultdict(list)
        for problem in problems:
            by_target[
                (index.diagnostic_target(problem.diagnostic), problem.diagnostic.code)
            ].append(problem)
        for target, code in sorted(by_target):
            target_problems = by_target[(target, code)]
            record_count = sum(1 + len(problem.related_unknowns) for problem in target_problems)
            target_branch = diagnostic_branch.add(
                Text(
                    f"{target} - {code} "
                    f"({attention_count_text(len(target_problems), record_count)})"
                )
            )
            for problem in target_problems:
                diagnostic = problem.diagnostic
                if not problem.related_unknowns:
                    target_branch.add(record_label(index, diagnostic))
                    continue
                issue = target_branch.add(
                    Text(f"Issue ({attention_count_text(1, 1 + len(problem.related_unknowns))})")
                )
                issue.add(record_label(index, diagnostic))
                context = issue.add(Text(f"Related context ({len(problem.related_unknowns)})"))
                for finding in problem.related_unknowns:
                    context.add(record_label(index, finding))
        return
    subsection = section.add(Text(f"{group.subsection} ({len(group.records)})"))
    for record in group.records:
        subsection.add(record_label(index, record))


def _add_component(
    index: ScanPresentation,
    parent: Tree,
    component: Component,
    *,
    role: ComponentNavigationRole,
) -> None:
    branch = parent.add(record_label(index, component))
    groups = index.component_groups(component.id)
    sections: dict[str, Tree] = {}
    what_it_is = branch.add(Text("What it is"))
    what_it_is.add(
        Text(
            f"{component.kind.capitalize()} at {component.path}; role: {component.role}; "
            f"ecosystems: {', '.join(component.ecosystems)} - source: "
            f"{index.evidence_citation(component.evidence_ids)}"
        )
    )
    sections["What it is"] = what_it_is
    for group in groups:
        section = sections.get(group.section)
        if section is None:
            section = branch.add(Text(group.section))
            sections[group.section] = section
        _add_component_group(index, section, group)
    for child in index.children_of(component.id, role=role):
        _add_component(index, branch, child, role=role)


@implements("REQ019", "REQ030", "REQ031")
def build_report_tree(index: ScanPresentation) -> Tree:
    """Build the complete user-focused Rich tree without reading repository state."""
    result = index.result
    root = Tree(Text(f"Repository map - {index.root}"), guide_style="dim")

    at_a_glance = root.add(Text("At a glance"))
    status = glossary_entry(result.completion)
    at_a_glance.add(Text(f"Status: {status.label} - {status.short}"))
    at_a_glance.add(Text(f"Components: {len(result.components)}"))
    at_a_glance.add(Text(f"Inspection boundaries: {len(result.skipped_scopes)}"))
    ecosystems = sorted(
        {facet for component in result.components for facet in component.ecosystems}
    )
    at_a_glance.add(
        Text(f"Technologies: {', '.join(ecosystems) if ecosystems else 'not established'}")
    )
    at_a_glance.add(
        Text(
            "Inspection support: Python, JavaScript/TypeScript, and generic Cargo, Go, "
            "Maven, CMake/ESP-IDF, and KiCad evidence"
        )
    )

    repository_groups = index.repository_groups()
    for group in (item for item in repository_groups if item.section == "At a glance"):
        subsection = at_a_glance.add(Text(f"{group.subsection} ({len(group.records)})"))
        for record in group.records:
            subsection.add(record_label(index, record))

    primary_components = tuple(
        component for component in result.components if component.role == "unknown"
    )
    components = root.add(Text(f"Component paths (primary navigation) ({len(primary_components)})"))
    for component in index.component_roots("unknown"):
        _add_component(index, components, component, role="unknown")
    _add_empty(components, primary_components)

    auxiliary_components = tuple(
        component for component in result.components if component.role == "auxiliary"
    )
    auxiliary = root.add(
        Text(f"Auxiliary components (secondary navigation) ({len(auxiliary_components)})")
    )
    for component in index.component_roots("auxiliary"):
        _add_component(index, auxiliary, component, role="auxiliary")
    _add_empty(auxiliary, auxiliary_components)

    workflow_groups = tuple(
        group for group in repository_groups if group.section == "Repository-wide workflows"
    )
    workflows = root.add(
        Text(f"Repository-wide workflows ({sum(len(group.records) for group in workflow_groups)})")
    )
    for group in workflow_groups:
        subsection = workflows.add(Text(f"{group.subsection} ({len(group.records)})"))
        for record in group.records:
            subsection.add(record_label(index, record))
    _add_empty(workflows, workflow_groups)

    attention_groups = tuple(
        group for group in repository_groups if group.section == "Needs attention"
    )
    component_issues, component_records = index.component_attention_counts()
    repository_issues, repository_records = index.attention_counts(repository_groups)
    attention_issues = component_issues + repository_issues
    attention_records = component_records + repository_records
    attention = root.add(
        Text(f"Needs attention ({attention_count_text(attention_issues, attention_records)})")
    )
    if component_records:
        attention.add(
            Text(
                f"Component items: {attention_count_text(component_issues, component_records)} "
                "(shown once under their components)"
            )
        )
    for group in attention_groups:
        _add_component_group(index, attention, group)
    _add_empty(attention, (attention_records,) if attention_records else ())

    boundary_groups = tuple(
        group for group in repository_groups if group.section == "Inspection boundaries"
    )
    boundaries = root.add(
        Text(f"Inspection boundaries ({sum(len(group.records) for group in boundary_groups)})")
    )
    skipped: defaultdict[str, list[ScanRecord]] = defaultdict(list)
    for group in boundary_groups:
        for record in group.records:
            assert isinstance(record, SkippedScope)
            skipped[record.reason].append(record)
    for reason in sorted(skipped):
        reason_branch = boundaries.add(Text(f"{reason} ({len(skipped[reason])})"))
        for record in skipped[reason]:
            reason_branch.add(record_label(index, record))
    _add_empty(boundaries, boundary_groups)

    sources = root.add(Text(f"Sources & provenance ({len(result.evidence)})"))
    source_groups = index.evidence_groups()
    for location, evidence_values in source_groups:
        location_branch = sources.add(Text(f"{location} ({len(evidence_values)})"))
        for evidence_item in evidence_values:
            location_branch.add(record_label(index, evidence_item))
    _add_empty(sources, source_groups)
    return root


def _plain_tree_lines(tree: Tree) -> list[str]:
    lines: list[str] = []

    def visit(node: Tree, prefix: str, last: bool, *, root: bool = False) -> None:
        assert isinstance(node.label, Text)
        connector = "" if root else "`- " if last else "|- "
        lines.append(f"{prefix}{connector}{node.label.plain}")
        for position, child in enumerate(node.children):
            child_prefix = prefix if root else f"{prefix}{'   ' if last else '|  '}"
            visit(child, child_prefix, position == len(node.children) - 1)

    visit(tree, "", True, root=True)
    return lines


@implements("REQ019")
def render_scan_report(result: ScanResult, root: Path, console: Console) -> None:
    """Render a complete report to the supplied terminal-aware console."""
    tree = build_report_tree(ScanPresentation(result, root))
    console.print(Text("Scan completed", style="bold green"))
    if console.is_terminal and not console.legacy_windows:
        console.print(tree)
    else:
        console.print(Text("\n".join(_plain_tree_lines(tree))))
    console.print(Text(f"Claim terms: {compact_claim_guide()}", style="dim"))
