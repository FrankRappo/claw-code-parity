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

    def test_gate_blocks_claw_command(self):
        message = {
            "chat": {"id": 100},
            "from": {"id": 42, "username": "other"},
            "message_id": 5,
            "text": "/claw",
        }
        sent = []
        self.bot.CHAT_MODES.pop(100, None)
        original = self.bot.send_message
        self.bot.send_message = lambda chat_id, text, reply_to=None, keyboard=True: sent.append(
            (chat_id, text)
        )
        try:
            self.bot.handle_message(message)
        finally:
            self.bot.send_message = original
        self.assertEqual(len(sent), 1)
        self.assertIn("Claw", sent[0][1])

    def test_gate_silent_for_ordinary_gemma_message(self):
        message = {
            "chat": {"id": 101},
            "from": {"id": 42, "username": "other"},
            "message_id": 6,
            "text": "hello",
        }
        sent = []
        self.bot.CHAT_MODES.pop(101, None)
        original_send = self.bot.send_message
        self.bot.send_message = lambda chat_id, text, reply_to=None, keyboard=True: sent.append(
            (chat_id, text)
        )
        original_llm = self.bot.llm_answer
        self.bot.llm_answer = lambda chat_id, user_text, image=None, image_mime_type=None: "ok"
        try:
            self.bot.handle_message(message)
        finally:
            self.bot.send_message = original_send
            self.bot.llm_answer = original_llm
        self.assertTrue(all("Claw-агент отключён" not in text for _, text in sent))


if __name__ == "__main__":
    unittest.main()
