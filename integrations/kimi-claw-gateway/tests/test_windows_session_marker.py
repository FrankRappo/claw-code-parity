import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


@unittest.skipUnless(sys.platform == "win32", "Windows launcher test")
class WindowsSessionMarkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.script = (
            Path(__file__).resolve().parents[1]
            / "windows"
            / "get-kimi-session-marker.ps1"
        )

    def marker(self, workspace: Path) -> str:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.script),
                "-WorkspacePath",
                str(workspace),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return completed.stdout.strip()

    def test_marker_is_stable_for_the_same_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            self.assertEqual(self.marker(workspace), self.marker(workspace / "."))

    def test_marker_separates_different_workspaces(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_marker = self.marker(Path(first))
            second_marker = self.marker(Path(second))

        self.assertRegex(first_marker, r"^workspace-[0-9a-f]{32}$")
        self.assertNotEqual(first_marker, second_marker)
