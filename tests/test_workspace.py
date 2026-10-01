import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from paper_notes.tool_registry import ApplicationSpec, ToolRegistry, ToolSpec
from paper_notes.workspace import (
    WorkspaceError,
    child_environment,
    ensure_ports_available,
    stop_processes,
    streamlit_command,
    wait_until_ready,
)


class WorkspaceProcessTests(unittest.TestCase):
    def spec(self, port: int) -> ApplicationSpec:
        return ApplicationSpec(
            id="test",
            name="Test app",
            entrypoint=Path("test-app.py"),
            port=port,
            url=f"http://localhost:{port}",
        )

    @patch("paper_notes.workspace.port_is_available", return_value=False)
    def test_occupied_port_fails_before_startup(self, available):
        port = 8765
        with self.assertRaisesRegex(WorkspaceError, str(port)):
            ensure_ports_available([self.spec(port)])
        available.assert_called_once_with(port)

    def test_streamlit_command_is_local_headless_and_uses_exact_port(self):
        command = streamlit_command(self.spec(8765))
        self.assertIn("8765", command)
        self.assertIn("127.0.0.1", command)
        self.assertIn("true", command)
        self.assertNotIn("--server.runOnSave", command)

    def test_registry_urls_and_storage_are_propagated_to_children(self):
        tool = ToolSpec(
            id="paper-notes",
            name="Notes",
            entrypoint=Path("notes.py"),
            port=8501,
            url="http://localhost:8501",
            description="Notes",
            icon="N",
            storage=Path("notes-data"),
        )
        registry = ToolRegistry(
            root=Path("."),
            launcher=self.spec(8500),
            tools=(tool,),
        )
        environment = child_environment(registry)
        self.assertEqual(environment["WORKFLOW_TOOL_PAPER_NOTES_URL"], tool.url)
        self.assertEqual(environment["WORKFLOW_TOOL_PAPER_NOTES_STORAGE"], str(tool.storage))

    def test_termination_request_interrupts_startup_wait(self):
        process = Mock(spec=subprocess.Popen)
        process.poll.return_value = None
        stop_requested = threading.Event()
        stop_requested.set()
        with self.assertRaisesRegex(WorkspaceError, "interrupted"):
            wait_until_ready(self.spec(8765), process, stop_requested=stop_requested)

    @patch("paper_notes.workspace.os.killpg")
    def test_cleanup_signals_only_the_processes_it_is_given(self, killpg):
        first = Mock(spec=subprocess.Popen)
        first.pid = 101
        first.poll.return_value = None
        first.wait.return_value = 0
        second = Mock(spec=subprocess.Popen)
        second.pid = 202
        second.poll.return_value = None
        second.wait.return_value = 0

        stop_processes([first, second])

        self.assertEqual([call.args[0] for call in killpg.call_args_list], [202, 101])
        self.assertNotIn(999, [call.args[0] for call in killpg.call_args_list])


if __name__ == "__main__":
    unittest.main()
