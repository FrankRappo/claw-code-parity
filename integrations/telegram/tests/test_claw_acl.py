"""Per-account Claw agent ACL tests (dependency-free, mocked).

Loads and reloads the bot module with explicit CLAW_ALLOWED_* environment so
the rest of the suite keeps running with the gate unset.
"""

import importlib
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SAVED = {key: os.environ.get(key) for key in ("CLAW_ALLOWED_USER_IDS", "CLAW_ALLOWED_USERNAMES")}


def load_bot(user_ids="", usernames=""):
    os.environ["CLAW_ALLOWED_USER_IDS"] = user_ids
    os.environ["CLAW_ALLOWED_USERNAMES"] = usernames
    try:
        sys.modules.pop("telegram_claw_bot", None)
        return importlib.import_module("telegram_claw_bot")
    finally:
        for key, value in SAVED.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class ClawAllowlistTest(unittest.TestCase):
    def setUp(self):
        self.bot = load_bot("123456789", "someone")

    def tearDown(self):
        load_bot("", "")

    def test_allowed_by_id(self):
        self.assertTrue(self.bot.claw_allowed_user({"id": 123456789, "username": None}))

    def test_allowed_by_username_case_insensitive(self):
        self.assertTrue(self.bot.claw_allowed_user({"id": 1, "username": "SomeOne"}))
        self.assertTrue(self.bot.claw_allowed_user({"id": 1, "username": "@someone"}))

    def test_denied_for_other_allowed_account(self):
        self.assertFalse(self.bot.claw_allowed_user({"id": 42, "username": "other"}))
        self.assertFalse(self.bot.claw_allowed_user({"id": 42, "username": None}))

    def test_unset_allows_everyone(self):
        bot = load_bot("", "")
        self.assertTrue(bot.claw_allowed_user({"id": 42, "username": "other"}))

    def run_message(self, chat_id, user, text, mode=None):
        sent = []
        self.bot.CHAT_MODES.pop(chat_id, None)
        if mode:
            self.bot.CHAT_MODES[chat_id] = mode
        original_send = self.bot.send_message
        original_llm = self.bot.llm_answer
        original_claw = self.bot.claw_request
        self.bot.send_message = lambda cid, text, reply_to=None, keyboard=True: sent.append(
            (cid, text)
        )
        self.bot.llm_answer = lambda cid, user_text, image=None, image_mime_type=None: "ok"
        self.bot.claw_request = lambda path, payload, timeout=5: {"ok": True}
        try:
            self.bot.handle_message(
                {"chat": {"id": chat_id}, "from": user, "message_id": 1, "text": text}
            )
        finally:
            self.bot.send_message = original_send
            self.bot.llm_answer = original_llm
            self.bot.claw_request = original_claw
        return sent

    BLOCK = "Claw-агент отключён"
    USER = {"id": 42, "username": "other"}

    def test_gate_blocks_claw_command(self):
        sent = self.run_message(100, self.USER, "/claw")
        self.assertEqual(len(sent), 1)
        self.assertIn(self.BLOCK, sent[0][1])

    def test_gate_blocks_claw_keyboard_buttons(self):
        for text in ("🛠 Claw Cod", "🆕 Новый проект", "📁 Проекты"):
            with self.subTest(text=text):
                sent = self.run_message(102, self.USER, text)
                self.assertTrue(sent)
                self.assertIn(self.BLOCK, sent[0][1])

    def test_gate_silent_for_ordinary_gemma_message(self):
        sent = self.run_message(101, self.USER, "hello")
        self.assertTrue(all(self.BLOCK not in text for _, text in sent))

    def test_gemma_button_escapes_stuck_claw_mode(self):
        sent = self.run_message(103, self.USER, "💬 Gemma", mode="claw")
        self.assertTrue(all(self.BLOCK not in text for _, text in sent))
        self.assertEqual(self.bot.CHAT_MODES[103], "gemma")

    def test_gemma_command_escapes_stuck_claw_mode(self):
        sent = self.run_message(104, self.USER, "/gemma", mode="claw")
        self.assertTrue(all(self.BLOCK not in text for _, text in sent))
        self.assertEqual(self.bot.CHAT_MODES[104], "gemma")

    def test_reset_button_passes_in_claw_mode(self):
        sent = self.run_message(105, self.USER, "🧹 Сбросить чат Gemma", mode="claw")
        self.assertTrue(all(self.BLOCK not in text for _, text in sent))

    def test_plain_task_blocked_in_claw_mode(self):
        sent = self.run_message(106, self.USER, "сделай задачу", mode="claw")
        self.assertTrue(sent)
        self.assertIn(self.BLOCK, sent[0][1])


if __name__ == "__main__":
    unittest.main()
