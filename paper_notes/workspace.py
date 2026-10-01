from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Iterable

from .tool_registry import ApplicationSpec, RegistryError, ToolRegistry, load_registry


class WorkspaceError(RuntimeError):
    """Raised when the local workspace cannot start safely."""


def port_is_available(port: int) -> bool:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        listener.close()
    return True


def ensure_ports_available(applications: Iterable[ApplicationSpec]) -> None:
    occupied = [app for app in applications if not port_is_available(app.port)]
    if occupied:
        details = ", ".join(f"{app.name} ({app.port})" for app in occupied)
        raise WorkspaceError(
            f"Required local port(s) are already occupied: {details}. "
            "Stop the existing app or use its current browser tab; no processes were started."
        )


def streamlit_command(app: ApplicationSpec) -> list[str]:
    return [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(app.entrypoint),
        "--server.port",
        str(app.port),
        "--server.address",
        "127.0.0.1",
        "--server.headless",
        "true",
        "--browser.gatherUsageStats",
        "false",
    ]


def wait_until_ready(
    app: ApplicationSpec,
    process: subprocess.Popen,
    timeout_seconds: float = 30.0,
    stop_requested: threading.Event | None = None,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if stop_requested is not None and stop_requested.is_set():
            raise WorkspaceError("Workspace startup was interrupted.")
        exit_code = process.poll()
        if exit_code is not None:
            raise WorkspaceError(f"{app.name} exited during startup with code {exit_code}.")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            if probe.connect_ex(("127.0.0.1", app.port)) == 0:
                return
        time.sleep(0.1)
    raise WorkspaceError(f"{app.name} did not become ready on port {app.port} within 30 seconds.")


def child_environment(registry: ToolRegistry) -> dict[str, str]:
    environment = os.environ.copy()
    for tool in registry.tools:
        prefix = re.sub(r"[^A-Z0-9]+", "_", tool.id.upper()).strip("_")
        environment[f"WORKFLOW_TOOL_{prefix}_URL"] = tool.url
        environment[f"WORKFLOW_TOOL_{prefix}_STORAGE"] = str(tool.storage)
    return environment


def stop_processes(processes: Iterable[subprocess.Popen], grace_seconds: float = 5.0) -> None:
    tracked = [process for process in processes if process.poll() is None]
    for process in reversed(tracked):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + grace_seconds
    for process in reversed(tracked):
        remaining = max(0.0, deadline - time.monotonic())
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)


def start_workspace(registry: ToolRegistry, *, open_browser: bool = True) -> int:
    applications = registry.applications
    ensure_ports_available(applications)
    processes: list[subprocess.Popen] = []
    stop_requested = threading.Event()
    previous_term_handler = signal.getsignal(signal.SIGTERM)

    def request_stop(signum, frame) -> None:
        del signum, frame
        stop_requested.set()

    signal.signal(signal.SIGTERM, request_stop)
    try:
        environment = child_environment(registry)
        for app in applications:
            if stop_requested.is_set():
                raise WorkspaceError("Workspace startup was interrupted.")
            print(f"Starting {app.name} at {app.url}…", flush=True)
            process = subprocess.Popen(
                streamlit_command(app),
                cwd=registry.root,
                start_new_session=True,
                env=environment,
            )
            processes.append(process)
            wait_until_ready(app, process, stop_requested=stop_requested)

        if open_browser:
            webbrowser.open_new_tab(registry.launcher.url)
        print(
            f"Workflow Hub is ready at {registry.launcher.url}. Press Ctrl+C to stop it.",
            flush=True,
        )
        while not stop_requested.wait(0.25):
            for app, process in zip(applications, processes):
                exit_code = process.poll()
                if exit_code is not None:
                    raise WorkspaceError(
                        f"{app.name} stopped unexpectedly with code {exit_code}."
                    )
    except KeyboardInterrupt:
        print("\nStopping the workspace…", flush=True)
    finally:
        stop_processes(processes)
        signal.signal(signal.SIGTERM, previous_term_handler)
    return 0


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    registry_path = Path(arguments[0]) if arguments else Path.cwd() / "tools.toml"
    try:
        registry = load_registry(registry_path)
        no_browser = os.environ.get("WORKFLOW_HUB_NO_BROWSER") == "1"
        return start_workspace(registry, open_browser=not no_browser)
    except (RegistryError, WorkspaceError) as error:
        print(f"Workspace could not start: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
