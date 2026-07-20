#!/usr/bin/env python3
"""Authenticated localhost bridge between Telegram and persisted Claw projects.

The bridge intentionally starts one Claw process per turn and resumes the same
logical Claw session for the next turn. Completed turns are persisted by Claw,
so a bridge or VM restart does not lose project context. Running processes are
kept in separate process groups so `/stop` can interrupt only the selected chat.
"""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import os
import re
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


JSON_CONTENT_TYPE = "application/json; charset=utf-8"
PROJECT_NAME_LIMIT = 80
PROMPT_LIMIT = 64 * 1024
VISION_CONTEXT_LIMIT = 16 * 1024
OCR_CONTEXT_LIMIT = 24 * 1024
OCR_HINTS = (
    "ocr",
    "текст",
    "прочит",
    "распозн",
    "номер",
    "код",
    "дата",
    "сумм",
    "скрин",
    "документ",
    "ошиб",
    "таблиц",
)
SUPPORTED_ATTACHMENTS = {
    "image/jpeg": (".jpg", b"\xff\xd8\xff"),
    "image/png": (".png", b"\x89PNG\r\n\x1a\n"),
    "application/pdf": (".pdf", b"%PDF-"),
}


class BridgeError(RuntimeError):
    """Safe bridge error carrying an HTTP status code."""

    def __init__(self, message: str, status: int = HTTPStatus.BAD_REQUEST):
        super().__init__(message)
        self.status = int(status)


@dataclass(frozen=True)
class BridgeConfig:
    bind_host: str
    bind_port: int
    bearer_token: str
    state_file: Path
    projects_root: Path
    claw_binary: Path
    model: str
    allowed_tools: str
    permission_mode: str
    turn_timeout: int
    max_concurrent: int
    max_body_bytes: int
    max_attachment_bytes: int
    auto_compact_input_tokens: int
    gemma_base_url: str
    gemma_api_key: str
    gemma_max_output_tokens: int
    ocr_timeout: int
    ocr_languages: str

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        token = os.environ.get("CLAW_BRIDGE_TOKEN", "").strip()
        if len(token) < 32:
            raise SystemExit("CLAW_BRIDGE_TOKEN must contain at least 32 characters")
        return cls(
            bind_host=os.environ.get("CLAW_BRIDGE_HOST", "127.0.0.1"),
            bind_port=int(os.environ.get("CLAW_BRIDGE_PORT", "19090")),
            bearer_token=token,
            state_file=Path(
                os.environ.get(
                    "CLAW_BRIDGE_STATE_FILE",
                    "/home/clawrun/analise_storage/state/telegram-claw-projects.json",
                )
            ),
            projects_root=Path(
                os.environ.get(
                    "CLAW_PROJECTS_ROOT",
                    "/home/clawrun/analise_storage/telegram-projects",
                )
            ),
            claw_binary=Path(
                os.environ.get(
                    "CLAW_BINARY",
                    "/home/clawrun/analise_storage/claw-code-parity/rust/target/release/claw",
                )
            ),
            model=os.environ.get("CLAW_MODEL", "gemma4"),
            allowed_tools=os.environ.get(
                "CLAW_ALLOWED_TOOLS", "read,write,edit,glob,grep,bash"
            ),
            permission_mode=os.environ.get(
                "CLAW_PERMISSION_MODE", "workspace-write"
            ),
            turn_timeout=max(10, int(os.environ.get("CLAW_TURN_TIMEOUT", "900"))),
            max_concurrent=max(1, int(os.environ.get("CLAW_MAX_CONCURRENT", "2"))),
            max_body_bytes=max(
                1024, int(os.environ.get("CLAW_BRIDGE_MAX_BODY_BYTES", str(24 << 20)))
            ),
            max_attachment_bytes=max(
                1024, int(os.environ.get("CLAW_MAX_ATTACHMENT_BYTES", str(20 << 20)))
            ),
            auto_compact_input_tokens=max(
                1, int(os.environ.get("CLAW_AUTO_COMPACT_INPUT_TOKENS", "22000"))
            ),
            gemma_base_url=os.environ.get(
                "GOOGLE_BASE_URL", "http://127.0.0.1:18080/v1"
            ),
            gemma_api_key=os.environ.get("GOOGLE_API_KEY", "local-gemma"),
            gemma_max_output_tokens=max(
                1, int(os.environ.get("CLAW_GEMMA_MAX_OUTPUT_TOKENS", "1024"))
            ),
            ocr_timeout=max(10, int(os.environ.get("CLAW_OCR_TIMEOUT", "180"))),
            ocr_languages=os.environ.get("CLAW_OCR_LANGUAGES", "rus+eng"),
        )


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def validated_chat_id(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError) as error:
        raise BridgeError("chat_id must be an integer") from error


def clean_project_name(value: Any) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if not name:
        name = time.strftime("Проект %Y-%m-%d %H:%M", time.localtime())
    return name[:PROJECT_NAME_LIMIT]


class ProjectStore:
    """Atomic JSON project/session mapping with per-chat project selection."""

    def __init__(self, state_file: Path, projects_root: Path):
        self.state_file = state_file
        self.projects_root = projects_root
        self._lock = threading.RLock()
        self.projects_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._state = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {"version": 1, "chats": {}}
        try:
            value = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise SystemExit(f"cannot load bridge state: {error}") from error
        if value.get("version") != 1 or not isinstance(value.get("chats"), dict):
            raise SystemExit("unsupported or invalid bridge state file")
        return value

    def _save(self) -> None:
        temporary = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        payload = json.dumps(self._state, ensure_ascii=False, indent=2) + "\n"
        temporary.write_text(payload, encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, self.state_file)

    def _chat(self, chat_id: str) -> dict[str, Any]:
        return self._state["chats"].setdefault(
            chat_id, {"active_project_id": None, "projects": {}}
        )

    def new_project(self, chat_id: str, name: Any = None) -> dict[str, Any]:
        with self._lock:
            chat = self._chat(chat_id)
            active_id = chat.get("active_project_id")
            if active_id and active_id in chat["projects"]:
                chat["projects"][active_id]["status"] = "closed"
            project_id = uuid.uuid4().hex[:12]
            workspace = self.projects_root / f"chat-{chat_id}" / project_id
            workspace.mkdir(parents=True, exist_ok=False, mode=0o700)
            now = utc_timestamp()
            project = {
                "id": project_id,
                "name": clean_project_name(name),
                "workspace": str(workspace),
                "session_id": None,
                "session_path": None,
                "status": "open",
                "created_at": now,
                "updated_at": now,
            }
            chat["projects"][project_id] = project
            chat["active_project_id"] = project_id
            self._save()
            return dict(project)

    def active_project(self, chat_id: str, create: bool = False) -> dict[str, Any] | None:
        with self._lock:
            chat = self._chat(chat_id)
            project_id = chat.get("active_project_id")
            if project_id and project_id in chat["projects"]:
                return dict(chat["projects"][project_id])
        return self.new_project(chat_id) if create else None

    def set_session(
        self, chat_id: str, project_id: str, session_id: str, session_path: str
    ) -> dict[str, Any]:
        with self._lock:
            project = self._chat(chat_id)["projects"].get(project_id)
            if project is None:
                raise BridgeError("project no longer exists", HTTPStatus.CONFLICT)
            project["session_id"] = session_id
            project["session_path"] = session_path
            project["status"] = "open"
            project["updated_at"] = utc_timestamp()
            self._save()
            return dict(project)

    def list_projects(self, chat_id: str) -> dict[str, Any]:
        with self._lock:
            chat = self._chat(chat_id)
            projects = sorted(
                (dict(value) for value in chat["projects"].values()),
                key=lambda value: value["created_at"],
                reverse=True,
            )
            return {
                "active_project_id": chat.get("active_project_id"),
                "projects": projects,
            }

    def switch_project(self, chat_id: str, project_id: str) -> dict[str, Any]:
        with self._lock:
            chat = self._chat(chat_id)
            project = chat["projects"].get(project_id)
            if project is None:
                matches = [
                    value
                    for key, value in chat["projects"].items()
                    if key.startswith(project_id)
                ]
                if len(matches) != 1:
                    raise BridgeError("project not found", HTTPStatus.NOT_FOUND)
                project = matches[0]
                project_id = project["id"]
            previous = chat.get("active_project_id")
            if previous and previous in chat["projects"]:
                chat["projects"][previous]["status"] = "closed"
            project["status"] = "open"
            project["updated_at"] = utc_timestamp()
            chat["active_project_id"] = project_id
            self._save()
            return dict(project)

    def close_active(self, chat_id: str) -> dict[str, Any] | None:
        with self._lock:
            chat = self._chat(chat_id)
            project_id = chat.get("active_project_id")
            if not project_id:
                return None
            project = chat["projects"].get(project_id)
            if project:
                project["status"] = "closed"
                project["updated_at"] = utc_timestamp()
            chat["active_project_id"] = None
            self._save()
            return dict(project) if project else None


class ClawRunner:
    """Runs resumable Claw turns and provides targeted process interruption."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self._capacity = threading.BoundedSemaphore(config.max_concurrent)
        self._active_lock = threading.Lock()
        self._active: dict[str, subprocess.Popen[str]] = {}

    def _command(self, project: dict[str, Any], prompt: str) -> list[str]:
        command = [
            str(self.config.claw_binary),
            "--model",
            self.config.model,
            "--output-format",
            "json",
            "--permission-mode",
            self.config.permission_mode,
            "--allowedTools",
            self.config.allowed_tools,
        ]
        if project.get("session_id"):
            # Prefer the exact persisted file. This remains unambiguous even
            # for sessions created by older Claw builds whose process-local ID
            # counters could collide across simultaneous worker processes.
            reference = project.get("session_path") or project["session_id"]
            command.extend(["--resume", reference, "prompt", prompt])
        else:
            command.extend(["prompt", prompt])
        return command

    def run_turn(self, chat_id: str, project: dict[str, Any], prompt: str) -> dict[str, Any]:
        if not self.config.claw_binary.is_file():
            raise BridgeError("Claw binary is unavailable", HTTPStatus.SERVICE_UNAVAILABLE)
        environment = os.environ.copy()
        environment.update(
            {
                "GOOGLE_BASE_URL": self.config.gemma_base_url,
                "GOOGLE_API_KEY": self.config.gemma_api_key,
                "CLAW_GEMMA_MAX_OUTPUT_TOKENS": str(
                    self.config.gemma_max_output_tokens
                ),
                "CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS": str(
                    self.config.auto_compact_input_tokens
                ),
            }
        )
        with self._capacity:
            process = subprocess.Popen(
                self._command(project, prompt),
                cwd=project["workspace"],
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
            with self._active_lock:
                if chat_id in self._active:
                    process.kill()
                    raise BridgeError(
                        "this project already has a running turn", HTTPStatus.CONFLICT
                    )
                self._active[chat_id] = process
            try:
                stdout, stderr = process.communicate(timeout=self.config.turn_timeout)
            except subprocess.TimeoutExpired as error:
                self._terminate(process, signal.SIGINT)
                raise BridgeError(
                    f"Claw turn exceeded {self.config.turn_timeout} seconds",
                    HTTPStatus.GATEWAY_TIMEOUT,
                ) from error
            finally:
                with self._active_lock:
                    self._active.pop(chat_id, None)

        if process.returncode != 0:
            message = stderr.strip() or stdout.strip() or f"exit code {process.returncode}"
            raise BridgeError(f"Claw turn failed: {message[-2000:]}", HTTPStatus.BAD_GATEWAY)
        for line in reversed(stdout.splitlines()):
            try:
                result = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(result, dict) and "message" in result:
                return result
        raise BridgeError("Claw returned no JSON result", HTTPStatus.BAD_GATEWAY)

    def _terminate(self, process: subprocess.Popen[str], first_signal: signal.Signals) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, first_signal)
            process.wait(timeout=3)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=3)
            return
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def stop(self, chat_id: str) -> bool:
        with self._active_lock:
            process = self._active.get(chat_id)
        if process is None or process.poll() is not None:
            return False
        self._terminate(process, signal.SIGINT)
        return True

    def is_running(self, chat_id: str) -> bool:
        with self._active_lock:
            process = self._active.get(chat_id)
            return bool(process and process.poll() is None)


class BridgeApplication:
    def __init__(self, config: BridgeConfig):
        self.config = config
        self.store = ProjectStore(config.state_file, config.projects_root)
        self.runner = ClawRunner(config)
        self._chat_locks_guard = threading.Lock()
        self._chat_locks: dict[str, threading.Lock] = {}

    def chat_lock(self, chat_id: str) -> threading.Lock:
        with self._chat_locks_guard:
            return self._chat_locks.setdefault(chat_id, threading.Lock())

    def save_attachment(self, project: dict[str, Any], value: Any) -> Path | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise BridgeError("attachment must be an object")
        mime_type = str(value.get("mime_type") or "").lower()
        specification = SUPPORTED_ATTACHMENTS.get(mime_type)
        if specification is None:
            raise BridgeError("attachment must be PNG, JPEG, or PDF")
        encoded = value.get("data_base64")
        if not isinstance(encoded, str):
            raise BridgeError("attachment data is missing")
        try:
            data = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise BridgeError("attachment base64 is invalid") from error
        if not data or len(data) > self.config.max_attachment_bytes:
            raise BridgeError(
                f"attachment must be between 1 and {self.config.max_attachment_bytes} bytes"
            )
        suffix, signature = specification
        if not data.startswith(signature):
            raise BridgeError("attachment signature does not match its MIME type")
        directory = Path(project["workspace"]) / "attachments"
        directory.mkdir(mode=0o700, exist_ok=True)
        path = directory / f"{utc_timestamp().replace(':', '')}-{uuid.uuid4().hex[:8]}{suffix}"
        path.write_bytes(data)
        os.chmod(path, 0o600)
        return path

    @staticmethod
    def effective_prompt(
        text: str,
        attachment_path: Path | None,
        mime_type: str | None,
        vision_context: str,
        ocr_context: str,
    ) -> str:
        sections = [text.strip() or "Проанализируй переданное вложение."]
        if attachment_path:
            ocr_instruction = (
                "Локальный OCR уже выполнен и приведён ниже. Не запускай OCR "
                "повторно, если пользователь явно не просит перепроверку."
                if ocr_context
                else "Если требуется точное распознавание текста, используй "
                "установленный Tesseract/OCRmyPDF автоматически. Не проси "
                "отдельный OCR-режим."
            )
            sections.append(
                "В текущий проект добавлено вложение:\n"
                f"- путь: {attachment_path}\n"
                f"- MIME: {mime_type}\n"
                + ocr_instruction
            )
        if vision_context:
            sections.append(
                "Предварительный анализ Gemma Vision (проверь по исходному файлу, "
                "если нужна точность):\n" + vision_context[:VISION_CONTEXT_LIMIT]
            )
        if ocr_context:
            sections.append(
                "Автоматически извлечённый локальный OCR/текстовый слой "
                "(сверяй с исходным файлом):\n" + ocr_context[:OCR_CONTEXT_LIMIT]
            )
        return "\n\n".join(sections)

    @staticmethod
    def should_run_ocr(text: str) -> bool:
        lowered = text.casefold()
        return any(hint in lowered for hint in OCR_HINTS)

    def extract_attachment_text(
        self, text: str, path: Path | None, mime_type: str | None
    ) -> str:
        """Extract text without asking the model to decide whether OCR is needed."""

        if path is None:
            return ""
        try:
            if mime_type in {"image/png", "image/jpeg"}:
                if not self.should_run_ocr(text):
                    return ""
                command = [
                    "tesseract",
                    str(path),
                    "stdout",
                    "-l",
                    self.config.ocr_languages,
                    "--psm",
                    "6",
                ]
                result = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    timeout=self.config.ocr_timeout,
                    check=False,
                )
                return result.stdout.strip() if result.returncode == 0 else ""

            if mime_type == "application/pdf":
                text_layer = subprocess.run(
                    ["pdftotext", str(path), "-"],
                    text=True,
                    capture_output=True,
                    timeout=self.config.ocr_timeout,
                    check=False,
                )
                if text_layer.returncode == 0 and text_layer.stdout.strip():
                    return text_layer.stdout.strip()
                if not self.should_run_ocr(text):
                    return ""
                searchable = path.with_name(path.stem + "-ocr.pdf")
                ocr = subprocess.run(
                    [
                        "ocrmypdf",
                        "--skip-text",
                        "--output-type",
                        "pdf",
                        "-l",
                        self.config.ocr_languages,
                        str(path),
                        str(searchable),
                    ],
                    text=True,
                    capture_output=True,
                    timeout=self.config.ocr_timeout,
                    check=False,
                )
                if ocr.returncode != 0 or not searchable.exists():
                    return ""
                extracted = subprocess.run(
                    ["pdftotext", str(searchable), "-"],
                    text=True,
                    capture_output=True,
                    timeout=self.config.ocr_timeout,
                    check=False,
                )
                return extracted.stdout.strip() if extracted.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired) as error:
            print(f"attachment OCR warning: {error!r}", flush=True)
        return ""

    @staticmethod
    def append_audit(project: dict[str, Any], record: dict[str, Any]) -> None:
        path = Path(project["workspace"]) / "telegram-transcript.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)

    def message(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = validated_chat_id(payload.get("chat_id"))
        text = str(payload.get("text") or "")
        if len(text.encode("utf-8")) > PROMPT_LIMIT:
            raise BridgeError("prompt is too large")
        vision_context = str(payload.get("vision_context") or "")
        with self.chat_lock(chat_id):
            project = self.store.active_project(chat_id, create=True)
            assert project is not None
            attachment = payload.get("attachment")
            attachment_path = self.save_attachment(project, attachment)
            mime_type = attachment.get("mime_type") if isinstance(attachment, dict) else None
            ocr_context = self.extract_attachment_text(
                text, attachment_path, mime_type
            )
            prompt = self.effective_prompt(
                text, attachment_path, mime_type, vision_context, ocr_context
            )
            started = time.monotonic()
            result = self.runner.run_turn(chat_id, project, prompt)
            session_id = str(result.get("session_id") or "")
            session_path = str(result.get("session_path") or "")
            if not session_id or not session_path:
                raise BridgeError("Claw did not return session metadata", HTTPStatus.BAD_GATEWAY)
            project = self.store.set_session(
                chat_id, project["id"], session_id, session_path
            )
            self.append_audit(
                project,
                {
                    "timestamp": utc_timestamp(),
                    "user_id": payload.get("user_id"),
                    "text": text,
                    "attachment": str(attachment_path) if attachment_path else None,
                    "answer": str(result.get("message") or ""),
                    "usage": result.get("usage"),
                    "auto_compaction": result.get("auto_compaction"),
                },
            )
            return {
                "ok": True,
                "message": str(result.get("message") or ""),
                "project": public_project(project),
                "usage": result.get("usage"),
                "auto_compaction": result.get("auto_compaction"),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }


def public_project(project: dict[str, Any]) -> dict[str, Any]:
    return {
        key: project.get(key)
        for key in (
            "id",
            "name",
            "session_id",
            "status",
            "created_at",
            "updated_at",
        )
    }


def make_handler(application: BridgeApplication):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ClawTelegramBridge/1"

        def log_message(self, format_string: str, *args: Any) -> None:
            print(
                f"{self.address_string()} [{self.log_date_time_string()}] "
                + format_string % args,
                flush=True,
            )

        def write_json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", JSON_CONTENT_TYPE)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {application.config.bearer_token}"
            return hmac.compare_digest(supplied, expected)

        def read_payload(self) -> dict[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as error:
                raise BridgeError("invalid Content-Length") from error
            if length < 0 or length > application.config.max_body_bytes:
                raise BridgeError("request body is too large", HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            try:
                value = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError as error:
                raise BridgeError("request body is not valid JSON") from error
            if not isinstance(value, dict):
                raise BridgeError("request body must be a JSON object")
            return value

        def do_GET(self) -> None:  # noqa: N802
            try:
                if self.path == "/health":
                    self.write_json(HTTPStatus.OK, {"status": "ok"})
                    return
                if not self.authorized():
                    self.write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                    return
                self.write_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not found"})
            except (BrokenPipeError, ConnectionResetError):
                return

        def do_POST(self) -> None:  # noqa: N802
            if not self.authorized():
                self.write_json(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
                return
            try:
                payload = self.read_payload()
                chat_id = validated_chat_id(payload.get("chat_id"))
                if self.path == "/v1/message":
                    result = application.message(payload)
                elif self.path == "/v1/projects/new":
                    application.runner.stop(chat_id)
                    result = {
                        "ok": True,
                        "project": public_project(
                            application.store.new_project(chat_id, payload.get("name"))
                        ),
                    }
                elif self.path == "/v1/projects":
                    listing = application.store.list_projects(chat_id)
                    result = {
                        "ok": True,
                        "active_project_id": listing["active_project_id"],
                        "projects": [public_project(item) for item in listing["projects"]],
                    }
                elif self.path == "/v1/projects/switch":
                    project_id = str(payload.get("project_id") or "").strip()
                    if not project_id:
                        raise BridgeError("project_id is required")
                    application.runner.stop(chat_id)
                    result = {
                        "ok": True,
                        "project": public_project(
                            application.store.switch_project(chat_id, project_id)
                        ),
                    }
                elif self.path == "/v1/projects/close":
                    stopped = application.runner.stop(chat_id)
                    project = application.store.close_active(chat_id)
                    result = {
                        "ok": True,
                        "stopped": stopped,
                        "project": public_project(project) if project else None,
                    }
                elif self.path == "/v1/stop":
                    result = {"ok": True, "stopped": application.runner.stop(chat_id)}
                elif self.path == "/v1/status":
                    project = application.store.active_project(chat_id)
                    result = {
                        "ok": True,
                        "running": application.runner.is_running(chat_id),
                        "project": public_project(project) if project else None,
                        "auto_compact_input_tokens": application.config.auto_compact_input_tokens,
                        "max_concurrent": application.config.max_concurrent,
                    }
                else:
                    raise BridgeError("not found", HTTPStatus.NOT_FOUND)
                self.write_json(HTTPStatus.OK, result)
            except BridgeError as error:
                self.write_json(error.status, {"ok": False, "error": str(error)})
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as error:  # keep internals out of the HTTP response
                print(f"unhandled bridge error: {error!r}", flush=True)
                self.write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": "internal bridge error"},
                )

    return Handler


def main() -> None:
    config = BridgeConfig.from_env()
    application = BridgeApplication(config)
    server = ThreadingHTTPServer(
        (config.bind_host, config.bind_port), make_handler(application)
    )
    server.daemon_threads = True
    print(
        f"Claw Telegram bridge listening on {config.bind_host}:{config.bind_port}",
        flush=True,
    )
    server.serve_forever(poll_interval=0.5)


if __name__ == "__main__":
    main()
