from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import toml


ID_PATTERN = re.compile(r"[a-z][a-z0-9-]{0,63}")


class RegistryError(ValueError):
    """Raised when the local tool registry is malformed or unsafe."""


@dataclass(frozen=True)
class ApplicationSpec:
    id: str
    name: str
    entrypoint: Path
    port: int
    url: str


@dataclass(frozen=True)
class ToolSpec(ApplicationSpec):
    description: str
    icon: str
    storage: Path


@dataclass(frozen=True)
class ToolRegistry:
    root: Path
    launcher: ApplicationSpec
    tools: tuple[ToolSpec, ...]

    @property
    def applications(self) -> tuple[ApplicationSpec, ...]:
        return (*self.tools, self.launcher)


def _required_text(payload: dict[str, Any], key: str, context: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{context}.{key} must be a non-empty string.")
    return value.strip()


def _safe_path(root: Path, value: str, context: str, *, must_be_file: bool) -> Path:
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise RegistryError(f"{context} must be a project-relative path without '..'.")
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise RegistryError(f"{context} resolves outside the project folder.") from error
    if must_be_file and (resolved.suffix != ".py" or not resolved.is_file()):
        raise RegistryError(f"{context} must name an existing project-local Python file: {value}")
    return resolved


def _port(payload: dict[str, Any], context: str) -> int:
    value = payload.get("port")
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
        raise RegistryError(f"{context}.port must be an integer from 1 through 65535.")
    return value


def _local_url(payload: dict[str, Any], port: int, context: str) -> str:
    value = _required_text(payload, "url", context)
    parsed = urlparse(value)
    try:
        parsed_port = parsed.port
    except ValueError as error:
        raise RegistryError(f"{context}.url contains an invalid port.") from error
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"localhost", "127.0.0.1"}
        or parsed_port != port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise RegistryError(
            f"{context}.url must be a plain local HTTP URL whose port matches {port}."
        )
    return value.rstrip("/")


def _application(
    root: Path, payload: dict[str, Any], context: str, *, identifier: str
) -> ApplicationSpec:
    port = _port(payload, context)
    return ApplicationSpec(
        id=identifier,
        name=_required_text(payload, "name", context),
        entrypoint=_safe_path(
            root,
            _required_text(payload, "entrypoint", context),
            f"{context}.entrypoint",
            must_be_file=True,
        ),
        port=port,
        url=_local_url(payload, port, context),
    )


def load_registry(path: Path) -> ToolRegistry:
    registry_path = Path(path).resolve()
    root = registry_path.parent
    try:
        payload = toml.loads(registry_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RegistryError(f"Could not read tool registry: {error}") from error
    except (TypeError, ValueError) as error:
        raise RegistryError(f"Malformed tool registry: {error}") from error
    if not isinstance(payload, dict):
        raise RegistryError("The tool registry must contain a TOML table.")
    if payload.get("schema_version") != 1:
        raise RegistryError("tools.toml schema_version must be 1.")

    workspace = payload.get("workspace")
    if not isinstance(workspace, dict):
        raise RegistryError("tools.toml must contain a [workspace] table.")
    launcher = _application(root, workspace, "workspace", identifier="workflow-hub")

    raw_tools = payload.get("tools")
    if not isinstance(raw_tools, list) or not raw_tools:
        raise RegistryError("tools.toml must contain at least one [[tools]] entry.")

    tools: list[ToolSpec] = []
    ids: set[str] = set()
    ports = {launcher.port}
    storage_paths: list[Path] = []
    for index, raw_tool in enumerate(raw_tools, start=1):
        context = f"tools[{index}]"
        if not isinstance(raw_tool, dict):
            raise RegistryError(f"{context} must be a TOML table.")
        identifier = _required_text(raw_tool, "id", context)
        if not ID_PATTERN.fullmatch(identifier):
            raise RegistryError(
                f"{context}.id must start with a lowercase letter and use lowercase letters, numbers, or hyphens."
            )
        if identifier in ids or identifier == launcher.id:
            raise RegistryError(f"Duplicate tool ID: {identifier}")
        ids.add(identifier)
        base = _application(root, raw_tool, context, identifier=identifier)
        if base.port in ports:
            raise RegistryError(f"Duplicate application port: {base.port}")
        ports.add(base.port)
        storage = _safe_path(
            root,
            _required_text(raw_tool, "storage", context),
            f"{context}.storage",
            must_be_file=False,
        )
        for existing in storage_paths:
            if storage == existing or storage in existing.parents or existing in storage.parents:
                raise RegistryError(
                    f"Storage ownership overlaps: {storage.relative_to(root)} and {existing.relative_to(root)}"
                )
        storage_paths.append(storage)
        tools.append(
            ToolSpec(
                **base.__dict__,
                description=_required_text(raw_tool, "description", context),
                icon=_required_text(raw_tool, "icon", context),
                storage=storage,
            )
        )
    return ToolRegistry(root=root, launcher=launcher, tools=tuple(tools))
