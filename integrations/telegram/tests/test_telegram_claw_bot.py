import io
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from integrations.telegram import telegram_claw_bot as bot


PNG = b"\x89PNG\r\n\x1a\n" + b"test-png"
JPEG = b"\xff\xd8\xff" + b"test-jpeg"


class FakeResponse:
    def __init__(self, data, content_length=None):
        self._stream = io.BytesIO(data)
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = str(content_length)

    def read(self, size=-1):
        return self._stream.read(size)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class TelegramTransportTests(unittest.TestCase):
    def test_local_http_uses_direct_opener(self):
        with mock.patch.object(
            bot.DIRECT_OPENER, "open", return_value=FakeResponse(b'{"status":"ok"}')
        ) as opened:
            result = bot.http_json("http://127.0.0.1:18080/health")
        self.assertEqual(result, {"status": "ok"})
        opened.assert_called_once()

    def test_telegram_uses_explicit_opener(self):
        with mock.patch.object(bot, "http_json", return_value={"ok": True}) as request:
            bot.tg("getMe")
        self.assertIs(request.call_args.kwargs["opener"], bot.TELEGRAM_OPENER)

    def test_invalid_socks_url_fails_closed(self):
        for url in ("socks5://127.0.0.1:11080", "socks5h://example.com:11080"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                bot.build_telegram_opener(url)

    @unittest.skipUnless(
        importlib.util.find_spec("socks") and importlib.util.find_spec("sockshandler"),
        "PySocks is not installed",
    )
    def test_socks_opener_uses_remote_dns(self):
        import socks
        from sockshandler import SocksiPyHandler

        opener = bot.build_telegram_opener("socks5h://127.0.0.1:11080")
        handler = next(h for h in opener.handlers if isinstance(h, SocksiPyHandler))
        self.assertIsNot(opener, bot.DIRECT_OPENER)
        self.assertEqual(handler.args, (socks.SOCKS5, "127.0.0.1", 11080))
        self.assertTrue(handler.kw["rdns"])


class ImageSelectionTests(unittest.TestCase):
    def test_selects_largest_photo_variant(self):
        attachment = bot.select_image_attachment({
            "photo": [
                {"file_id": "small", "file_size": 10},
                {"file_id": "large", "file_size": 30},
                {"file_id": "medium", "file_size": 20},
            ]
        })
        self.assertEqual(attachment["file_id"], "large")
        self.assertEqual(attachment["declared_mime_type"], "image/jpeg")

    def test_rejects_non_image_document(self):
        with self.assertRaisesRegex(bot.ImageInputError, "PNG и JPEG"):
            bot.select_image_attachment({
                "document": {
                    "file_id": "pdf",
                    "mime_type": "application/pdf",
                    "file_size": 10,
                }
            })

    def test_rejects_declared_oversize_attachment(self):
        with mock.patch.object(bot, "MAX_IMAGE_BYTES", 8):
            with self.assertRaisesRegex(bot.ImageInputError, "слишком большое"):
                bot.select_image_attachment({
                    "document": {
                        "file_id": "large",
                        "mime_type": "image/png",
                        "file_size": 9,
                    }
                })


class ImageDownloadTests(unittest.TestCase):
    def test_content_length_limit_is_enforced_before_read(self):
        response = FakeResponse(b"", content_length=5)
        with mock.patch.object(
            bot.TELEGRAM_OPENER,
            "open",
            return_value=response,
        ):
            with self.assertRaisesRegex(bot.ImageInputError, "слишком большое"):
                bot._download_limited("https://example.invalid/image", max_bytes=4)

    def test_stream_limit_applies_without_content_length(self):
        with mock.patch.object(
            bot.TELEGRAM_OPENER,
            "open",
            return_value=FakeResponse(b"12345"),
        ):
            with self.assertRaisesRegex(bot.ImageInputError, "слишком большое"):
                bot._download_limited("https://example.invalid/image", max_bytes=4)

    def test_download_validates_signature_and_returns_actual_type(self):
        attachment = {
            "file_id": "telegram-file",
            "declared_mime_type": "image/png",
            "kind": "document",
        }
        with mock.patch.object(
            bot,
            "tg",
            return_value={"result": {"file_path": "documents/screenshot.png"}},
        ), mock.patch.object(bot, "_download_limited", return_value=PNG):
            data, mime_type = bot.download_telegram_image(attachment)
        self.assertEqual(data, PNG)
        self.assertEqual(mime_type, "image/png")

    def test_rejects_declared_and_actual_type_mismatch(self):
        attachment = {
            "file_id": "telegram-file",
            "declared_mime_type": "image/png",
            "kind": "document",
        }
        with mock.patch.object(
            bot,
            "tg",
            return_value={"result": {"file_path": "documents/screenshot.png"}},
        ), mock.patch.object(bot, "_download_limited", return_value=JPEG):
            with self.assertRaisesRegex(bot.ImageInputError, "не совпадает"):
                bot.download_telegram_image(attachment)


class LlmPayloadTests(unittest.TestCase):
    def setUp(self):
        bot.HISTORY.clear()
        bot.CLAW_OPERATION_EPOCHS.clear()
        bot.ACTIVE_CLAW_OPERATIONS.clear()
        bot.ACTIVE_VISION_PROCESSES.clear()

    def test_multimodal_payload_places_image_first_and_does_not_store_base64(self):
        captured = {}

        def fake_http_json(url, payload=None, timeout=None):
            captured["url"] = url
            captured["payload"] = payload
            return {"choices": [{"message": {"content": "На скриншоте код 42."}}]}

        with mock.patch.object(bot, "http_json", side_effect=fake_http_json):
            answer = bot.llm_answer(
                100,
                "Какой код?",
                image=PNG,
                image_mime_type="image/png",
            )

        self.assertEqual(answer, "На скриншоте код 42.")
        content = captured["payload"]["messages"][-1]["content"]
        self.assertEqual([part["type"] for part in content], ["image_url", "text"])
        self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(content[1]["text"], "Какой код?")
        self.assertIsInstance(bot.HISTORY[100][0]["content"], str)
        self.assertNotIn("base64", bot.HISTORY[100][0]["content"])

    def test_text_payload_remains_plain_string(self):
        captured = {}

        def fake_http_json(url, payload=None, timeout=None):
            captured["payload"] = payload
            return {"choices": [{"message": {"content": "Ответ"}}]}

        with mock.patch.object(bot, "http_json", side_effect=fake_http_json):
            bot.llm_answer(101, "Обычный вопрос")
        self.assertEqual(
            captured["payload"]["messages"][-1]["content"],
            "Обычный вопрос",
        )
        self.assertEqual(captured["payload"]["top_p"], bot.TOP_P)
        self.assertEqual(captured["payload"]["top_k"], bot.TOP_K)
        self.assertEqual(
            captured["payload"]["chat_template_kwargs"]["enable_thinking"],
            bot.ENABLE_THINKING,
        )

    def test_llm_waits_for_backend_health_after_reboot(self):
        unavailable = bot.urllib.error.URLError(ConnectionRefusedError("offline"))
        completion = {
            "choices": [{"message": {"content": "Сервис восстановлен"}}],
            "usage": {},
        }
        with mock.patch.object(
            bot,
            "http_json",
            side_effect=[unavailable, {"status": "ok"}, completion],
        ) as request, mock.patch.object(
            bot,
            "LLM_RECOVERY_WAIT_SECONDS",
            10.0,
        ), mock.patch.object(
            bot,
            "LLM_RECOVERY_POLL_SECONDS",
            2.0,
        ), mock.patch.object(
            bot.time,
            "monotonic",
            side_effect=[0.0, 0.0, 2.0, 3.0],
        ), mock.patch.object(bot.time, "sleep") as sleep:
            content, _, _ = bot.llm_chat(
                [{"role": "user", "content": "Проверка после reboot"}],
                64,
            )

        self.assertEqual(content, "Сервис восстановлен")
        self.assertEqual(
            [call.args[0] for call in request.call_args_list],
            [
                f"{bot.LLM_BASE_URL}/health",
                f"{bot.LLM_BASE_URL}/health",
                f"{bot.LLM_BASE_URL}/v1/chat/completions",
            ],
        )
        self.assertIsNone(request.call_args_list[0].kwargs.get("payload"))
        self.assertIsNone(request.call_args_list[1].kwargs.get("payload"))
        sleep.assert_called_once_with(2.0)

    def test_llm_health_wait_does_not_retry_permanent_http_error(self):
        error = bot.urllib.error.HTTPError(
            f"{bot.LLM_BASE_URL}/health",
            404,
            "Not Found",
            {},
            io.BytesIO(b"not found"),
        )
        with mock.patch.object(bot, "http_json", side_effect=error) as request, mock.patch.object(
            bot.time, "sleep"
        ) as sleep:
            with self.assertRaises(bot.urllib.error.HTTPError):
                bot.llm_chat([{"role": "user", "content": "test"}], 64)

        request.assert_called_once_with(
            f"{bot.LLM_BASE_URL}/health",
            timeout=bot.LLM_RECOVERY_PROBE_TIMEOUT,
        )
        sleep.assert_not_called()

    def test_llm_health_wait_stops_at_the_recovery_deadline(self):
        unavailable = bot.urllib.error.URLError(ConnectionRefusedError("offline"))
        with mock.patch.object(
            bot,
            "http_json",
            side_effect=[unavailable, unavailable],
        ) as request, mock.patch.object(
            bot,
            "LLM_RECOVERY_WAIT_SECONDS",
            3.0,
        ), mock.patch.object(
            bot,
            "LLM_RECOVERY_POLL_SECONDS",
            5.0,
        ), mock.patch.object(
            bot.time,
            "monotonic",
            side_effect=[0.0, 0.0, 1.0, 3.0],
        ), mock.patch.object(bot.time, "sleep") as sleep:
            with self.assertRaises(bot.urllib.error.URLError):
                bot.llm_chat([{"role": "user", "content": "test"}], 64)

        self.assertEqual(request.call_count, 2)
        sleep.assert_called_once_with(2.0)

    def test_claw_vision_preprocessing_has_no_automatic_timeout(self):
        process = mock.Mock()
        process.communicate.return_value = (
            json.dumps(
                {"content": "vision result", "usage": {}, "elapsed": 0.1}
            ),
            "",
        )
        process.returncode = 0
        with mock.patch.object(bot.subprocess, "Popen", return_value=process) as popen:
            result = bot.claw_vision_context(
                100, 0, "operation-1", "inspect", PNG, "image/png"
            )

        self.assertEqual(result, "vision result")
        self.assertEqual(popen.call_args.args[0][-1], "--claw-vision-worker")
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        request = json.loads(process.communicate.call_args.args[0])
        self.assertEqual(request["user_text"], "inspect")
        self.assertEqual(request["mime_type"], "image/png")
        self.assertEqual(process.communicate.call_args.kwargs["timeout"], None)

    def test_stop_terminates_vision_and_invalidates_pending_local_work(self):
        process = mock.Mock()
        process.pid = 1234
        process.poll.return_value = None
        process.wait.return_value = None
        bot.ACTIVE_CLAW_OPERATIONS[100] = {"operation-1"}
        bot.ACTIVE_VISION_PROCESSES[100] = {"operation-1": process}

        with mock.patch.object(bot.os, "killpg") as killpg:
            operation_ids, vision_stopped = bot.cancel_claw_operation(100)

        self.assertEqual(operation_ids, ("operation-1",))
        self.assertTrue(vision_stopped)
        self.assertEqual(bot.CLAW_OPERATION_EPOCHS[100], 1)
        killpg.assert_called_once_with(1234, bot.signal.SIGTERM)
        with self.assertRaises(bot.ClawOperationStopped):
            bot.ensure_claw_operation_active(100, 0)

    def test_system_prompt_file_is_preferred_and_hot_reloaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "system-prompt.txt"
            path.write_text("deployment prompt v1", encoding="utf-8")
            with mock.patch.object(bot, "SYSTEM_PROMPT_FILE", str(path)), mock.patch.object(
                bot,
                "SYSTEM_PROMPT",
                "legacy fallback",
            ):
                self.assertEqual(bot.load_system_prompt(), "deployment prompt v1")
                path.write_text("deployment prompt v2", encoding="utf-8")
                self.assertEqual(bot.load_system_prompt(), "deployment prompt v2")

    def test_system_prompt_environment_is_fallback_for_missing_file(self):
        with mock.patch.object(bot, "SYSTEM_PROMPT_FILE", "/missing/prompt.txt"), mock.patch.object(
            bot,
            "SYSTEM_PROMPT",
            "legacy fallback",
        ):
            self.assertEqual(bot.load_system_prompt(), "legacy fallback")


class ExistingBotBehaviorTests(unittest.TestCase):
    def setUp(self):
        bot.HISTORY.clear()
        bot.TOKEN_LIMITS.clear()
        bot.CHAT_MODES.clear()
        bot.LONG_QUESTIONS.clear()
        bot.CLAW_OPERATION_EPOCHS.clear()
        bot.ACTIVE_CLAW_OPERATIONS.clear()
        bot.ACTIVE_VISION_PROCESSES.clear()

    def test_username_allowlist_is_preserved(self):
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot,
            "ALLOWED_USERNAMES",
            {"owner"},
        ):
            self.assertTrue(bot.allowed_user({"id": 100, "username": "@Owner"}))
            self.assertFalse(bot.allowed_user({"id": 101, "username": "guest"}))

    def test_tokens_command_updates_only_that_chat(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "/tokens 768",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot,
            "ALLOWED_USERNAMES",
            set(),
        ), mock.patch.object(bot, "send_message") as send:
            bot.handle_message(message)
        self.assertEqual(bot.chat_token_limit(10), 768)
        self.assertEqual(bot.chat_token_limit(11), bot.MAX_TOKENS)
        self.assertIn("768", send.call_args.args[1])

    def test_help_advertises_screenshot_support(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "/help",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot,
            "ALLOWED_USERNAMES",
            set(),
        ), mock.patch.object(bot, "send_message") as send:
            bot.handle_message(message)
        self.assertIn("PNG/JPEG", send.call_args.args[1])

    def test_long_response_is_sent_in_order_without_character_loss(self):
        text = ("Первый абзац.\n" * 400) + ("X" * 4200) + "\nКонец"
        with mock.patch.object(bot, "tg") as telegram:
            bot.send_message(10, text, reply_to=30)

        payloads = [call.args[1] for call in telegram.call_args_list]
        self.assertGreater(len(payloads), 1)
        self.assertEqual("".join(payload["text"] for payload in payloads), text)
        self.assertTrue(all(len(payload["text"]) <= 3900 for payload in payloads))
        self.assertEqual(payloads[0]["reply_parameters"], {"message_id": 30})
        self.assertTrue(
            all("reply_parameters" not in payload for payload in payloads[1:])
        )
        self.assertTrue(
            all("reply_markup" not in payload for payload in payloads[:-1])
        )
        self.assertIn("reply_markup", payloads[-1])

    def test_long_question_parts_are_compiled_in_message_order_for_claw(self):
        bot.CHAT_MODES[10] = bot.MODE_CLAW

        def message(message_id, text):
            return {
                "chat": {"id": 10},
                "from": {"id": 20},
                "message_id": message_id,
                "text": text,
            }

        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(bot, "_persist_long_questions"):
            started = bot.prepare_long_question_message(
                message(100, "/long /status должен быть частью вопроса")
            )
            later_part = bot.prepare_long_question_message(
                message(102, "Третья часть")
            )
            earlier_part = bot.prepare_long_question_message(
                message(101, "Вторая часть")
            )
            compiled = bot.prepare_long_question_message(message(103, "/send"))

        self.assertEqual(started["_long_question_action"], "started")
        self.assertEqual(later_part["_long_question_action"], "part_added")
        self.assertEqual(earlier_part["_long_question_action"], "part_added")
        self.assertTrue(compiled["_long_question_compiled"])
        self.assertEqual(
            compiled["text"],
            "/status должен быть частью вопроса\nВторая часть\nТретья часть",
        )
        self.assertNotIn(10, bot.LONG_QUESTIONS)

        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(bot, "send_typing"), mock.patch.object(
            bot, "claw_answer", return_value="Готово"
        ) as answer, mock.patch.object(bot, "send_message") as send:
            bot.handle_message(compiled)

        answer.assert_called_once()
        self.assertEqual(answer.call_args.args[:3], (10, 20, compiled["text"]))
        self.assertEqual(send.call_args.args[1], "Готово")

    def test_long_question_rejects_only_the_part_over_total_limit(self):
        def message(message_id, text):
            return {
                "chat": {"id": 10},
                "from": {"id": 20},
                "message_id": message_id,
                "text": text,
            }

        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(bot, "MAX_LONG_QUESTION_CHARS", 12), mock.patch.object(
            bot, "_persist_long_questions"
        ):
            bot.prepare_long_question_message(message(100, "/long 123456"))
            rejected = bot.prepare_long_question_message(message(101, "789012"))
            compiled = bot.prepare_long_question_message(message(102, "/send"))

        self.assertEqual(rejected["_long_question_action"], "too_large")
        self.assertEqual(compiled["text"], "123456")

    def test_long_question_can_be_cancelled_without_model_request(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 100,
            "text": "/long первая часть",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(bot, "_persist_long_questions"):
            bot.prepare_long_question_message(message)
            cancelled = bot.prepare_long_question_message(
                {**message, "message_id": 101, "text": "/cancellong"}
            )

        self.assertEqual(cancelled["_long_question_action"], "cancelled")
        self.assertNotIn(10, bot.LONG_QUESTIONS)

    def test_long_question_does_not_capture_bot_commands_or_keyboard_actions(self):
        def message(message_id, text):
            return {
                "chat": {"id": 10},
                "from": {"id": 20},
                "message_id": message_id,
                "text": text,
            }

        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(bot, "_persist_long_questions"):
            bot.prepare_long_question_message(message(100, "/long первая часть"))
            status = bot.prepare_long_question_message(message(101, "/status"))
            stop = bot.prepare_long_question_message(message(102, "⛔ Остановить Claw"))
            compiled = bot.prepare_long_question_message(message(103, "/send"))

        self.assertNotIn("_long_question_action", status)
        self.assertNotIn("_long_question_action", stop)
        self.assertEqual(compiled["text"], "первая часть")

    def test_long_question_draft_is_loaded_from_private_state_file(self):
        with tempfile.TemporaryDirectory() as directory:
            state_file = Path(directory) / "long-questions.json"
            with mock.patch.object(bot, "LONG_QUESTION_STATE_FILE", state_file):
                bot.LONG_QUESTIONS[10] = {
                    "user_id": 20,
                    "parts": {102: "вторая", 101: "первая"},
                }
                bot._persist_long_questions()
                loaded = bot._load_long_questions()

            self.assertEqual(loaded, bot.LONG_QUESTIONS)
            self.assertEqual(state_file.stat().st_mode & 0o777, 0o600)

    def test_send_message_retries_transient_transport_failure(self):
        with mock.patch.object(
            bot,
            "tg",
            side_effect=[TimeoutError("temporary timeout"), {"ok": True}],
        ) as telegram, mock.patch.object(bot.time, "sleep") as sleep:
            bot.send_message(10, "Статус Claw")

        self.assertEqual(telegram.call_count, 2)
        sleep.assert_called_once_with(bot.TELEGRAM_SEND_RETRY_BASE_SECONDS)

    def test_send_message_does_not_retry_non_retryable_http_error(self):
        error = bot.urllib.error.HTTPError(
            "https://api.telegram.org/redacted",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"ok":false}'),
        )
        with mock.patch.object(bot, "tg", side_effect=error) as telegram, mock.patch.object(
            bot.time, "sleep"
        ) as sleep:
            with self.assertRaises(bot.urllib.error.HTTPError):
                bot.send_message(10, "Некорректный запрос")

        telegram.assert_called_once()
        sleep.assert_not_called()

    def test_send_message_honors_telegram_retry_after(self):
        error = bot.urllib.error.HTTPError(
            "https://api.telegram.org/redacted",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(
                b'{"ok":false,"parameters":{"retry_after":7}}'
            ),
        )
        with mock.patch.object(
            bot,
            "tg",
            side_effect=[error, {"ok": True}],
        ) as telegram, mock.patch.object(bot.time, "sleep") as sleep:
            bot.send_message(10, "Статус Claw")

        self.assertEqual(telegram.call_count, 2)
        sleep.assert_called_once_with(7.0)

    def test_tokens_command_allows_long_multi_message_answers(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "/tokens 999999",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot,
            "ALLOWED_USERNAMES",
            set(),
        ), mock.patch.object(bot, "send_message"):
            bot.handle_message(message)
        self.assertEqual(bot.chat_token_limit(10), bot.MAX_RESPONSE_TOKENS)

    def test_permissions_command_explains_sandbox_auto_approval(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "/permissions",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot,
            "ALLOWED_USERNAMES",
            set(),
        ), mock.patch.object(bot, "send_message") as send:
            bot.handle_message(message)
        self.assertIn("danger-full-access", send.call_args.args[1])
        self.assertIn("автоматически", send.call_args.args[1])


class MessageHandlingTests(unittest.TestCase):
    def setUp(self):
        bot.HISTORY.clear()
        bot.CHAT_MODES.clear()
        bot.CLAW_OPERATION_EPOCHS.clear()
        bot.ACTIVE_CLAW_OPERATIONS.clear()
        bot.ACTIVE_VISION_PROCESSES.clear()

    def test_photo_without_caption_uses_default_prompt(self):
        message = {
            "chat": {"id": 1},
            "from": {"id": 2},
            "message_id": 3,
            "photo": [{"file_id": "photo", "file_size": len(JPEG)}],
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot,
            "send_typing",
        ), mock.patch.object(
            bot,
            "download_telegram_image",
            return_value=(JPEG, "image/jpeg"),
        ) as download, mock.patch.object(
            bot,
            "llm_answer",
            return_value="Описание",
        ) as answer, mock.patch.object(bot, "send_message") as send:
            bot.handle_message(message)

        download.assert_called_once()
        answer.assert_called_once_with(
            1,
            "",
            image=JPEG,
            image_mime_type="image/jpeg",
        )
        send.assert_called_once_with(1, "Описание", reply_to=3)

    def test_unauthorized_image_is_not_downloaded(self):
        message = {
            "chat": {"id": 1},
            "from": {"id": 99},
            "message_id": 3,
            "photo": [{"file_id": "photo", "file_size": len(JPEG)}],
        }
        with mock.patch.object(bot, "ALLOWED", {2}), mock.patch.object(
            bot,
            "download_telegram_image",
        ) as download, mock.patch.object(bot, "send_message"):
            bot.handle_message(message)
        download.assert_not_called()

    def test_unsupported_document_gets_safe_validation_message(self):
        message = {
            "chat": {"id": 1},
            "from": {"id": 2},
            "message_id": 3,
            "document": {
                "file_id": "pdf",
                "file_size": 50,
                "mime_type": "application/pdf",
            },
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot,
            "download_telegram_image",
        ) as download, mock.patch.object(bot, "send_message") as send:
            bot.handle_message(message)
        download.assert_not_called()
        self.assertIn("PNG и JPEG", send.call_args.args[1])


class ClawModeTests(unittest.TestCase):
    def setUp(self):
        bot.HISTORY.clear()
        bot.CHAT_MODES.clear()
        bot.CLAW_OPERATION_EPOCHS.clear()
        bot.ACTIVE_CLAW_OPERATIONS.clear()
        bot.ACTIVE_VISION_PROCESSES.clear()

    def test_keyboard_has_project_controls_and_no_ocr_menu(self):
        labels = [
            button["text"]
            for row in bot.main_keyboard()["keyboard"]
            for button in row
        ]
        self.assertIn("🆕 Новый проект", labels)
        self.assertIn("⛔ Остановить Claw", labels)
        self.assertIn("📊 Ход работы", labels)
        self.assertFalse(any("OCR" in label.upper() for label in labels))

    def test_claw_accepts_pdf_while_gemma_selector_rejects_it(self):
        message = {
            "document": {
                "file_id": "pdf",
                "mime_type": "application/pdf",
                "file_size": 100,
                "file_name": "scan.pdf",
            }
        }
        attachment = bot.select_claw_attachment(message)
        self.assertEqual(attachment["declared_mime_type"], "application/pdf")
        with self.assertRaises(bot.ImageInputError):
            bot.select_image_attachment(message)

    def test_switch_to_claw_checks_bridge_then_persists_mode(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "/claw",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(
            bot, "claw_status_text", return_value="Claw: готов"
        ), mock.patch.object(
            bot, "_persist_chat_modes"
        ), mock.patch.object(bot, "send_message") as send:
            bot.handle_message(message)
        self.assertEqual(bot.chat_mode(10), bot.MODE_CLAW)
        self.assertIn("Claw: готов", send.call_args.args[1])

    def test_new_project_selects_claw_mode(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "/newclaw Audit",
        }
        response = {"project": {"id": "abc123", "name": "Audit"}, "ok": True}
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(
            bot, "claw_request", return_value=response
        ) as request, mock.patch.object(
            bot, "_persist_chat_modes"
        ), mock.patch.object(bot, "send_message"):
            bot.handle_message(message)
        request.assert_called_once_with(
            "/v1/projects/new", {"chat_id": 10, "name": "Audit"}, timeout=30
        )
        self.assertEqual(bot.chat_mode(10), bot.MODE_CLAW)

    def test_new_project_button_opens_optional_force_reply_name_prompt(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "🆕 Новый проект",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(bot, "claw_request") as request, mock.patch.object(
            bot, "send_message"
        ) as send:
            bot.handle_message(message)

        request.assert_not_called()
        self.assertIn("название новой Claw-сессии", send.call_args.args[1])
        self.assertFalse(send.call_args.kwargs["keyboard"])
        self.assertTrue(send.call_args.kwargs["reply_markup"]["force_reply"])

    def test_reply_to_name_prompt_creates_named_session(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 31,
            "text": "Аудит инфраструктуры",
            "reply_to_message": {
                "text": bot.NEW_CLAW_NAME_PROMPT,
                "from": {"id": 999, "is_bot": True},
            },
        }
        response = {
            "project": {"id": "abc123", "name": "Аудит инфраструктуры"},
            "ok": True,
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(
            bot, "claw_request", return_value=response
        ) as request, mock.patch.object(
            bot, "_persist_chat_modes"
        ), mock.patch.object(bot, "send_message"):
            bot.handle_message(message)

        request.assert_called_once_with(
            "/v1/projects/new",
            {"chat_id": 10, "name": "Аудит инфраструктуры"},
            timeout=30,
        )
        self.assertEqual(bot.chat_mode(10), bot.MODE_CLAW)
        self.assertTrue(bot.is_control_message(message))

    def test_name_prompt_can_be_cancelled_without_changing_project(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 31,
            "text": "/cancel",
            "reply_to_message": {
                "text": bot.NEW_CLAW_NAME_PROMPT,
                "from": {"id": 999, "is_bot": True},
            },
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(bot, "claw_request") as request, mock.patch.object(
            bot, "send_message"
        ) as send:
            bot.handle_message(message)

        request.assert_not_called()
        self.assertIn("отменено", send.call_args.args[1])

    def test_claw_image_is_forwarded_without_separate_ocr_command(self):
        bot.CHAT_MODES[1] = bot.MODE_CLAW
        message = {
            "chat": {"id": 1},
            "from": {"id": 2},
            "message_id": 3,
            "caption": "Прочитай номер",
            "photo": [{"file_id": "photo", "file_size": len(JPEG)}],
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "send_typing"
        ), mock.patch.object(
            bot,
            "download_telegram_attachment",
            return_value=(JPEG, "image/jpeg"),
        ), mock.patch.object(
            bot, "claw_answer", return_value="Номер 4827"
        ) as answer, mock.patch.object(bot, "send_message") as send:
            bot.handle_message(message)
        answer.assert_called_once()
        self.assertEqual(answer.call_args.args[:3], (1, 2, "Прочитай номер"))
        self.assertEqual(answer.call_args.kwargs["attachment_data"], JPEG)
        self.assertIn("4827", send.call_args.args[1])

    def test_stop_uses_control_executor(self):
        self.assertTrue(bot.is_control_message({"text": "/stop"}))
        self.assertTrue(bot.is_control_message({"text": "/newclaw Новый"}))
        self.assertTrue(bot.is_control_message({"text": "/progress"}))
        self.assertTrue(bot.is_control_message({"text": "/status"}))
        self.assertTrue(bot.is_control_message({"text": "📊 Ход работы"}))
        self.assertFalse(bot.is_control_message({"text": "обычная задача"}))

    def test_progress_text_reports_tool_agent_and_stall_health(self):
        response = {
            "ok": True,
            "running": True,
            "health": "possibly_stalled",
            "elapsed_seconds": 367,
            "last_activity_seconds": 301,
            "phase": "tool",
            "tool_name": "Bash",
            "detail": "shell: apt-get install",
            "agents": [
                {
                    "agent_id": "agent-1",
                    "status": "running",
                    "description": "Проверить сайт",
                }
            ],
        }
        with mock.patch.object(bot, "claw_request", return_value=response) as request:
            text = bot.claw_progress_text(10)

        request.assert_called_once_with("/v1/progress", {"chat_id": 10}, timeout=15)
        self.assertIn("возможно завис", text)
        self.assertIn("Bash", text)
        self.assertIn("apt-get install", text)
        self.assertIn("Проверить сайт", text)
        self.assertIn("6м 7с", text)

    def test_regular_claw_message_steers_the_active_turn(self):
        bot.CHAT_MODES[10] = bot.MODE_CLAW
        bot.ACTIVE_CLAW_OPERATIONS[10] = {"operation-1"}
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "Ещё один вопрос",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(bot, "claw_answer") as answer, mock.patch.object(
            bot,
            "claw_request",
            return_value={"ok": True, "accepted": True, "interrupted": True},
        ) as request, mock.patch.object(
            bot, "send_message"
        ) as send:
            bot.handle_message(message)

        answer.assert_not_called()
        request.assert_called_once_with(
            "/v1/steer",
            {"chat_id": 10, "text": "Ещё один вопрос", "message_id": 30},
            timeout=15,
        )
        self.assertIn("Уточнение принято", send.call_args.args[1])

    def test_natural_progress_question_does_not_interrupt_the_active_turn(self):
        progress = {
            "ok": True,
            "running": True,
            "health": "working",
            "elapsed_seconds": 73,
            "last_activity_seconds": 4,
            "phase": "tool",
            "tool_name": "Bash",
            "detail": "проверяет сетевую конфигурацию",
            "agents": [],
            "observer_context": {
                "plan": {
                    "completed": 1,
                    "total": 3,
                    "current": "Проверить маршрутизацию",
                },
                "last_tool": "Bash",
            },
        }
        for text in (
            "что ты сейцас делаешь ?",
            "Такинапиши ответь на вопрос на чем остановился",
            "ты не повис?",
            "опиши эту задачу",
            "расскажи о текущей работе",
        ):
            with self.subTest(text=text):
                bot.CHAT_MODES[10] = bot.MODE_CLAW
                bot.ACTIVE_CLAW_OPERATIONS[10] = {"operation-1"}
                message = {
                    "chat": {"id": 10},
                    "from": {"id": 20},
                    "message_id": 30,
                    "text": text,
                }
                with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
                    bot, "ALLOWED_USERNAMES", set()
                ), mock.patch.object(bot, "claw_answer") as answer, mock.patch.object(
                    bot, "claw_request", return_value=progress
                ) as request, mock.patch.object(
                    bot, "observer_slot_available", return_value=True
                ), mock.patch.object(
                    bot,
                    "llm_chat",
                    return_value=(
                        "Сейчас я проверяю маршрутизацию через Bash; выполнен 1 из 3 пунктов.",
                        {},
                        0.1,
                    ),
                ) as observer, mock.patch.object(bot, "send_message") as send:
                    bot.handle_message(message)

                answer.assert_not_called()
                request.assert_called_once_with(
                    "/v1/progress", {"chat_id": 10}, timeout=15
                )
                observer.assert_called_once()
                self.assertIn("Сейчас я проверяю маршрутизацию", send.call_args.args[1])
                self.assertNotIn("Уточнение принято", send.call_args.args[1])

        self.assertFalse(bot.is_claw_progress_question("Опиши конфигурацию Hysteria 2"))

    def test_natural_progress_question_falls_back_when_all_model_slots_are_busy(self):
        bot.CHAT_MODES[10] = bot.MODE_CLAW
        bot.ACTIVE_CLAW_OPERATIONS[10] = {"operation-1"}
        progress = {
            "ok": True,
            "running": True,
            "health": "working",
            "elapsed_seconds": 73,
            "last_activity_seconds": 4,
            "phase": "tool",
            "tool_name": "Bash",
            "detail": "проверяет сетевую конфигурацию",
            "agents": [],
        }
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "что конкретно делаешь?",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(
            bot, "claw_request", return_value=progress
        ) as request, mock.patch.object(
            bot, "observer_slot_available", return_value=False
        ), mock.patch.object(bot, "llm_chat") as observer, mock.patch.object(
            bot, "send_message"
        ) as send:
            bot.handle_message(message)

        request.assert_called_once_with("/v1/progress", {"chat_id": 10}, timeout=15)
        observer.assert_not_called()
        self.assertIn("проверяет сетевую конфигурацию", send.call_args.args[1])
        self.assertIn("слота Gemma заняты", send.call_args.args[1])

    def test_begin_claw_operation_is_atomic_per_chat(self):
        first = bot.begin_claw_operation(10)
        second = bot.begin_claw_operation(10)

        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(bot.ACTIVE_CLAW_OPERATIONS[10]), 1)

    def test_stop_reports_local_vision_cancellation_even_if_bridge_is_idle(self):
        bot.ACTIVE_CLAW_OPERATIONS[10] = {"operation-1"}
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "/stop",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(
            bot, "claw_request", return_value={"ok": True, "stopped": False}
        ) as request, mock.patch.object(bot, "send_message") as send:
            bot.handle_message(message)

        self.assertIn("остановлена", send.call_args.args[1])
        self.assertEqual(bot.CLAW_OPERATION_EPOCHS[10], 1)
        request.assert_called_once_with(
            "/v1/stop",
            {"chat_id": 10, "operation_ids": ("operation-1",)},
            timeout=15,
        )

    def test_continue_restarts_a_durable_inflight_task_after_service_restart(self):
        message = {
            "chat": {"id": 10},
            "from": {"id": 20},
            "message_id": 30,
            "text": "/continue",
        }
        with mock.patch.object(bot, "ALLOWED", set()), mock.patch.object(
            bot, "ALLOWED_USERNAMES", set()
        ), mock.patch.object(
            bot,
            "claw_request",
            return_value={
                "ok": True,
                "resumed": True,
                "live": False,
                "restart_prompt": "recover persisted task",
            },
        ) as request, mock.patch.object(
            bot, "claw_answer", return_value="recovered answer"
        ) as answer, mock.patch.object(bot, "send_message") as send:
            bot.handle_message(message)

        request.assert_called_once_with(
            "/v1/continue", {"chat_id": 10}, timeout=15
        )
        answer.assert_called_once()
        self.assertEqual(answer.call_args.args[:3], (10, 20, "recover persisted task"))
        self.assertTrue(answer.call_args.kwargs["preserve_plan"])
        self.assertEqual(send.call_args.args[1], "recovered answer")
        self.assertNotIn(10, bot.ACTIVE_CLAW_OPERATIONS)


if __name__ == "__main__":
    unittest.main()
