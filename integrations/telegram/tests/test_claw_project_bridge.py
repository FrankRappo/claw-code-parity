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
        allowed_tools=None,
        permission_mode="danger-full-access",
        unrestricted=True,
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


class BridgeConfigTests(unittest.TestCase):
    def test_default_configuration_exposes_the_full_tool_registry(self):
        with mock.patch.dict(
            bridge.os.environ,
            {
                "CLAW_BRIDGE_TOKEN": "x" * 32,
                "CLAW_UNRESTRICTED": "1",
            },
            clear=True,
        ):
            config = bridge.BridgeConfig.from_env()

        self.assertIsNone(config.allowed_tools)
        self.assertEqual(config.permission_mode, "danger-full-access")
        self.assertTrue(config.unrestricted)
        self.assertEqual(config.gemma_max_output_tokens, 32000)
        self.assertEqual(config.auto_compact_input_tokens, 110000)
        self.assertIsNone(config.turn_timeout)
        self.assertIsNone(config.ocr_timeout)

    def test_explicit_tool_allowlist_remains_available_for_other_deployments(self):
        with mock.patch.dict(
            bridge.os.environ,
            {
                "CLAW_BRIDGE_TOKEN": "x" * 32,
                "CLAW_ALLOWED_TOOLS": "read,bash",
            },
            clear=True,
        ):
            config = bridge.BridgeConfig.from_env()

        self.assertEqual(config.allowed_tools, "read,bash")

    def test_unrestricted_registry_reports_agent_enabled_without_an_allowlist(self):
        self.assertTrue(bridge.configured_tool_enabled(None, "Agent"))
        self.assertTrue(bridge.configured_tool_enabled("read,Agent,bash", "Agent"))
        self.assertFalse(bridge.configured_tool_enabled("read,bash", "Agent"))

    def test_restricted_profile_does_not_enable_unrestricted_runtime(self):
        with mock.patch.dict(
            bridge.os.environ,
            {"CLAW_BRIDGE_TOKEN": "x" * 32},
            clear=True,
        ):
            config = bridge.BridgeConfig.from_env()
            environment = bridge.ClawRunner(config)._agent_environment()

        self.assertFalse(config.unrestricted)
        self.assertEqual(config.permission_mode, "workspace-write")
        self.assertEqual(config.allowed_tools, "Read,Glob,Grep")
        self.assertNotIn("CLAW_UNRESTRICTED", environment)
        self.assertNotIn("CLAW_SUBAGENT_LOCK_FILE", environment)


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
    def test_agent_environment_inherits_all_credentials_and_enables_unrestricted_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = bridge.ClawRunner(test_config(root))
            with mock.patch.dict(
                bridge.os.environ,
                {
                    "PATH": "/usr/bin:/bin",
                    "HOME": "/home/clawrun",
                    "CLAW_BRIDGE_TOKEN": "bridge-secret",
                    "TELEGRAM_BOT_TOKEN": "telegram-secret",
                    "GITHUB_TOKEN": "github-secret",
                    "UNRELATED_SECRET": "other-secret",
                    "CLAW_SYSTEM_PROMPT_FILE": "/srv/prompts/gemma4.txt",
                },
                clear=True,
            ):
                environment = runner._agent_environment()

            self.assertEqual(environment["CLAW_BRIDGE_TOKEN"], "bridge-secret")
            self.assertEqual(environment["TELEGRAM_BOT_TOKEN"], "telegram-secret")
            self.assertEqual(environment["GITHUB_TOKEN"], "github-secret")
            self.assertEqual(environment["UNRELATED_SECRET"], "other-secret")
            self.assertEqual(environment["HOME"], "/home/clawrun")
            self.assertEqual(environment["GOOGLE_API_KEY"], "local-test")
            self.assertEqual(environment["CLAW_SUBAGENT_MODEL"], "gemma4")
            self.assertEqual(environment["CLAW_SUBAGENT_MAX_CONCURRENT"], "1")
            self.assertEqual(environment["CLAW_UNRESTRICTED"], "1")
            self.assertEqual(
                environment["CLAW_SUBAGENT_LOCK_FILE"],
                str(root / "state" / "claw-subagent.lock"),
            )
            self.assertEqual(
                environment["CLAW_SYSTEM_PROMPT_FILE"],
                "/srv/prompts/gemma4.txt",
            )

    def test_first_turn_and_resume_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = bridge.ClawRunner(test_config(root))
            project = {"workspace": str(root), "session_id": None}
            first = runner._command(project, "hello")
            self.assertEqual(first[-2:], ["prompt", "hello"])
            self.assertNotIn("--allowedTools", first)
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
            self.assertIn("danger-full-access", resumed)

    def test_command_can_still_apply_an_explicit_tool_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = test_config(root)
            config = bridge.BridgeConfig(
                **{**config.__dict__, "allowed_tools": "read,bash"}
            )
            command = bridge.ClawRunner(config)._command(
                {"workspace": str(root), "session_id": None}, "hello"
            )

            index = command.index("--allowedTools")
            self.assertEqual(command[index + 1], "read,bash")

    def test_stop_tombstone_prevents_a_not_yet_registered_turn(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_claw = root / "claw"
            fake_claw.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            fake_claw.chmod(0o755)
            runner = bridge.ClawRunner(test_config(root))
            self.assertFalse(runner.stop("7", ("operation-1",)))

            with mock.patch.object(bridge.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(bridge.BridgeError, "was stopped"):
                    runner.run_turn(
                        "7",
                        {"workspace": str(root), "session_id": None},
                        "continue",
                        "operation-1",
                    )

            popen.assert_not_called()

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
                    {
                        "chat_id": 7,
                        "user_id": 8,
                        "text": "Сделай задачу",
                        "operation_id": "operation-99",
                    }
                )
            self.assertEqual(response["message"], "Готово")
            project = application.store.active_project("7")
            self.assertEqual(project["session_id"], "session-99")
            run_turn.assert_called_once()
            self.assertEqual(run_turn.call_args.args[3], "operation-99")
            transcript = Path(project["workspace"]) / "telegram-transcript.jsonl"
            record = json.loads(transcript.read_text(encoding="utf-8"))
            self.assertEqual(record["text"], "Сделай задачу")
            self.assertEqual(record["answer"], "Готово")

    def test_large_prompt_is_forwarded_without_bridge_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            application = bridge.BridgeApplication(test_config(root))
            text = "x" * (70 * 1024)
            with mock.patch.object(
                application.runner,
                "run_turn",
                return_value={
                    "message": "ok",
                    "session_id": "session-large",
                    "session_path": "/sessions/session-large.jsonl",
                },
            ) as run_turn:
                application.message({"chat_id": 1, "text": text})

            self.assertEqual(run_turn.call_args.args[2], text)


if __name__ == "__main__":
    unittest.main()
