"""Headless interaction tests for the opt-in scan explorer."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any, cast

import pytest
from rich.text import Text
from textual.widgets import Collapsible, Input, RichLog, Select, Static, Tree
from textual.widgets.tree import TreeNode
from textual.worker import Worker, WorkerState

import slygentify._explorer as explorer
from slygentify import ComponentRelationship, ScanError, ScanResult
from slygentify._explorer import ScanExplorer, _GlossaryScreen, _NodePayload
from tests.scan_samples import sample_result


def _label(node: TreeNode[Any]) -> str:
    return node.label.plain if isinstance(node.label, Text) else str(node.label)


def _nodes(node: TreeNode[Any]) -> list[TreeNode[Any]]:
    return [node, *(descendant for child in node.children for descendant in _nodes(child))]


def _find(tree: Tree[Any], text: str) -> TreeNode[Any]:
    return next(node for node in _nodes(tree.root) if text in _label(node))


def _detail_text(app: ScanExplorer) -> str:
    return str(app.query_one("#record-summary", Static).render())


@pytest.mark.verifies("TST033")
def test_explorer_navigates_lazy_groups_and_resolves_details() -> None:
    async def scenario() -> None:
        app = ScanExplorer(sample_result(), Path("repository"))
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert app.focused is not None
            assert app.focused.id == "tree"
            assert not app.query_one("#raw-record-section", Collapsible).display
            tree = app.query_one("#tree", Tree)
            sources = _find(tree, "Sources & provenance")
            tree.move_cursor(sources)
            await pilot.press("space")
            await pilot.pause()
            assert sources.is_expanded
            evidence = _find(tree, ".git (1)")
            assert len(evidence.children) == 0

            tree.move_cursor(evidence)
            await pilot.press("space")
            await pilot.pause()
            assert evidence.is_expanded
            assert len(evidence.children) == 1
            await pilot.press("space")
            await pilot.pause()
            assert not evidence.is_expanded
            await pilot.press("space")
            await pilot.pause()
            assert evidence.is_expanded
            assert len(evidence.children) == 1

            tree.move_cursor(evidence)
            await pilot.press("down")
            await pilot.pause()
            assert "Source: .git" in _detail_text(app)
            assert "Git repository marker is present." in _detail_text(app)
            assert not evidence.children[0].allow_expand

            finding_group = _find(tree, "Other observations (1)")
            tree.move_cursor(finding_group)
            await pilot.press("space")
            await pilot.pause()
            tree.move_cursor(finding_group.children[0])
            await pilot.press("enter")
            await pilot.pause()
            finding_detail = _detail_text(app)
            assert "VERIFIED: A package boundary is verified." in finding_detail
            assert "Verified from:" in finding_detail
            assert "Cargo.toml [package]: Cargo package boundary is declared." in finding_detail
            assert "Meaning" not in finding_detail
            raw_section = app.query_one("#raw-record-section", Collapsible)
            assert raw_section.display
            assert raw_section.collapsed
            raw_title = raw_section.query_one("CollapsibleTitle")
            raw_title.focus()
            await pilot.press("enter")
            await pilot.pause()
            assert not raw_section.collapsed
            assert '"id": "finding_b"' in str(app.query_one("#raw-record", Static).render())

            await pilot.press("/")
            assert app.focused is app.query_one("#search", Input)
            await pilot.press(*tuple("verified"))
            await pilot.pause()
            assert app.query_one("#search", Input).value == "verified"
            filtered_tree = app.query_one("#tree", Tree)
            assert _find(filtered_tree, "Component paths")
            assert _find(filtered_tree, ". - generic/package")
            assert _find(filtered_tree, "Other observations (1)")

            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#search", Input).value == ""
            assert app.focused is not None
            assert app.focused.id == "tree"

            app.query_one("#record-type", Select).value = "evidence"
            await pilot.pause()
            labels = [_label(node) for node in _nodes(app.query_one("#tree", Tree).root)]
            assert any("Sources & provenance" in label for label in labels)
            assert any(".git (1)" in label for label in labels)
            assert not any("Relationships" in label for label in labels)

            app.query_one("#record-type", Select).value = "all"
            app.query_one("#classification", Select).value = "unknown"
            await pilot.pause()
            labels = [_label(node) for node in _nodes(app.query_one("#tree", Tree).root)]
            assert any("Unknowns (1)" in label for label in labels)
            assert not any(".git (1)" in label for label in labels)

            auxiliary_group = _find(app.query_one("#tree", Tree), "Auxiliary components")
            assert "(0)" in _label(auxiliary_group)
            assert not auxiliary_group.allow_expand

            app.query_one("#classification", Select).value = "all"
            await pilot.pause()
            await pilot.press("q")

    asyncio.run(scenario())


@pytest.mark.verifies("TST033", "TST030")
def test_explorer_separates_auxiliary_components_without_hiding_details() -> None:
    scan = sample_result()
    auxiliary = replace(
        scan.components[0],
        id="component_auxiliary",
        path="tests",
        role="auxiliary",
    )
    result = replace(scan, components=(scan.components[0], auxiliary))

    async def scenario() -> None:
        app = ScanExplorer(result, Path("repository"))
        async with app.run_test(size=(120, 32)) as pilot:
            tree = app.query_one("#tree", Tree)
            assert _find(tree, "Component paths (primary navigation)")
            assert _find(tree, "Auxiliary components (secondary navigation)")
            auxiliary_node = _find(tree, "tests - generic/package")
            tree.move_cursor(auxiliary_node)
            await pilot.press("enter")
            await pilot.pause()
            assert "Role: auxiliary" in _detail_text(app)

            app.query_one("#search", Input).value = "auxiliary"
            await pilot.pause()
            assert _find(app.query_one("#tree", Tree), "tests - generic/package")

    asyncio.run(scenario())


@pytest.mark.verifies("TST033")
def test_explorer_groups_high_cardinality_diagnostics_without_merging() -> None:
    scan = sample_result()
    diagnostics = tuple(
        replace(
            scan.diagnostics[0],
            id=f"diagnostic_{index:03}",
            message=f"Repeated diagnostic message {index}",
        )
        for index in range(50)
    )
    high_cardinality: ScanResult = replace(scan, diagnostics=diagnostics)
    child = replace(scan.components[0], id="component_child", path="child")
    relationship = ComponentRelationship(
        id="relationship_child",
        kind="contains",
        source_id=scan.components[0].id,
        target_id=child.id,
        classification="inferred",
        evidence_ids=scan.components[0].evidence_ids,
    )
    high_cardinality = replace(
        high_cardinality,
        components=(scan.components[0], child),
        relationships=(relationship,),
    )

    async def scenario() -> None:
        app = ScanExplorer(high_cardinality, Path("repository"))
        async with app.run_test(size=(160, 50)) as pilot:
            tree = app.query_one("#tree", Tree)
            assert _find(tree, "child - generic/package")
            assert _find(tree, "Relationships (1)")
            group = _find(tree, "Diagnostics / . / example.diagnostic (50)")
            assert len(group.children) == 0

            group.expand()
            await pilot.pause()
            assert len(group.children) == 50
            group.collapse()
            group.expand()
            await pilot.pause()
            assert len(group.children) == 50

            app.query_one("#search", Input).value = "message 42"
            await pilot.pause()
            filtered = _find(app.query_one("#tree", Tree), "example.diagnostic (1)")
            assert len(filtered.children) == 0
            filtered.expand()
            await pilot.pause()
            assert len(filtered.children) == 1
            assert "Repeated diagnostic message 42" in _label(filtered.children[0])

    asyncio.run(scenario())


@pytest.mark.verifies("TST033")
def test_explorer_event_guards_and_actions() -> None:
    async def scenario() -> None:
        app = ScanExplorer(sample_result(), Path("repository"))
        async with app.run_test() as pilot:
            tree = app.query_one("#tree", Tree)
            summary = _find(tree, "Component paths")
            empty = tree.root.add("No payload", None)
            app.on_tree_node_expanded(SimpleNamespace(node=summary))  # type: ignore[arg-type]
            app.on_tree_node_expanded(SimpleNamespace(node=empty))  # type: ignore[arg-type]
            app.on_tree_node_highlighted(SimpleNamespace(node=empty))  # type: ignore[arg-type]
            app.on_tree_node_selected(SimpleNamespace(node=empty))  # type: ignore[arg-type]
            app.on_worker_state_changed(
                cast(
                    Worker.StateChanged,
                    SimpleNamespace(worker=object(), state=WorkerState.SUCCESS),
                )
            )
            assert app.presentation is not None
            app.query_one("#detail", explorer._RecordDetail).show(
                _NodePayload("summary", "A concise summary"), app.presentation
            )
            assert not app.query_one("#raw-record-section", Collapsible).display

            other_input = Input(id="other")
            app.on_input_changed(SimpleNamespace(input=other_input))  # type: ignore[arg-type]
            other_select = Select((("Other", "other"),), value="other", id="other")
            app.on_select_changed(SimpleNamespace(select=other_select))  # type: ignore[arg-type]
            app.query_one("#record-type", Select).value = "evidence"
            await pilot.pause()
            assert (
                app._add_component(tree.root, sample_result().components[0], role="unknown") is None
            )

            app.action_focus_search()
            await pilot.pause()
            assert app.focused is app.query_one("#search", Input)
            app.action_clear_search()
            await pilot.pause()
            assert app.focused is not None
            assert app.focused.id == "tree"

    asyncio.run(scenario())


@pytest.mark.verifies("TST033")
def test_explorer_reports_worker_errors_after_restoring_the_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = ScanExplorer(None, Path("repository"), scan=sample_result)
    failure = ScanError("scan failed")
    worker = SimpleNamespace(error=failure)
    app._scan_worker = cast(Worker[ScanResult], worker)
    exited: list[bool] = []
    monkeypatch.setattr(app, "exit", lambda: exited.append(True))

    app.on_worker_state_changed(
        cast(Worker.StateChanged, SimpleNamespace(worker=worker, state=WorkerState.ERROR))
    )

    assert app.error is failure
    assert exited == [True]


@pytest.mark.verifies("TST033")
def test_explorer_shows_loading_state_before_a_scan_result_is_available() -> None:
    started = Event()
    release = Event()

    def scan() -> ScanResult:
        started.set()
        assert release.wait(timeout=5)
        return sample_result()

    async def scenario() -> None:
        app = ScanExplorer(None, Path("repository"), scan=scan)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            assert started.is_set()
            assert "Scanning repository" in str(app.query_one("#scan-status", Static).render())
            assert not app.query_one("#explorer").display

            release.set()
            await pilot.pause(0.1)
            assert app.presentation is not None
            assert app.query_one("#explorer").display
            assert app.focused is app.query_one("#tree", Tree)

    asyncio.run(scenario())


@pytest.mark.verifies("TST033")
def test_explorer_requires_a_scan_callable_without_a_completed_result() -> None:
    with pytest.raises(ValueError, match="scan callable"):
        ScanExplorer(None, Path("repository"))


@pytest.mark.verifies("TST033")
def test_explorer_glossary_is_contextual_and_dismissible() -> None:
    async def scenario() -> None:
        app = ScanExplorer(sample_result(), Path("repository"))
        async with app.run_test(size=(80, 24)) as pilot:
            main_screen = app.screen
            await pilot.press("?")
            await pilot.pause()

            assert isinstance(app.screen, _GlossaryScreen)
            content = app.screen.query_one("#glossary-content", RichLog)
            glossary = "\n".join(line.text for line in content.lines)
            assert "VERIFIED — Directly supported by repository content" in glossary
            assert "Partial scan — The scan succeeded" in glossary
            assert "Source & provenance — An inspected location" in glossary
            assert app.focused is content
            await pilot.press("end")
            await pilot.pause()
            assert content.scroll_y > 0

            await pilot.press("escape")
            await pilot.pause()
            assert app.screen is main_screen

    asyncio.run(scenario())


@pytest.mark.verifies("TST033")
def test_run_scan_explorer_starts_textual_app(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[ScanExplorer] = []
    monkeypatch.setattr(ScanExplorer, "run", lambda self: calls.append(self))

    explorer.run_scan_explorer(Path("repository"), sample_result)

    assert len(calls) == 1
    assert calls[0].presentation is None


@pytest.mark.verifies("TST033")
def test_run_scan_explorer_reraises_a_scan_error_after_the_app_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(self: ScanExplorer) -> None:
        self.error = ScanError("scan failed")

    monkeypatch.setattr(ScanExplorer, "run", fail)

    with pytest.raises(ScanError, match="scan failed"):
        explorer.run_scan_explorer(Path("repository"), sample_result)
