"""Shared bounded CI attribution helpers."""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import cast

import yaml  # type: ignore[import-untyped]

from slygentify._scan.contracts import RepositoryView
from slygentify._scan.detectors._support import StaticStructureError, pointer, strict_yaml_document
from slygentify._scan.paths import safe_member as _safe_member


def workflow_directory(value: object) -> str | None:
    if not isinstance(value, str) or "${{" in value or "\\" in value:
        return None
    stripped = value.strip()
    if stripped in {"", ".", "./"}:
        return "."
    return _safe_member(".", stripped[2:] if stripped.startswith("./") else stripped)


def owned_directory(
    directory: object, ownership: dict[str, bool | None], checkout_seen: bool
) -> str | None:
    normalized = workflow_directory(directory)
    if normalized is None:
        return None
    if not checkout_seen:
        return normalized
    candidates = [
        path
        for path in ownership
        if path == "." or normalized == path or normalized.startswith(f"{path}/")
    ]
    if not candidates:
        return None
    owner = max(candidates, key=len)
    if ownership[owner] is not True:
        return None
    if owner == ".":
        return normalized
    remainder = normalized[len(owner) :].lstrip("/")
    return remainder or "."


_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)(?<![A-Za-z0-9_])"
    r"[A-Za-z0-9_]*(?:token|password|passwd|secret|api[_-]?key)[A-Za-z0-9_]*"
    r"\s*=\s*(?P<value>\"[^\"\r\n]*\"|'[^'\r\n]*'|[^\s;&|]+)"
)
_CREDENTIAL_URL = re.compile(r"(?i)https?://[^/\s:@]+:(?P<value>[^/\s@]+)@")


def contains_literal_credential(command: str) -> bool:
    def is_literal(value: str) -> bool:
        unquoted = value.strip().strip("\"'")
        is_function_call = re.match(r"^[A-Za-z_][A-Za-z0-9_.]*\(", unquoted) is not None
        return (
            bool(unquoted)
            and not is_function_call
            and not any(
                marker in unquoted for marker in ("$", "%", "{", "}", "`", "$(", "{{", "}}")
            )
        )

    return any(
        is_literal(match.group("value")) for match in _CREDENTIAL_ASSIGNMENT.finditer(command)
    ) or any(is_literal(match.group("value")) for match in _CREDENTIAL_URL.finditer(command))


def expression_only(value: str) -> bool:
    return re.fullmatch(r"\s*\$\{\{(?:(?!}}).)*}}\s*", value, flags=re.DOTALL) is not None


@dataclass(frozen=True, slots=True)
class CICommand:
    path: str
    locator: str
    value: str
    directory: str


def inspect_commands(
    view: RepositoryView, issue: Callable[[str, str, bool], None]
) -> Iterator[CICommand]:
    """Inspect supported CI declarations without executing workflow inputs."""
    paths = frozenset(view.paths())

    def document(path: str) -> dict[str, object] | None:
        data = view.read_bytes(path)
        if data is None:
            return None
        try:
            result = strict_yaml_document(data)
            if not isinstance(result, dict):
                raise StaticStructureError("not a mapping")
            return cast(dict[str, object], result)
        except (UnicodeError, yaml.YAMLError, StaticStructureError):
            issue("invalid-ci-workflow", path, True)
            return None

    for path in sorted(paths):
        if view.checkpoint():
            return
        if not path.startswith((".github/workflows/", ".gitea/workflows/")) or not path.endswith(
            (".yml", ".yaml")
        ):
            continue
        workflow = document(path)
        jobs = workflow.get("jobs") if workflow is not None else None
        if workflow is None or not isinstance(jobs, dict):
            continue
        defaults = workflow.get("defaults")
        run = defaults.get("run") if isinstance(defaults, dict) else None
        workflow_directory_value = (
            run.get("working-directory", ".") if isinstance(run, dict) else "."
        )
        for job_name, job in jobs.items():
            if not isinstance(job, dict) or not isinstance(job.get("steps"), list):
                continue
            defaults = job.get("defaults")
            run = defaults.get("run") if isinstance(defaults, dict) else None
            directory = (
                run.get("working-directory", workflow_directory_value)
                if isinstance(run, dict)
                else workflow_directory_value
            )
            ownership: dict[str, bool | None] = {}
            checkout_seen = False
            for index, step in enumerate(job["steps"]):
                if view.checkpoint():
                    return
                if not isinstance(step, dict):
                    continue
                uses = step.get("uses")
                values = step.get("with")
                if isinstance(uses, str) and uses.startswith("actions/checkout@"):
                    checkout_seen = True
                    checkout = workflow_directory(
                        values.get("path", ".") if isinstance(values, dict) else "."
                    )
                    repository = values.get("repository") if isinstance(values, dict) else None
                    if checkout is not None:
                        ownership[checkout] = (
                            True
                            if repository is None
                            else False
                            if isinstance(repository, str) and "${{" not in repository
                            else None
                        )
                resolved = owned_directory(
                    step.get("working-directory", directory), ownership, checkout_seen
                )
                command = step.get("run")
                if isinstance(command, str):
                    if resolved is None:
                        issue("ci-scope-unresolved", path, False)
                    else:
                        yield CICommand(
                            path,
                            pointer("jobs", job_name, "steps", index, "run"),
                            command,
                            resolved,
                        )

    visited: set[str] = set()
    active: set[str] = set()

    def gitlab(path: str, depth: int) -> Iterator[CICommand]:
        if view.checkpoint():
            return
        if path in active:
            issue("ci-include-cycle", path, False)
            return
        if path in visited:
            return
        if depth > 16:
            issue("ci-include-depth", path, True)
            return
        visited.add(path)
        doc = document(path)
        if doc is None:
            return
        active.add(path)
        includes = doc.get("include", [])
        for include in includes if isinstance(includes, list) else [includes]:
            local = (
                include
                if isinstance(include, str)
                else include.get("local")
                if isinstance(include, dict)
                else None
            )
            if isinstance(local, str) and "*" not in local and "$" not in local:
                target = local.lstrip("/")
                if _safe_member(".", target) is None or target not in paths:
                    issue("invalid-ci-include", path, True)
                else:
                    yield from gitlab(target, depth + 1)
            elif include:
                issue("external-ci-include", path, False)

        def values(value: object, locator: tuple[object, ...]) -> Iterator[CICommand]:
            for index, command in enumerate(value if isinstance(value, list) else [value]):
                suffix: tuple[object, ...] = ()
                if isinstance(command, dict):
                    command = command.get("run")
                    suffix = ("run",)
                if isinstance(command, str):
                    yield CICommand(path, pointer(*locator, index, *suffix), command, ".")

        for field in ("before_script", "after_script"):
            if field in doc:
                yield from values(doc[field], (field,))
        reserved = {
            "include",
            "stages",
            "variables",
            "workflow",
            "default",
            "image",
            "services",
            "before_script",
            "after_script",
            "cache",
            "pages",
            "interruptible",
        }
        for name, job in doc.items():
            if name in reserved or name.startswith(".") or not isinstance(job, dict):
                continue
            for field in ("before_script", "script", "after_script", "run"):
                if field in job:
                    yield from values(job[field], (name, field))
        active.remove(path)

    if ".gitlab-ci.yml" in paths:
        yield from gitlab(".gitlab-ci.yml", 0)
