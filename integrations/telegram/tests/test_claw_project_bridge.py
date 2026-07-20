import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integrations.telegram import claw_project_bridge as bridge


PNG = b"\x89PNG\r\n\x1a\n" + b"fixture"


def test_config(root: Path) -> bridge.BridgeConfig:
    return bridge.BridgeConfig(
        bind_host="127.0.0.1",
        bind_port=0,
        bearer_token="x" * 32,
        state_file=root / "state" / "projects.json",
        projects_root=root / "projects",
        claw_binary=root / "claw",
        model="gemma4",
        allowed_tools="read,write,bash",
        permission_mode="workspace-write",
        turn_timeout=30,
        max_concurrent=2,
        max_body_bytes=1 << 20,
        max_attachment_bytes=1 << 20,
        auto_compact_input_tokens=22000,
        gemma_base_url="http://127.0.0.1:18080/v1",
        gemma_api_key="local-test",
        gemma_max_output_tokens=4096,
        ocr_timeout=30,
        ocr_languages="rus+eng",
    )


class ProjectStoreTests(unittest.TestCase):
    def test_new_switch_close_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = bridge.ProjectStore(root / "state.json", root / "projects")
            first = store.new_project("10", "Первый")
            second = store.new_project("10", "Второй")
            self.assertNotEqual(first["id"], second["id"])
            self.assertEqual(store.active_project("10")["id"], second["id"])
            switched = store.switch_project("10", first["id"][:6])
            self.assertEqual(switched["id"], first["id"])
            store.set_session("10", first["id"], "session-1", "/tmp/session-1")
            store.close_active("10")

            restored = bridge.ProjectStore(root / "state.json", root / "projects")
            listing = restored.list_projects("10")
            self.assertIsNone(listing["active_project_id"])
            saved = next(item for item in listing["projects"] if item["id"] == first["id"])
            self.assertEqual(saved["session_id"], "session-1")
            self.assertEqual(saved["status"], "closed")

    def test_project_name_does_not_control_workspace_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = bridge.ProjectStore(root / "state.json", root / "projects")
            project = store.new_project("10", "../../escape")
            self.assertEqual(project["name"], "../../escape")
            self.assertTrue(Path(project["workspace"]).is_relative_to(root / "projects"))


class RunnerTests(unittest.TestCase):
    def test_agent_environment_excludes_bridge_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = bridge.ClawRunner(test_config(root))
            with mock.patch.dict(
                bridge.os.environ,
                {
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/home/clawrun",
                    "CLAW_BRIDGE_TOKEN": "must-not-leak",
                    "UNRELATED_SECRET": "must-not-leak",
                },
                clear=True,
            ):
                environment = runner._agent_environment()

            self.assertNotIn("CLAW_BRIDGE_TOKEN", environment)
            self.assertNotIn("UNRELATED_SECRET", environment)
            self.assertEqual(environment["HOME"], "/home/clawrun")
            self.assertEqual(environment["GOOGLE_API_KEY"], "local-test")

    def test_first_turn_and_resume_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = bridge.ClawRunner(test_config(root))
            project = {"workspace": str(root), "session_id": None}
            first = runner._command(project, "hello")
            self.assertEqual(first[-2:], ["prompt", "hello"])
            resumed = runner._command(
                {
                    "workspace": str(root),
                    "session_id": "session-42",
                    "session_path": "/sessions/session-42.jsonl",
                },
                "continue",
            )
            self.assertEqual(
                resumed[-4:],
                ["--resume", "/sessions/session-42.jsonl", "prompt", "continue"],
            )
            self.assertIn("workspace-write", resumed)

    def test_recovers_completed_turn_from_changed_session_when_stdout_is_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.jsonl"
            session.write_text(
                json.dumps({"type": "session_meta", "session_id": "session-42"})
                + "\n",
                encoding="utf-8",
            )
            fake_claw = root / "claw"
            fake_claw.write_text(
                """#!/usr/bin/env python3
import json
import sys

path = sys.argv[sys.argv.index("--resume") + 1]
record = {
    "type": "message",
    "message": {
        "role": "assistant",
        "blocks": [{"type": "text", "text": "Восстановленный ответ"}],
        "usage": {"output_tokens": 3},
    },
}
with open(path, "a", encoding="utf-8") as stream:
    stream.write(json.dumps(record, ensure_ascii=False) + "\\n")
print("diagnostic without json")
""",
                encoding="utf-8",
            )
            fake_claw.chmod(0o755)
            runner = bridge.ClawRunner(test_config(root))
            result = runner.run_turn(
                "7",
                {
                    "workspace": str(root),
                    "session_id": "session-42",
                    "session_path": str(session),
                },
                "continue",
            )

            self.assertEqual(result["message"], "Восстановленный ответ")
            self.assertEqual(result["session_id"], "session-42")
            self.assertTrue(result["recovered_from_session"])

    def test_does_not_replay_old_session_reply_when_stdout_is_lost(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            session = root / "session.jsonl"
            session.write_text(
                json.dumps(
                    {
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "blocks": [{"type": "text", "text": "Старый ответ"}],
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fake_claw = root / "claw"
            fake_claw.write_text(
                """#!/usr/bin/env python3
import pathlib
import sys

path = pathlib.Path(sys.argv[sys.argv.index("--resume") + 1])
path.touch()
print("diagnostic-without-json")
"""
            )
            fake_claw.chmod(0o755)
            runner = bridge.ClawRunner(test_config(root))

            with self.assertRaisesRegex(bridge.BridgeError, "no JSON"):
                runner.run_turn(
                    "7",
                    {
                        "workspace": str(root),
                        "session_id": "session-42",
                        "session_path": str(session),
                    },
                    "continue",
                )


class ApplicationTests(unittest.TestCase):
    def test_attachment_validation_and_effective_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = test_config(root)
            application = bridge.BridgeApplication(config)
            project = application.store.new_project("1", "OCR")
            path = application.save_attachment(
                project,
                {
                    "mime_type": "image/png",
                    "data_base64": base64.b64encode(PNG).decode("ascii"),
                },
            )
            self.assertEqual(path.read_bytes(), PNG)
            prompt = application.effective_prompt(
                "Прочитай код",
                path,
                "image/png",
                "На изображении код 4827",
                "OCR TOOL OK 4827",
            )
            self.assertIn("OCR уже выполнен", prompt)
            self.assertIn("Не запускай OCR повторно", prompt)
            self.assertIn("4827", prompt)
            self.assertIn("локальный OCR", prompt)

    def test_ocr_runs_automatically_for_text_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = bridge.BridgeApplication(test_config(root))
            path = root / "fixture.png"
            path.write_bytes(PNG)
            completed = mock.Mock(returncode=0, stdout="OCR TOOL OK 4827\n")
            with mock.patch.object(
                bridge.subprocess, "run", return_value=completed
            ) as run:
                text = application.extract_attachment_text(
                    "Прочитай точный код", path, "image/png"
                )
            self.assertEqual(text, "OCR TOOL OK 4827")
            self.assertEqual(run.call_args.args[0][0], "tesseract")

    def test_ocr_is_skipped_for_non_text_image_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = bridge.BridgeApplication(test_config(root))
            path = root / "fixture.png"
            path.write_bytes(PNG)
            with mock.patch.object(bridge.subprocess, "run") as run:
                text = application.extract_attachment_text(
                    "Какие цвета преобладают?", path, "image/png"
                )
            self.assertEqual(text, "")
            run.assert_not_called()

    def test_rejects_attachment_signature_mismatch(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = bridge.BridgeApplication(test_config(root))
            project = application.store.new_project("1", "bad")
            with self.assertRaisesRegex(bridge.BridgeError, "signature"):
                application.save_attachment(
                    project,
                    {
                        "mime_type": "image/png",
                        "data_base64": base64.b64encode(b"not png").decode("ascii"),
                    },
                )

    def test_message_persists_session_and_separate_transcript(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = bridge.BridgeApplication(test_config(root))
            fake_result = {
                "message": "Готово",
                "session_id": "session-99",
                "session_path": "/sessions/session-99.jsonl",
                "usage": {"input_tokens": 20},
                "auto_compaction": None,
            }
            with mock.patch.object(
                application.runner, "run_turn", return_value=fake_result
            ) as run_turn:
                response = application.message(
                    {"chat_id": 7, "user_id": 8, "text": "Сделай задачу"}
                )
            self.assertEqual(response["message"], "Готово")
            project = application.store.active_project("7")
            self.assertEqual(project["session_id"], "session-99")
            run_turn.assert_called_once()
            transcript = Path(project["workspace"]) / "telegram-transcript.jsonl"
            record = json.loads(transcript.read_text(encoding="utf-8"))
            self.assertEqual(record["text"], "Сделай задачу")
            self.assertEqual(record["answer"], "Готово")

    def test_prompt_limit_is_enforced_before_starting_claw(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = bridge.BridgeApplication(test_config(root))
            with mock.patch.object(application.runner, "run_turn") as run_turn:
                with self.assertRaisesRegex(bridge.BridgeError, "too large"):
                    application.message({"chat_id": 1, "text": "x" * (70 * 1024)})
            run_turn.assert_not_called()


if __name__ == "__main__":
    unittest.main()
