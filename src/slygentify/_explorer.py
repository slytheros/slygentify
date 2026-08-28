"""Opt-in Textual explorer for immutable scan results."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    Collapsible,
    Footer,
    Header,
    Input,
    LoadingIndicator,
    RichLog,
    Select,
    Static,
    Tree,
)
from textual.widgets.tree import TreeNode
from textual.worker import Worker, WorkerState

from slygentify._glossary import glossary_text
from slygentify._presentation import (
    AttentionProblem,
    ComponentNavigationRole,
    RecordGroup,
    ScanPresentation,
    ScanRecord,
    attention_count_text,
    record_classification,
    record_kind,
    record_label,
)
from slygentify.models import Component, ScanResult, SkippedScope
from slygentify.traceability import implements

PayloadKind = Literal["summary", "group", "record"]


@dataclass(frozen=True, slots=True)
class _NodePayload:
    kind: PayloadKind
    detail: str
    record: ScanRecord | None = None
    records: tuple[ScanRecord, ...] = ()


class _RecordDetail(VerticalScroll):
    """A human-readable record explanation with opt-in technical detail."""

    def compose(self) -> ComposeResult:
        yield Static(id="record-summary")
        yield Collapsible(
            Static(id="raw-record"),
            title="Raw JSON record",
            collapsed=True,
            id="raw-record-section",
        )

    def on_mount(self) -> None:
        self.query_one("#raw-record-section", Collapsible).display = False

    def show(self, payload: _NodePayload, presentation: ScanPresentation) -> None:
        """Update this pane from a selected tree payload."""
        self.query_one("#record-summary", Static).update(Text(payload.detail))
        raw = self.query_one("#raw-record", Static)
        section = self.query_one("#raw-record-section", Collapsible)
        if payload.record is None:
            raw.update("")
            section.display = False
        else:
            raw.update(Text(presentation.record_json(payload.record)))
            section.collapsed = True
            section.display = True
        self.scroll_home(animate=False)


_TYPE_OPTIONS = (
    ("All record types", "all"),
    ("Components", "component"),
    ("Relationships", "relationship"),
    ("Findings", "finding"),
    ("Sources & provenance", "evidence"),
    ("Diagnostics", "diagnostic"),
    ("Inspection boundaries", "skipped-scope"),
)
_CLASSIFICATION_OPTIONS = (
    ("All classifications", "all"),
    ("VERIFIED", "verified"),
    ("INFERRED", "inferred"),
    ("RECOMMENDED", "recommended"),
    ("UNKNOWN", "unknown"),
)
_SECTION_HELP = {
    "At a glance": "A compact orientation to scan status, components, and technologies.",
    "What it is": "Identity, role, runtime, and package-management information.",
    "How to work on it": "Setup, run, test, lint, format, and build tasks.",
    "Architecture": "Entry points, frameworks, dependencies, tools, and relationships.",
    "Automation": "Commands and runtime choices declared by CI automation.",
    "Repository-wide workflows": "Tasks attributable to the repository as a whole.",
    "Needs attention": "Problems with next steps, unknowns to confirm, cautions, and recommendations.",
    "Inspection boundaries": "Areas omitted because of safety rules, unsupported input, or resource limits.",
    "Sources & provenance": "Inspected locations and observations supporting scan claims.",
}


@implements("REQ033")
class _GlossaryScreen(ModalScreen[None]):
    """Dismissible help overlay that does not occupy navigation space."""

    BINDINGS = [
        ("escape", "close_terms", "Close"),
        ("?", "close_terms", "Close"),
        ("q", "close_terms", "Close"),
    ]
    CSS = """
    _GlossaryScreen { align: center middle; background: $background 70%; }
    #glossary { width: 90%; max-width: 100; height: 90%;
                border: round $accent; padding: 1 2; background: $surface; }
    #glossary-content { height: 1fr; overflow-y: auto; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="glossary"):
            yield RichLog(wrap=True, markup=False, id="glossary-content")

    def on_mount(self) -> None:
        content = self.query_one("#glossary-content", RichLog)
        content.write(Text(glossary_text()))
        content.focus()

    def action_close_terms(self) -> None:
        self.dismiss(None)


@implements("REQ033")
class ScanExplorer(App[None]):
    """Keyboard-accessible tree and detail explorer with immediate scan feedback."""

    TITLE = "Slygentify scan explorer"
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("/", "focus_search", "Search"),
        ("?", "show_terms", "Terms"),
        ("escape", "clear_search", "Clear search"),
    ]
    CSS = """
    #controls { width: 42%; min-width: 30; }
    #detail { width: 58%; padding: 1 2; }
    #search { margin: 0 1 1 1; }
    #record-type, #classification { margin: 0 1 1 1; }
    #tree { height: 1fr; }
    #loading { height: 1fr; align: center middle; }
    #scan-status { width: auto; margin-top: 1; }
    #explorer { display: none; height: 1fr; }
    .ready #loading { display: none; }
    .ready #explorer { display: block; }
    #raw-record-section { margin-top: 1; }
    """

    def __init__(
        self,
        result: ScanResult | None,
        root: Path,
        *,
        scan: Callable[[], ScanResult] | None = None,
    ) -> None:
        super().__init__()
        if result is None and scan is None:
            raise ValueError("a scan callable is required when no result is supplied")
        self.presentation = None if result is None else ScanPresentation(result, root)
        self._root = root
        self._scan = scan
        self._scan_worker: Worker[ScanResult] | None = None
        self.error: BaseException | None = None

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="loading"):
            yield LoadingIndicator()
            yield Static(
                "Scanning repository… This can take a moment for large repositories.",
                id="scan-status",
            )
        with Horizontal(id="explorer"):
            with Vertical(id="controls"):
                yield Input(
                    placeholder="Search paths, commands, messages, and sources", id="search"
                )
                yield Select(
                    _TYPE_OPTIONS,
                    value="all",
                    allow_blank=False,
                    compact=True,
                    id="record-type",
                )
                yield Select(
                    _CLASSIFICATION_OPTIONS,
                    value="all",
                    allow_blank=False,
                    compact=True,
                    id="classification",
                )
                yield Tree[_NodePayload](
                    f"Repository - {self._root}",
                    _NodePayload("summary", "Repository scan result"),
                    id="tree",
                )
            yield _RecordDetail(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        if self.presentation is not None:
            self._show_explorer()
            return
        assert self._scan is not None
        self._scan_worker = self.run_worker(
            self._scan,
            thread=True,
            name="repository scan",
            exit_on_error=False,
        )

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker is not self._scan_worker:
            return
        if event.state is WorkerState.SUCCESS:
            result = event.worker.result
            assert result is not None
            self.presentation = ScanPresentation(result, self._root)
            self._show_explorer()
        elif event.state is WorkerState.ERROR:
            self.error = event.worker.error
            self.exit()

    def _show_explorer(self) -> None:
        self.add_class("ready")
        self._rebuild_tree()
        self.query_one("#tree", Tree).focus()

    def _filter_values(self) -> tuple[str, str, str]:
        query = self.query_one("#search", Input).value.casefold().strip()
        type_value = self.query_one("#record-type", Select).value
        classification_value = self.query_one("#classification", Select).value
        assert isinstance(type_value, str)
        assert isinstance(classification_value, str)
        return query, type_value, classification_value

    def _matches(self, record: ScanRecord) -> bool:
        assert self.presentation is not None
        query, type_value, classification_value = self._filter_values()
        if type_value != "all" and record_kind(record) != type_value:
            return False
        if classification_value != "all" and record_classification(record) != classification_value:
            return False
        return not query or query in self.presentation.record_search_text(record)

    def _matching_records(self, group: RecordGroup) -> tuple[ScanRecord, ...]:
        return tuple(record for record in group.records if self._matches(record))

    def _component_visible(self, component: Component, *, role: ComponentNavigationRole) -> bool:
        assert self.presentation is not None
        if self._matches(component) or any(
            self._matching_records(group)
            for group in self.presentation.component_groups(component.id)
        ):
            return True
        return any(
            self._component_visible(child, role=role)
            for child in self.presentation.children_of(component.id, role=role)
        )

    def _group(
        self,
        parent: TreeNode[_NodePayload],
        label: str,
        records: tuple[ScanRecord, ...],
    ) -> TreeNode[_NodePayload] | None:
        matches = tuple(record for record in records if self._matches(record))
        if not matches:
            return None
        return parent.add(
            f"{label} ({len(matches)})",
            _NodePayload("group", f"{label}: {len(matches)} records", records=matches),
            allow_expand=True,
        )

    def _navigation_group(
        self,
        parent: TreeNode[_NodePayload],
        label: str,
        detail: str,
        count: int,
        *,
        expand: bool,
    ) -> TreeNode[_NodePayload]:
        """Add a counted structural group, making known-empty groups unmistakable."""
        label_text = Text(f"{label} ({count})", style="dim" if count == 0 else "")
        payload = _NodePayload("summary", detail)
        if count == 0:
            return parent.add_leaf(label_text, payload)
        return parent.add(label_text, payload, expand=expand)

    def _add_group(
        self, section: TreeNode[_NodePayload], group: RecordGroup, records: tuple[ScanRecord, ...]
    ) -> None:
        assert self.presentation is not None
        if group.subsection == "Problems & next steps":
            by_target: defaultdict[tuple[str, str], list[AttentionProblem]] = defaultdict(list)
            for problem in self.presentation.attention_problems(group):
                by_target[
                    (
                        self.presentation.diagnostic_target(problem.diagnostic),
                        problem.diagnostic.code,
                    )
                ].append(problem)
            for target, code in sorted(by_target):
                problems = by_target[(target, code)]
                all_record_values: list[ScanRecord] = []
                for problem in problems:
                    all_record_values.append(problem.diagnostic)
                    all_record_values.extend(problem.related_unknowns)
                all_records = tuple(all_record_values)
                matches = tuple(record for record in all_records if self._matches(record))
                visible_issues = sum(
                    any(
                        self._matches(record)
                        for record in cast(
                            tuple[ScanRecord, ...],
                            (problem.diagnostic, *problem.related_unknowns),
                        )
                    )
                    for problem in problems
                )
                if not any(problem.related_unknowns for problem in problems):
                    section.add(
                        f"Problems & next steps / {target} / {code} "
                        f"({attention_count_text(visible_issues, len(matches))})",
                        _NodePayload(
                            "group",
                            f"{visible_issues} issues represented by {len(matches)} records",
                            records=matches,
                        ),
                        allow_expand=True,
                    )
                    continue
                target_node = section.add(
                    f"Problems & next steps / {target} / {code} "
                    f"({attention_count_text(visible_issues, len(matches))})",
                    _NodePayload(
                        "group",
                        f"{visible_issues} issues represented by {len(matches)} records",
                        records=matches,
                    ),
                    allow_expand=True,
                )
                for problem in problems:
                    problem_records: tuple[ScanRecord, ...] = (
                        problem.diagnostic,
                        *problem.related_unknowns,
                    )
                    problem_matches = tuple(
                        record for record in problem_records if self._matches(record)
                    )
                    if not problem_matches:
                        continue
                    issue = target_node.add(
                        f"Issue ({attention_count_text(1, len(problem_matches))})",
                        _NodePayload(
                            "group",
                            f"One problem represented by {len(problem_matches)} matching records",
                            records=problem_matches,
                        ),
                        allow_expand=True,
                    )
                    if self._matches(problem.diagnostic):
                        diagnostic = problem.diagnostic
                        issue.add_leaf(
                            record_label(self.presentation, diagnostic),
                            _NodePayload(
                                "record",
                                self.presentation.record_detail(diagnostic),
                                record=diagnostic,
                            ),
                        )
                    related = tuple(
                        finding for finding in problem.related_unknowns if self._matches(finding)
                    )
                    if related:
                        context = issue.add(
                            f"Related context ({len(related)})",
                            _NodePayload(
                                "group",
                                f"Related context: {len(related)} records",
                                records=related,
                            ),
                            allow_expand=True,
                        )
                        for finding in related:
                            context.add_leaf(
                                record_label(self.presentation, finding),
                                _NodePayload(
                                    "record",
                                    self.presentation.record_detail(finding),
                                    record=finding,
                                ),
                            )
            return
        if group.subsection == "Excluded or limited areas":
            skipped: defaultdict[str, list[ScanRecord]] = defaultdict(list)
            for record in records:
                assert isinstance(record, SkippedScope)
                skipped[record.reason].append(record)
            for reason in sorted(skipped):
                self._group(section, f"{reason}", tuple(skipped[reason]))
            return
        self._group(section, group.subsection, records)

    def _add_groups(
        self,
        parent: TreeNode[_NodePayload],
        groups: tuple[RecordGroup, ...],
        *,
        only_section: str | None = None,
        existing_sections: dict[str, TreeNode[_NodePayload]] | None = None,
    ) -> None:
        sections = dict(existing_sections or {})
        for group in groups:
            if only_section is not None and group.section != only_section:
                continue
            records = self._matching_records(group)
            if not records:
                continue
            section = parent if only_section is not None else sections.get(group.section)
            if section is None:
                section = parent.add(
                    group.section,
                    _NodePayload("summary", _SECTION_HELP[group.section]),
                    expand=True,
                )
                sections[group.section] = section
            self._add_group(section, group, records)

    def _group_count(self, groups: tuple[RecordGroup, ...], section: str) -> int:
        return sum(
            len(self._matching_records(group)) for group in groups if group.section == section
        )

    def _attention_counts(self, groups: tuple[RecordGroup, ...]) -> tuple[int, int]:
        """Count visible issues and records without splitting paired problem context."""
        assert self.presentation is not None
        issue_count = 0
        record_count = 0
        for group in groups:
            if group.section != "Needs attention":
                continue
            if group.subsection == "Problems & next steps":
                for problem in self.presentation.attention_problems(group):
                    records: tuple[ScanRecord, ...] = (
                        problem.diagnostic,
                        *problem.related_unknowns,
                    )
                    matches = tuple(record for record in records if self._matches(record))
                    if matches:
                        issue_count += 1
                        record_count += len(matches)
            else:
                matches = self._matching_records(group)
                issue_count += len(matches)
                record_count += len(matches)
        return issue_count, record_count

    def _add_component(
        self,
        parent: TreeNode[_NodePayload],
        component: Component,
        *,
        role: ComponentNavigationRole,
    ) -> TreeNode[_NodePayload] | None:
        assert self.presentation is not None
        if not self._component_visible(component, role=role):
            return None
        groups = self.presentation.component_groups(component.id)
        visible_children = tuple(
            child
            for child in self.presentation.children_of(component.id, role=role)
            if self._component_visible(child, role=role)
        )
        payload = _NodePayload(
            "record", self.presentation.record_detail(component), record=component
        )
        node = parent.add(
            record_label(self.presentation, component),
            payload,
            expand=component.path == ".",
        )
        if self._matches(component):
            what_it_is = node.add(
                "What it is",
                _NodePayload("summary", self.presentation.record_detail(component)),
                expand=True,
            )
            existing_sections = {"What it is": what_it_is}
        else:
            existing_sections = None
        self._add_groups(
            node,
            groups,
            existing_sections=existing_sections,
        )
        for child in visible_children:
            self._add_component(node, child, role=role)
        return node

    def _rebuild_tree(self) -> None:
        assert self.presentation is not None
        tree = cast(Tree[_NodePayload], self.query_one("#tree", Tree))
        tree.clear()
        tree.root.data = _NodePayload(
            "record",
            self.presentation.record_detail(self.presentation.result.repository),
            record=self.presentation.result.repository,
        )
        tree.root.expand()

        repository_groups = self.presentation.repository_groups()
        at_a_glance = self._navigation_group(
            tree.root,
            "At a glance",
            self.presentation.record_detail(self.presentation.result.repository),
            self._group_count(repository_groups, "At a glance"),
            expand=True,
        )
        self._add_groups(
            at_a_glance,
            repository_groups,
            only_section="At a glance",
        )

        primary_components = tuple(
            component
            for component in self.presentation.component_roots("unknown")
            if self._component_visible(component, role="unknown")
        )
        components = self._navigation_group(
            tree.root,
            "Component paths (primary navigation)",
            "Component paths are navigation, not inferred relationships.",
            len(primary_components),
            expand=True,
        )
        for component in primary_components:
            self._add_component(components, component, role="unknown")

        auxiliary_components = tuple(
            component
            for component in self.presentation.component_roots("auxiliary")
            if self._component_visible(component, role="auxiliary")
        )
        auxiliary = self._navigation_group(
            tree.root,
            "Auxiliary components (secondary navigation)",
            "Auxiliary components remain available without being presented as primary navigation.",
            len(auxiliary_components),
            expand=True,
        )
        for component in auxiliary_components:
            self._add_component(auxiliary, component, role="auxiliary")

        workflows = self._navigation_group(
            tree.root,
            "Repository-wide workflows",
            _SECTION_HELP["Repository-wide workflows"],
            self._group_count(repository_groups, "Repository-wide workflows"),
            expand=True,
        )
        self._add_groups(
            workflows,
            repository_groups,
            only_section="Repository-wide workflows",
        )

        attention_issues, attention_records = self._attention_counts(repository_groups)
        attention = self._navigation_group(
            tree.root,
            "Needs attention",
            f"{_SECTION_HELP['Needs attention']} {attention_issues} issues; "
            f"{attention_records} records.",
            attention_records,
            expand=True,
        )
        attention.set_label(
            Text(
                f"Needs attention ({attention_count_text(attention_issues, attention_records)})",
                style="dim" if attention_records == 0 else "",
            )
        )
        self._add_groups(
            attention,
            repository_groups,
            only_section="Needs attention",
        )

        boundaries = self._navigation_group(
            tree.root,
            "Inspection boundaries",
            _SECTION_HELP["Inspection boundaries"],
            self._group_count(repository_groups, "Inspection boundaries"),
            expand=False,
        )
        self._add_groups(
            boundaries,
            repository_groups,
            only_section="Inspection boundaries",
        )

        evidence_groups = self.presentation.evidence_groups()
        evidence_count = sum(
            len(tuple(record for record in records if self._matches(record)))
            for _, records in evidence_groups
        )
        sources = self._navigation_group(
            tree.root,
            "Sources & provenance",
            _SECTION_HELP["Sources & provenance"],
            evidence_count,
            expand=False,
        )
        for location, evidence_values in evidence_groups:
            self._group(sources, location, evidence_values)

    def on_tree_node_expanded(self, event: Tree.NodeExpanded[_NodePayload]) -> None:
        payload = event.node.data
        if payload is None or payload.kind != "group" or event.node.children:
            return
        assert self.presentation is not None
        for record in payload.records:
            event.node.add_leaf(
                record_label(self.presentation, record),
                _NodePayload("record", self.presentation.record_detail(record), record=record),
            )

    def _show_payload(self, payload: _NodePayload | None) -> None:
        if payload is None or self.presentation is None:
            return
        with suppress(NoMatches):
            self.query_one("#detail", _RecordDetail).show(payload, self.presentation)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[_NodePayload]) -> None:
        self._show_payload(event.node.data)

    def on_tree_node_selected(self, event: Tree.NodeSelected[_NodePayload]) -> None:
        self._show_payload(event.node.data)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search" and self.presentation is not None:
            self._rebuild_tree()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id in {"record-type", "classification"} and self.presentation is not None:
            self._rebuild_tree()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_show_terms(self) -> None:
        self.push_screen(_GlossaryScreen())

    def action_clear_search(self) -> None:
        search = self.query_one("#search", Input)
        if search.value:
            search.value = ""
        self.query_one("#tree", Tree).focus()


@implements("REQ033")
def run_scan_explorer(root: Path, scan: Callable[[], ScanResult]) -> None:
    """Run the explorer while scanning in a worker so its status is immediately visible."""
    app = ScanExplorer(None, root, scan=scan)
    app.run()
    if app.error is not None:
        raise app.error
