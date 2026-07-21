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
import fcntl
import hashlib
import hmac
import json
import os
import re
import signal
import shutil
import subprocess
import tarfile
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
PROGRESS_SLOW_SECONDS = 90
PROGRESS_STALLED_SECONDS = 300
PROGRESS_DETAIL_LIMIT = 240
CONTROL_SCHEMA_VERSION = 1
PROJECT_MEMORY_SCHEMA_VERSION = 1
CONTROL_TEXT_LIMIT = 32_000
TURN_RECOVERY_ATTEMPTS = 3


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
    allowed_tools: str | None
    permission_mode: str
    unrestricted: bool
    turn_timeout: int | None
    max_concurrent: int
    max_body_bytes: int
    max_attachment_bytes: int
    auto_compact_input_tokens: int
    gemma_base_url: str
    gemma_api_key: str
    gemma_max_output_tokens: int
    ocr_timeout: int | None
    ocr_languages: str

    @classmethod
    def from_env(cls) -> "BridgeConfig":
        token = os.environ.get("CLAW_BRIDGE_TOKEN", "").strip()
        if len(token) < 32:
            raise SystemExit("CLAW_BRIDGE_TOKEN must contain at least 32 characters")
        unrestricted = os.environ.get("CLAW_UNRESTRICTED", "").strip().casefold() in {
            "1",
            "true",
            "yes",
            "on",
        }
        configured_tools = os.environ.get("CLAW_ALLOWED_TOOLS", "").strip()
        if configured_tools:
            allowed_tools = (
                None
                if configured_tools.casefold() in {"*", "all", "unrestricted"}
                else configured_tools
            )
        else:
            allowed_tools = None if unrestricted else "Read,Glob,Grep"
        configured_turn_timeout = int(os.environ.get("CLAW_TURN_TIMEOUT", "0"))
        configured_ocr_timeout = int(os.environ.get("CLAW_OCR_TIMEOUT", "0"))
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
            allowed_tools=allowed_tools,
            permission_mode=os.environ.get(
                "CLAW_PERMISSION_MODE",
                "danger-full-access" if unrestricted else "workspace-write",
            ),
            unrestricted=unrestricted,
            turn_timeout=(
                None if configured_turn_timeout <= 0 else max(10, configured_turn_timeout)
            ),
            max_concurrent=max(1, int(os.environ.get("CLAW_MAX_CONCURRENT", "2"))),
            max_body_bytes=max(
                1024, int(os.environ.get("CLAW_BRIDGE_MAX_BODY_BYTES", str(64 << 20)))
            ),
            max_attachment_bytes=max(
                1024, int(os.environ.get("CLAW_MAX_ATTACHMENT_BYTES", str(20 << 20)))
            ),
            auto_compact_input_tokens=max(
                1, int(os.environ.get("CLAW_AUTO_COMPACT_INPUT_TOKENS", "110000"))
            ),
            gemma_base_url=os.environ.get(
                "GOOGLE_BASE_URL", "http://127.0.0.1:18080/v1"
            ),
            gemma_api_key=os.environ.get("GOOGLE_API_KEY", "local-gemma"),
            gemma_max_output_tokens=max(
                1, int(os.environ.get("CLAW_GEMMA_MAX_OUTPUT_TOKENS", "32000"))
            ),
            ocr_timeout=(
                None if configured_ocr_timeout <= 0 else max(10, configured_ocr_timeout)
            ),
            ocr_languages=os.environ.get("CLAW_OCR_LANGUAGES", "rus+eng"),
        )


@dataclass
class ActiveTurn:
    process: subprocess.Popen[str]
    started_at: float
    progress_file: Path
    operation_id: str


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    """Write versioned state by replace so crashes cannot leave partial JSON."""

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    os.replace(temporary, path)


def append_project_event(project: dict[str, Any], event: str, **fields: Any) -> None:
    path = Path(project["workspace"]) / ".claw" / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {
        "schema_version": CONTROL_SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "event": event,
        "timestamp": utc_timestamp(),
        **fields,
    }
    with path.open("a", encoding="utf-8") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
        try:
            stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    path.chmod(0o600)


def _tail_text_lines(path: Path, limit: int, max_bytes: int = 256 * 1024) -> list[str]:
    """Read a bounded tail without loading an indefinitely growing journal."""

    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            remaining = min(stream.tell(), max_bytes)
            chunks = []
            newlines = 0
            while remaining > 0 and newlines <= limit:
                size = min(8192, remaining)
                remaining -= size
                stream.seek(remaining)
                chunk = stream.read(size)
                chunks.append(chunk)
                newlines += chunk.count(b"\n")
    except OSError:
        return []
    data = b"".join(reversed(chunks)).decode("utf-8", "replace")
    return data.splitlines()[-limit:]


def project_observer_context(project: dict[str, Any]) -> dict[str, Any]:
    """Return bounded, read-only durable facts for a separate Gemma observer."""

    workspace = Path(project["workspace"])
    claw = workspace / ".claw"
    try:
        plan_value = json.loads((claw / "plan.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        plan_value = {}
    todos = (
        plan_value.get("todos", [])
        if isinstance(plan_value, dict)
        else plan_value
        if isinstance(plan_value, list)
        else []
    )
    todos = [item for item in todos if isinstance(item, dict)]
    current = next(
        (item for item in todos if item.get("status") == "in_progress"),
        next((item for item in todos if item.get("status") == "pending"), None),
    )
    current_text = ""
    if current is not None:
        current_text = str(current.get("activeForm") or current.get("content") or "")[
            :PROGRESS_DETAIL_LIMIT
        ]

    recent_events = []
    for raw in _tail_text_lines(claw / "events.jsonl", 8):
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        recent_events.append(
            {
                "event": str(event.get("event") or "")[:80],
                "timestamp": str(event.get("timestamp") or "")[:40],
            }
        )

    last_tool = None
    last_assistant_text = None
    session_path = str(project.get("session_path") or "")
    if session_path:
        for raw in reversed(_tail_text_lines(Path(session_path), 200)):
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            message = record.get("message") if isinstance(record, dict) else None
            if not isinstance(message, dict):
                continue
            blocks = message.get("blocks")
            if not isinstance(blocks, list):
                continue
            if last_tool is None:
                for block in reversed(blocks):
                    if not isinstance(block, dict):
                        continue
                    name = block.get("name") or block.get("tool_name")
                    if block.get("type") in {"tool_use", "tool_result"} and name:
                        last_tool = str(name)[:64]
                        break
            if last_assistant_text is None and message.get("role") == "assistant":
                text = "".join(
                    str(block.get("text") or "")
                    for block in blocks
                    if isinstance(block, dict) and block.get("type") == "text"
                ).strip()
                if text:
                    last_assistant_text = text[:PROGRESS_DETAIL_LIMIT]
            if last_tool is not None and last_assistant_text is not None:
                break

    return {
        "plan": {
            "completed": sum(item.get("status") == "completed" for item in todos),
            "total": len(todos),
            "current": current_text or None,
        },
        "last_tool": last_tool,
        "last_assistant_text": last_assistant_text,
        "recent_events": recent_events,
    }


def project_plan_is_complete_and_verified(project: dict[str, Any]) -> bool:
    """Match the runtime completion contract for safe result recovery."""

    path = Path(project["workspace"]) / ".claw" / "plan.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    todos = (
        value.get("todos", [])
        if isinstance(value, dict)
        else value
        if isinstance(value, list)
        else []
    )
    todos = [item for item in todos if isinstance(item, dict)]
    if not todos or any(item.get("status") != "completed" for item in todos):
        return False
    markers = ("verif", "test", "проверк", "qa", "e2e")
    for item in todos:
        content = item.get("content")
        evidence = item.get("evidence")
        if not isinstance(content, str):
            content = ""
        if not isinstance(evidence, str) or not evidence.strip():
            continue
        verification_text = f"{content}\n{evidence}".casefold()
        if any(marker in verification_text for marker in markers):
            return True
    return False


def append_project_correction(
    project: dict[str, Any], text: str, source_message_id: Any = None
) -> None:
    path = Path(project["workspace"]) / ".claw" / "project-memory.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {
            "schema_version": PROJECT_MEMORY_SCHEMA_VERSION,
            "created_at": utc_timestamp(),
            "directives": [],
            "corrections": [],
            "provenance": {"source": "telegram_claw_bridge", "expires_at": None},
        }
    corrections = value.setdefault("corrections", [])
    corrections.append(
        {
            "id": uuid.uuid4().hex,
            "text": text,
            "created_at": utc_timestamp(),
            "source": {
                "kind": "telegram_steering",
                "message_id": source_message_id,
            },
            "expires_at": None,
        }
    )
    value["schema_version"] = PROJECT_MEMORY_SCHEMA_VERSION
    value["updated_at"] = utc_timestamp()
    atomic_write_json(path, value)


def create_project_checkpoint(project: dict[str, Any], reason: str) -> str:
    """Create a non-destructive recovery point before an autonomous mutation."""

    workspace = Path(project["workspace"])
    checkpoint_id = f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
    directory = workspace / ".claw" / "checkpoints" / checkpoint_id
    directory.mkdir(parents=True, mode=0o700)
    git_ref = None
    untracked: list[str] = []
    is_git_worktree = False
    try:
        inside = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "--is-inside-work-tree"],
            text=True,
            capture_output=True,
            check=False,
        )
        if inside.returncode == 0 and inside.stdout.strip() == "true":
            is_git_worktree = True
            snapshot = subprocess.run(
                ["git", "-C", str(workspace), "stash", "create", f"claw-{checkpoint_id}"],
                text=True,
                capture_output=True,
                check=False,
            ).stdout.strip()
            if snapshot:
                git_ref = f"refs/claw/checkpoints/{checkpoint_id}"
                subprocess.run(
                    ["git", "-C", str(workspace), "update-ref", git_ref, snapshot],
                    text=True,
                    capture_output=True,
                    check=True,
                )
            listed = subprocess.run(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ],
                capture_output=True,
                check=False,
            )
            if listed.returncode == 0:
                untracked = [
                    value.decode("utf-8", "surrogateescape")
                    for value in listed.stdout.split(b"\0")
                    if value and not value.startswith(b".claw/")
                ]
                if untracked:
                    with tarfile.open(directory / "untracked.tar.gz", "w:gz") as archive:
                        for relative in untracked:
                            candidate = workspace / relative
                            if candidate.exists() or candidate.is_symlink():
                                archive.add(candidate, arcname=relative, recursive=True)
        if not is_git_worktree:
            with tarfile.open(directory / "workspace.tar.gz", "w:gz") as archive:
                for candidate in workspace.iterdir():
                    if candidate.name == ".claw":
                        continue

                    def exclude_internal_state(
                        member: tarfile.TarInfo,
                    ) -> tarfile.TarInfo | None:
                        parts = Path(member.name).parts
                        return None if ".git" in parts or ".claw" in parts else member

                    archive.add(
                        candidate,
                        arcname=candidate.name,
                        recursive=True,
                        filter=exclude_internal_state,
                    )
    except (OSError, subprocess.SubprocessError, tarfile.TarError) as error:
        (directory / "checkpoint-warning.txt").write_text(
            f"Git/untracked snapshot warning: {error}\n", encoding="utf-8"
        )

    for name in ("plan.json", "project-memory.json", "control-state.json"):
        source = workspace / ".claw" / name
        if source.is_file():
            shutil.copy2(source, directory / name)
    atomic_write_json(
        directory / "metadata.json",
        {
            "schema_version": 1,
            "checkpoint_id": checkpoint_id,
            "created_at": utc_timestamp(),
            "reason": reason,
            "git_ref": git_ref,
            "untracked_archive": "untracked.tar.gz" if untracked else None,
            "workspace_archive": "workspace.tar.gz" if not is_git_worktree else None,
            "session_id": project.get("session_id"),
            "session_path": project.get("session_path"),
            "provenance": {
                "source": "telegram_claw_bridge",
                "expires_at": None,
            },
        },
    )
    append_project_event(
        project,
        "project_checkpoint_created",
        checkpoint_id=checkpoint_id,
        reason=reason,
        git_ref=git_ref,
    )
    return checkpoint_id


def validated_chat_id(value: Any) -> str:
    try:
        return str(int(value))
    except (TypeError, ValueError) as error:
        raise BridgeError("chat_id must be an integer") from error


def validated_operation_id(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise BridgeError("operation_id must be a string")
    operation_id = value.strip()
    if not operation_id or len(operation_id) > 256:
        raise BridgeError("operation_id must contain 1 to 256 characters")
    return operation_id


def validated_operation_ids(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise BridgeError("operation_ids must be an array")
    return tuple(
        operation_id
        for item in value
        if (operation_id := validated_operation_id(item)) is not None
    )


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
            claw_state = workspace / ".claw"
            claw_state.mkdir(mode=0o700)
            now = utc_timestamp()
            atomic_write_json(
                claw_state / "project-memory.json",
                {
                    "schema_version": PROJECT_MEMORY_SCHEMA_VERSION,
                    "created_at": now,
                    "updated_at": now,
                    "directives": [],
                    "corrections": [],
                    "provenance": {
                        "source": "telegram_claw_bridge",
                        "expires_at": None,
                    },
                },
            )
            atomic_write_json(
                claw_state / "control-state.json",
                {
                    "schema_version": CONTROL_SCHEMA_VERSION,
                    "paused": False,
                    "queue": [],
                },
            )
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
        self._active_lock = threading.RLock()
        self._control_changed = threading.Condition(self._active_lock)
        self._active: dict[str, ActiveTurn] = {}
        self._cancelled_operation_ids: dict[str, set[str]] = {}

    def _command(self, project: dict[str, Any], prompt: str) -> list[str]:
        command = [
            str(self.config.claw_binary),
            "--model",
            self.config.model,
            "--output-format",
            "json",
            "--permission-mode",
            self.config.permission_mode,
        ]
        if self.config.allowed_tools:
            command.extend(["--allowedTools", self.config.allowed_tools])
        if project.get("session_id"):
            # Prefer the exact persisted file. This remains unambiguous even
            # for sessions created by older Claw builds whose process-local ID
            # counters could collide across simultaneous worker processes.
            reference = project.get("session_path") or project["session_id"]
            command.extend(["--resume", reference, "prompt", prompt])
        else:
            command.extend(["prompt", prompt])
        return command

    def _agent_environment(
        self,
        progress_file: Path | None = None,
        operation_id: str | None = None,
        workspace: Path | None = None,
    ) -> dict[str, str]:
        """Pass the complete service environment to Claw and its tools."""

        environment = dict(os.environ)
        environment.update(
            {
                "GOOGLE_BASE_URL": self.config.gemma_base_url,
                "GOOGLE_API_KEY": self.config.gemma_api_key,
                "CLAW_GEMMA_MAX_OUTPUT_TOKENS": str(
                    self.config.gemma_max_output_tokens
                ),
                "CLAW_SUBAGENT_MODEL": self.config.model,
                # This is the only deployment-level capability limit: parent
                # plus one child exactly fills the two Gemma inference slots.
                "CLAW_SUBAGENT_MAX_CONCURRENT": "1",
                "CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS": str(
                    self.config.auto_compact_input_tokens
                ),
            }
        )
        if progress_file is not None:
            environment["CLAW_PROGRESS_FILE"] = str(progress_file)
        else:
            environment.pop("CLAW_PROGRESS_FILE", None)
        if operation_id:
            environment["CLAW_PROGRESS_OPERATION_ID"] = operation_id
        else:
            environment.pop("CLAW_PROGRESS_OPERATION_ID", None)
        if workspace is not None:
            state_dir = workspace / ".claw"
            state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            environment.update(
                {
                    "CLAWD_TODO_STORE": str(state_dir / "plan.json"),
                    "CLAW_PROJECT_MEMORY_FILE": str(
                        state_dir / "project-memory.json"
                    ),
                    "CLAW_EVENT_JOURNAL": str(state_dir / "events.jsonl"),
                    "CLAW_AUTONOMOUS_PLAN": "1",
                    "CLAW_DURABLE_MEMORY": "1",
                }
            )
        else:
            for name in (
                "CLAWD_TODO_STORE",
                "CLAW_PROJECT_MEMORY_FILE",
                "CLAW_EVENT_JOURNAL",
                "CLAW_AUTONOMOUS_PLAN",
                "CLAW_DURABLE_MEMORY",
            ):
                environment.pop(name, None)
        if self.config.unrestricted:
            environment["CLAW_UNRESTRICTED"] = "1"
            environment["CLAW_SUBAGENT_LOCK_FILE"] = str(
                self.config.state_file.parent / "claw-subagent.lock"
            )
        else:
            environment.pop("CLAW_UNRESTRICTED", None)
            environment.pop("CLAW_SUBAGENT_LOCK_FILE", None)
        return environment

    @staticmethod
    def _control_path(project: dict[str, Any]) -> Path:
        return Path(project["workspace"]) / ".claw" / "control-state.json"

    def _read_control(self, project: dict[str, Any]) -> dict[str, Any]:
        path = self._control_path(project)
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            value = {
                "schema_version": CONTROL_SCHEMA_VERSION,
                "paused": False,
                "queue": [],
            }
        if value.get("schema_version") != CONTROL_SCHEMA_VERSION:
            raise BridgeError("unsupported project control-state version")
        if not isinstance(value.get("queue"), list):
            raise BridgeError("invalid project control queue")
        return value

    def _write_control(self, project: dict[str, Any], value: dict[str, Any]) -> None:
        value["schema_version"] = CONTROL_SCHEMA_VERSION
        atomic_write_json(self._control_path(project), value)

    def _update_active_task(
        self,
        project: dict[str, Any],
        operation_id: str,
        prompt: str | None = None,
        state: str | None = None,
    ) -> None:
        with self._active_lock:
            value = self._read_control(project)
            task = value.get("active_task")
            if not isinstance(task, dict) or task.get("operation_id") != operation_id:
                task = {
                    "operation_id": operation_id,
                    "created_at": utc_timestamp(),
                    "attempts": 0,
                    "provenance": {
                        "source": "telegram_claw_bridge",
                        "expires_at": None,
                    },
                }
                value["active_task"] = task
            if prompt is not None:
                task["prompt"] = prompt
            if state is not None:
                task["state"] = state
            task["updated_at"] = utc_timestamp()
            self._write_control(project, value)

    def _increment_active_task_attempt(
        self, project: dict[str, Any], operation_id: str
    ) -> None:
        with self._active_lock:
            value = self._read_control(project)
            task = value.get("active_task")
            if isinstance(task, dict) and task.get("operation_id") == operation_id:
                task["attempts"] = int(task.get("attempts") or 0) + 1
                task["state"] = "running"
                task["updated_at"] = utc_timestamp()
                self._write_control(project, value)

    def _finish_active_task(
        self, project: dict[str, Any], operation_id: str, state: str
    ) -> None:
        with self._active_lock:
            value = self._read_control(project)
            task = value.get("active_task")
            if isinstance(task, dict) and task.get("operation_id") == operation_id:
                task["state"] = state
                task["finished_at"] = utc_timestamp()
                value["last_task"] = task
                value.pop("active_task", None)
                self._write_control(project, value)

    def _enqueue_control(
        self,
        project: dict[str, Any],
        kind: str,
        text: str,
        source_message_id: Any = None,
    ) -> dict[str, Any]:
        normalized = text.strip()
        if not normalized:
            raise BridgeError("control message must not be empty")
        if len(normalized) > CONTROL_TEXT_LIMIT:
            raise BridgeError(f"control message exceeds {CONTROL_TEXT_LIMIT} characters")
        with self._active_lock:
            value = self._read_control(project)
            item = {
                "id": uuid.uuid4().hex,
                "kind": kind,
                "text": normalized,
                "state": "pending",
                "created_at": utc_timestamp(),
                "attempts": 0,
                "source": {
                    "kind": "telegram",
                    "message_id": source_message_id,
                },
                "expires_at": None,
            }
            value["queue"].append(item)
            self._write_control(project, value)
        append_project_event(
            project,
            "control_message_queued",
            control_id=item["id"],
            kind=kind,
            source=item["source"],
        )
        return item

    def _claim_control(
        self, project: dict[str, Any], exclude_id: str | None = None
    ) -> dict[str, Any] | None:
        with self._active_lock:
            value = self._read_control(project)
            if value.get("paused"):
                return None
            queue = value["queue"]
            candidates = [
                item
                for item in queue
                if item.get("state") in {"pending", "running"}
                and item.get("id") != exclude_id
            ]
            if not candidates:
                return None
            candidates.sort(key=lambda item: 0 if item.get("kind") == "steer" else 1)
            item = candidates[0]
            item["state"] = "running"
            item["attempts"] = int(item.get("attempts") or 0) + 1
            item["started_at"] = utc_timestamp()
            self._write_control(project, value)
        append_project_event(
            project,
            "control_message_claimed",
            control_id=item.get("id"),
            kind=item.get("kind"),
            attempt=item["attempts"],
        )
        return dict(item)

    def _complete_control(self, project: dict[str, Any], item_id: str) -> None:
        with self._active_lock:
            value = self._read_control(project)
            for item in value["queue"]:
                if item.get("id") == item_id:
                    item["state"] = "completed"
                    item["completed_at"] = utc_timestamp()
                    break
            self._write_control(project, value)
        append_project_event(
            project, "control_message_completed", control_id=item_id
        )

    @staticmethod
    def _control_prompt(item: dict[str, Any]) -> str:
        kind = item.get("kind")
        heading = (
            "OPERATOR STEERING UPDATE — supersedes conflicting earlier instructions"
            if kind == "steer"
            else "NEXT QUEUED OPERATOR TASK"
        )
        return (
            f"{heading}\n"
            f"Control id / idempotency key: {item.get('id')}\n"
            f"Message: {item.get('text')}\n\n"
            "Resume the same project autonomously. Inspect current files and durable plan "
            "before repeating side effects. Apply this update, repair the plan, continue "
            "until every plan item is completed and verified, then answer the operator."
        )

    @staticmethod
    def _adopt_latest_session(project: dict[str, Any]) -> bool:
        session_dir = Path(project["workspace"]) / ".claw" / "sessions"
        try:
            candidates = sorted(
                session_dir.glob("*.jsonl"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            return False
        for path in candidates:
            try:
                for raw_line in path.read_text(encoding="utf-8").splitlines():
                    record = json.loads(raw_line)
                    if record.get("type") == "session_meta" and record.get("session_id"):
                        project["session_id"] = str(record["session_id"])
                        project["session_path"] = str(path)
                        return True
            except (OSError, json.JSONDecodeError):
                continue
        return False

    @staticmethod
    def _write_progress_file(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.chmod(0o600)
        os.replace(temporary, path)

    @staticmethod
    def _read_progress_file(turn: ActiveTurn) -> dict[str, Any]:
        try:
            payload = json.loads(turn.progress_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        reported_operation = str(payload.get("operation_id") or "")
        if reported_operation and reported_operation != turn.operation_id:
            return {}
        return payload

    def _agent_progress(self, operation_id: str) -> list[dict[str, Any]]:
        store = self.config.projects_root / ".clawd-agents"
        try:
            candidates = sorted(
                store.glob("*.json"),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )[:200]
        except OSError:
            return []
        agents = []
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict) or payload.get("operationId") != operation_id:
                continue
            agents.append(
                {
                    "agent_id": str(payload.get("agentId") or "")[:128],
                    "name": str(payload.get("name") or "")[:128],
                    "description": str(payload.get("description") or "")[
                        :PROGRESS_DETAIL_LIMIT
                    ],
                    "status": str(payload.get("status") or "unknown")[:32],
                    "started_at": payload.get("startedAt"),
                    "completed_at": payload.get("completedAt"),
                }
            )
        return agents

    def progress(self, chat_id: str, now: float | None = None) -> dict[str, Any]:
        with self._active_lock:
            turn = self._active.get(chat_id)
        if turn is None or turn.process.poll() is not None:
            return {
                "running": False,
                "health": "idle",
                "phase": "idle",
                "agents": [],
            }

        observed_at = time.time() if now is None else now
        payload = self._read_progress_file(turn)
        updated_at_ms = payload.get("updated_at_unix_ms")
        try:
            updated_at = float(updated_at_ms) / 1000
        except (TypeError, ValueError):
            updated_at = turn.started_at
        elapsed = max(0, int(observed_at - turn.started_at))
        inactive = max(0, int(observed_at - updated_at))
        if inactive >= PROGRESS_STALLED_SECONDS:
            health = "possibly_stalled"
        elif inactive >= PROGRESS_SLOW_SECONDS:
            health = "slow"
        else:
            health = "working"
        phase = str(payload.get("phase") or "starting")[:32]
        detail = str(payload.get("detail") or "")[:PROGRESS_DETAIL_LIMIT] or None
        tool_name = str(payload.get("tool_name") or "")[:64] or None
        return {
            "running": True,
            "health": health,
            "phase": phase,
            "detail": detail,
            "tool_name": tool_name,
            "elapsed_seconds": elapsed,
            "last_activity_seconds": inactive,
            "started_at_unix_ms": int(turn.started_at * 1000),
            "updated_at_unix_ms": int(updated_at * 1000),
            "operation_id": turn.operation_id,
            "process_id": turn.process.pid,
            "agents": self._agent_progress(turn.operation_id),
        }

    @staticmethod
    def _session_signature(
        project: dict[str, Any],
    ) -> tuple[int, int, int, str] | None:
        session_path = str(project.get("session_path") or "")
        if not session_path:
            return None
        try:
            path = Path(session_path)
            stat = path.stat()
            lines = path.read_bytes().splitlines()
        except OSError:
            return None
        tail_digest = hashlib.sha256(lines[-1]).hexdigest() if lines else ""
        return stat.st_mtime_ns, stat.st_size, len(lines), tail_digest

    @staticmethod
    def _recover_persisted_result(
        project: dict[str, Any], before: tuple[int, int, int, str] | None
    ) -> dict[str, Any] | None:
        """Recover a completed resumed turn if CLI stdout was lost.

        Claw persists the JSONL session before writing its JSON result to
        stdout. A terminal/signal race can therefore leave a valid completed
        turn on disk while the bridge receives no result. Only recover when an
        existing exact session file changed during this process; otherwise an
        older assistant reply could be returned incorrectly.
        """

        session_path = str(project.get("session_path") or "")
        session_id = str(project.get("session_id") or "")
        if not session_path or not session_id or before is None:
            return None
        path = Path(session_path)
        try:
            stat = path.stat()
            raw_lines = path.read_bytes().splitlines()
            tail_digest = hashlib.sha256(raw_lines[-1]).hexdigest() if raw_lines else ""
            after = (stat.st_mtime_ns, stat.st_size, len(raw_lines), tail_digest)
            if after == before:
                return None
            if len(raw_lines) > before[2]:
                candidate_lines = raw_lines[before[2] :]
            elif tail_digest != before[3]:
                # Auto-compaction may rewrite the complete JSONL file and
                # reduce its record count. A changed tail is still evidence
                # that this invocation persisted a new final record.
                candidate_lines = raw_lines
            else:
                # Metadata or timestamps changed, but the prior final record
                # did not. Never replay an older assistant response.
                return None
        except OSError:
            return None

        for raw_line in reversed(candidate_lines):
            try:
                record = json.loads(raw_line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if not isinstance(record, dict) or record.get("type") != "message":
                continue
            message = record.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            blocks = message.get("blocks")
            if not isinstance(blocks, list):
                continue
            text = "".join(
                str(block.get("text") or "")
                for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
            )
            if not text:
                continue
            return {
                "message": text,
                "session_id": session_id,
                "session_path": session_path,
                "usage": message.get("usage"),
                "auto_compaction": None,
                "recovered_from_session": True,
            }
        return None

    def run_turn(
        self,
        chat_id: str,
        project: dict[str, Any],
        prompt: str,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.config.claw_binary.is_file():
            raise BridgeError("Claw binary is unavailable", HTTPStatus.SERVICE_UNAVAILABLE)
        active_operation_id = operation_id or f"bridge-{uuid.uuid4().hex}"
        progress_file = (
            self.config.state_file.parent / "progress" / f"chat-{chat_id}.json"
        )
        current_prompt = prompt
        current_control: dict[str, Any] | None = None
        failure_attempts = 0
        started_at = time.time()
        append_project_event(
            project,
            "turn_started",
            chat_id=chat_id,
            operation_id=active_operation_id,
        )
        self._update_active_task(
            project, active_operation_id, prompt=current_prompt, state="running"
        )

        while True:
            with self._control_changed:
                while self._read_control(project).get("paused"):
                    cancelled = self._cancelled_operation_ids.get(chat_id, set())
                    if active_operation_id in cancelled:
                        cancelled.discard(active_operation_id)
                        if not cancelled:
                            self._cancelled_operation_ids.pop(chat_id, None)
                        self._finish_active_task(
                            project, active_operation_id, "cancelled"
                        )
                        raise BridgeError("Claw operation was stopped", HTTPStatus.CONFLICT)
                    self._control_changed.wait(timeout=1)

            session_before = self._session_signature(project)
            attempt_started = time.time()
            try:
                self._write_progress_file(
                    progress_file,
                    {
                        "operation_id": active_operation_id,
                        "phase": "starting",
                        "detail": (
                            f"control:{current_control.get('id')}"
                            if current_control
                            else "initial turn"
                        ),
                        "updated_at_unix_ms": int(attempt_started * 1000),
                    },
                )
            except OSError as error:
                print(f"Claw progress initialization warning: {error}", flush=True)
            environment = self._agent_environment(
                progress_file,
                active_operation_id,
                Path(project["workspace"]),
            )
            self._increment_active_task_attempt(project, active_operation_id)

            with self._capacity:
                with self._active_lock:
                    cancelled = self._cancelled_operation_ids.get(chat_id, set())
                    if operation_id and operation_id in cancelled:
                        cancelled.discard(operation_id)
                        if not cancelled:
                            self._cancelled_operation_ids.pop(chat_id, None)
                        self._finish_active_task(
                            project, active_operation_id, "cancelled"
                        )
                        raise BridgeError("Claw operation was stopped", HTTPStatus.CONFLICT)
                    if chat_id in self._active:
                        raise BridgeError(
                            "this project already has a running turn", HTTPStatus.CONFLICT
                        )
                    process = subprocess.Popen(
                        self._command(project, current_prompt),
                        cwd=project["workspace"],
                        env=environment,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        start_new_session=True,
                    )
                    self._active[chat_id] = ActiveTurn(
                        process=process,
                        started_at=attempt_started,
                        progress_file=progress_file,
                        operation_id=active_operation_id,
                    )
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
                adopted_session = self._adopt_latest_session(project)
                with self._active_lock:
                    paused = bool(self._read_control(project).get("paused"))
                    cancelled = self._cancelled_operation_ids.get(chat_id)
                    stopped = bool(
                        cancelled and active_operation_id in cancelled
                    )
                    if stopped:
                        cancelled.discard(active_operation_id)
                        if not cancelled:
                            self._cancelled_operation_ids.pop(chat_id, None)
                if stopped:
                    self._finish_active_task(project, active_operation_id, "cancelled")
                    raise BridgeError("Claw operation was stopped", HTTPStatus.CONFLICT)
                if paused:
                    self._update_active_task(
                        project,
                        active_operation_id,
                        prompt=current_prompt,
                        state="paused",
                    )
                    create_project_checkpoint(project, "pause after interrupted process")
                    append_project_event(
                        project,
                        "turn_interrupted_for_pause",
                        operation_id=active_operation_id,
                    )
                    current_prompt = (
                        "Resume the interrupted autonomous plan from durable state. "
                        "Inspect current files before repeating side effects and continue "
                        "until the plan is complete and verified."
                    )
                    self._update_active_task(
                        project,
                        active_operation_id,
                        prompt=current_prompt,
                        state="paused",
                    )
                    continue

                next_control = self._claim_control(
                    project,
                    exclude_id=(current_control or {}).get("id"),
                )
                if next_control is not None:
                    create_project_checkpoint(
                        project, f"before {next_control.get('kind')} control"
                    )
                    if current_control is not None:
                        self._supersede_control(
                            project,
                            str(current_control["id"]),
                            str(next_control["id"]),
                        )
                    current_control = next_control
                    failure_attempts = 0
                    control_prompt = self._control_prompt(next_control)
                    current_prompt = (
                        control_prompt
                        if adopted_session
                        else (
                            "The first attempt was interrupted before its session metadata "
                            "was durable. Preserve and continue this original request:\n"
                            f"{current_prompt}\n\n{control_prompt}"
                        )
                    )
                    self._update_active_task(
                        project,
                        active_operation_id,
                        prompt=current_prompt,
                        state="running",
                    )
                    append_project_event(
                        project,
                        "turn_restarted_for_steering",
                        operation_id=active_operation_id,
                        control_id=next_control["id"],
                    )
                    continue

                message = (
                    stderr.strip()
                    or stdout.strip()
                    or f"exit code {process.returncode}"
                )
                recovered_result = None
                if (
                    "assistant stream produced no content" in message
                    and project_plan_is_complete_and_verified(project)
                ):
                    recovered_result = self._recover_persisted_result(
                        project, session_before
                    )
                if recovered_result is not None:
                    append_project_event(
                        project,
                        "turn_result_recovered_after_empty_stream",
                        operation_id=active_operation_id,
                    )
                    stdout = (
                        stdout.rstrip()
                        + "\n"
                        + json.dumps(recovered_result, ensure_ascii=False)
                    )
                    failure_attempts = 0
                if recovered_result is None:
                    failure_attempts += 1
                    if failure_attempts < TURN_RECOVERY_ATTEMPTS:
                        append_project_event(
                            project,
                            "turn_recovery_scheduled",
                            operation_id=active_operation_id,
                            attempt=failure_attempts,
                            error=message[-1000:],
                        )
                        current_prompt = (
                            "RECOVERY ATTEMPT after an interrupted or failed Claw process. "
                            f"Attempt {failure_attempts + 1}/{TURN_RECOVERY_ATTEMPTS}. "
                            "Resume from the durable plan, project memory, event journal, and "
                            "current files. Inspect existing effects before repeating them; use "
                            "their idempotency keys. Diagnose the previous failure, choose a "
                            "materially different recovery when appropriate, and continue until "
                            "the plan is complete and verified. Previous process tail:\n"
                            f"{message[-1000:]}"
                        )
                        self._update_active_task(
                            project,
                            active_operation_id,
                            prompt=current_prompt,
                            state="recovering",
                        )
                        time.sleep(min(failure_attempts, 2))
                        continue
                    self._finish_active_task(project, active_operation_id, "blocked")
                    raise BridgeError(
                        f"Claw turn failed: {message[-2000:]}", HTTPStatus.BAD_GATEWAY
                    )

            result = None
            for line in reversed(stdout.splitlines()):
                try:
                    candidate = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(candidate, dict) and "message" in candidate:
                    result = candidate
                    break
            if result is None:
                result = self._recover_persisted_result(project, session_before)
            if result is None:
                print(
                    "Claw output protocol error: "
                    f"stdout_chars={len(stdout)} stderr_chars={len(stderr)}",
                    flush=True,
                )
                raise BridgeError("Claw returned no JSON result", HTTPStatus.BAD_GATEWAY)

            session_id = str(result.get("session_id") or "")
            session_path = str(result.get("session_path") or "")
            if session_id and session_path:
                project["session_id"] = session_id
                project["session_path"] = session_path
            if current_control is not None:
                self._complete_control(project, str(current_control["id"]))
                current_control = None
            failure_attempts = 0

            next_control = self._claim_control(project)
            if next_control is not None:
                create_project_checkpoint(
                    project, f"before {next_control.get('kind')} control"
                )
                current_control = next_control
                current_prompt = self._control_prompt(next_control)
                self._update_active_task(
                    project,
                    active_operation_id,
                    prompt=current_prompt,
                    state="running",
                )
                continue

            append_project_event(
                project,
                "turn_completed",
                operation_id=active_operation_id,
                elapsed_seconds=round(time.time() - started_at, 3),
            )
            self._finish_active_task(project, active_operation_id, "completed")
            return result

    def _supersede_control(
        self, project: dict[str, Any], item_id: str, replacement_id: str
    ) -> None:
        with self._active_lock:
            value = self._read_control(project)
            for item in value["queue"]:
                if item.get("id") == item_id:
                    item["state"] = "superseded"
                    item["superseded_by"] = replacement_id
                    break
            self._write_control(project, value)
        append_project_event(
            project,
            "control_message_superseded",
            control_id=item_id,
            replacement_id=replacement_id,
        )

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

    def stop(self, chat_id: str, operation_ids: tuple[str, ...] = ()) -> bool:
        with self._active_lock:
            if operation_ids:
                self._cancelled_operation_ids.setdefault(chat_id, set()).update(
                    operation_ids
                )
            turn = self._active.get(chat_id)
        if turn is None or turn.process.poll() is not None:
            return False
        self._terminate(turn.process, signal.SIGINT)
        return True

    def steer(
        self,
        chat_id: str,
        project: dict[str, Any],
        text: str,
        source_message_id: Any = None,
    ) -> dict[str, Any]:
        with self._active_lock:
            item = self._enqueue_control(
                project, "steer", text, source_message_id=source_message_id
            )
            append_project_correction(project, text, source_message_id)
            turn = self._active.get(chat_id)
        interrupted = bool(turn and turn.process.poll() is None)
        if interrupted:
            self._terminate(turn.process, signal.SIGINT)
        return {
            "accepted": True,
            "interrupted": interrupted,
            "control_id": item["id"],
        }

    def enqueue_next(
        self,
        chat_id: str,
        project: dict[str, Any],
        text: str,
        source_message_id: Any = None,
    ) -> dict[str, Any]:
        with self._active_lock:
            item = self._enqueue_control(
                project, "next", text, source_message_id=source_message_id
            )
            running = self.is_running(chat_id)
            self._control_changed.notify_all()
        return {"accepted": True, "running": running, "control_id": item["id"]}

    def pause(self, chat_id: str, project: dict[str, Any]) -> bool:
        with self._active_lock:
            value = self._read_control(project)
            value["paused"] = True
            self._write_control(project, value)
            turn = self._active.get(chat_id)
        append_project_event(project, "operation_paused", chat_id=chat_id)
        if turn and turn.process.poll() is None:
            self._terminate(turn.process, signal.SIGINT)
            return True
        return False

    def resume(self, chat_id: str, project: dict[str, Any]) -> dict[str, Any]:
        with self._control_changed:
            value = self._read_control(project)
            was_paused = bool(value.get("paused"))
            active_task = value.get("active_task")
            live = self.is_running(chat_id)
            value["paused"] = False
            self._write_control(project, value)
            self._control_changed.notify_all()
        append_project_event(project, "operation_resumed", chat_id=chat_id)
        restart_prompt = None
        if not live and isinstance(active_task, dict):
            persisted_prompt = str(active_task.get("prompt") or "").strip()
            if persisted_prompt:
                restart_prompt = (
                    "RECOVER A DURABLE IN-FLIGHT TASK AFTER PROCESS OR SERVICE RESTART. "
                    "Inspect the durable plan, project memory, checkpoints, event journal, "
                    "session archive, and current files before repeating any side effect. "
                    "Continue autonomously to verified completion. Persisted task:\n"
                    + persisted_prompt
                )
        return {
            "resumed": was_paused or live or restart_prompt is not None,
            "live": live,
            "restart_prompt": restart_prompt,
        }

    def cancel_control(self, project: dict[str, Any]) -> None:
        with self._control_changed:
            value = self._read_control(project)
            value["paused"] = False
            for item in value["queue"]:
                if item.get("state") in {"pending", "running"}:
                    item["state"] = "cancelled"
                    item["cancelled_at"] = utc_timestamp()
            active_task = value.get("active_task")
            if isinstance(active_task, dict):
                active_task["state"] = "cancelled"
                active_task["finished_at"] = utc_timestamp()
                value["last_task"] = active_task
                value.pop("active_task", None)
            self._write_control(project, value)
            self._control_changed.notify_all()
        append_project_event(project, "control_queue_cancelled")

    def control_status(self, project: dict[str, Any]) -> dict[str, Any]:
        with self._active_lock:
            value = self._read_control(project)
        queued = [
            {
                "id": item.get("id"),
                "kind": item.get("kind"),
                "state": item.get("state"),
                "text": str(item.get("text") or "")[:PROGRESS_DETAIL_LIMIT],
                "attempts": item.get("attempts", 0),
            }
            for item in value["queue"]
            if item.get("state") in {"pending", "running"}
        ]
        checkpoint_root = Path(project["workspace"]) / ".claw" / "checkpoints"
        try:
            checkpoints = sorted(
                (path.name for path in checkpoint_root.iterdir() if path.is_dir()),
                reverse=True,
            )
        except OSError:
            checkpoints = []
        return {
            "paused": bool(value.get("paused")),
            "queue": queued,
            "checkpoint_count": len(checkpoints),
            "latest_checkpoint": checkpoints[0] if checkpoints else None,
        }

    def is_running(self, chat_id: str) -> bool:
        with self._active_lock:
            turn = self._active.get(chat_id)
            return bool(turn and turn.process.poll() is None)


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
                "если нужна точность):\n" + vision_context
            )
        if ocr_context:
            sections.append(
                "Автоматически извлечённый локальный OCR/текстовый слой "
                "(сверяй с исходным файлом):\n" + ocr_context
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

    @staticmethod
    def ensure_autonomous_plan(
        project: dict[str, Any], text: str, operation_id: str | None
    ) -> None:
        path = Path(project["workspace"]) / ".claw" / "plan.json"
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            todos = (
                current
                if isinstance(current, list)
                else current.get("todos", [])
                if isinstance(current, dict)
                else []
            )
        except (OSError, json.JSONDecodeError):
            todos = []
        if not isinstance(todos, list):
            todos = []
        todos = [item for item in todos if isinstance(item, dict)]
        incomplete = [item for item in todos if item.get("status") != "completed"]
        request = (text.strip() or "Complete the operator request")[:4000]
        idempotency_key = operation_id or f"turn-{uuid.uuid4().hex}"
        if incomplete:
            for item in incomplete:
                if item.get("status") == "in_progress":
                    item["status"] = "pending"
            todos.insert(
                0,
                {
                    "content": f"Apply latest operator request: {request}",
                    "activeForm": "Applying latest operator request",
                    "status": "in_progress",
                    "idempotencyKey": idempotency_key,
                },
            )
        else:
            todos = [
                {
                    "content": f"Complete operator request: {request}",
                    "activeForm": "Completing operator request",
                    "status": "in_progress",
                    "idempotencyKey": idempotency_key,
                },
                {
                    "content": "Verify completion with tests or direct production evidence",
                    "activeForm": "Verifying completion",
                    "status": "pending",
                    "idempotencyKey": f"{idempotency_key}:verify",
                },
            ]
        atomic_write_json(
            path,
            {
                "schema_version": 1,
                "updated_at": utc_timestamp(),
                "provenance": {
                    "source": "telegram_claw_bridge",
                    "operation_id": operation_id,
                    "expires_at": None,
                },
                "todos": todos,
            },
        )
        append_project_event(
            project,
            "autonomous_plan_initialized",
            operation_id=operation_id,
            todo_count=len(todos),
        )

    def active_project_required(self, chat_id: str) -> dict[str, Any]:
        project = self.store.active_project(chat_id)
        if project is None:
            raise BridgeError("there is no active Claw project", HTTPStatus.CONFLICT)
        return project

    def steer(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = validated_chat_id(payload.get("chat_id"))
        project = self.active_project_required(chat_id)
        result = self.runner.steer(
            chat_id,
            project,
            str(payload.get("text") or ""),
            payload.get("message_id"),
        )
        return {"ok": True, **result}

    def enqueue_next(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = validated_chat_id(payload.get("chat_id"))
        project = self.active_project_required(chat_id)
        result = self.runner.enqueue_next(
            chat_id,
            project,
            str(payload.get("text") or ""),
            payload.get("message_id"),
        )
        return {"ok": True, **result}

    def pause(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = validated_chat_id(payload.get("chat_id"))
        project = self.active_project_required(chat_id)
        return {"ok": True, "paused": True, "interrupted": self.runner.pause(chat_id, project)}

    def resume(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = validated_chat_id(payload.get("chat_id"))
        project = self.active_project_required(chat_id)
        return {"ok": True, **self.runner.resume(chat_id, project)}

    def queue_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = validated_chat_id(payload.get("chat_id"))
        project = self.active_project_required(chat_id)
        return {"ok": True, **self.runner.control_status(project)}

    def message(self, payload: dict[str, Any]) -> dict[str, Any]:
        chat_id = validated_chat_id(payload.get("chat_id"))
        text = str(payload.get("text") or "")
        vision_context = str(payload.get("vision_context") or "")
        operation_id = validated_operation_id(payload.get("operation_id"))
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
            create_project_checkpoint(project, "before operator turn")
            self.ensure_autonomous_plan(project, text, operation_id)
            started = time.monotonic()
            result = self.runner.run_turn(chat_id, project, prompt, operation_id)
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


def configured_tool_enabled(allowed_tools: str | None, tool_name: str) -> bool:
    """Report whether a tool survives an optional deployment allowlist."""

    if allowed_tools is None:
        return True
    normalized = tool_name.casefold()
    return normalized in {
        token.casefold()
        for token in allowed_tools.replace(",", " ").split()
        if token
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
                elif self.path == "/v1/steer":
                    result = application.steer(payload)
                elif self.path == "/v1/next":
                    result = application.enqueue_next(payload)
                elif self.path == "/v1/pause":
                    result = application.pause(payload)
                elif self.path == "/v1/continue":
                    result = application.resume(payload)
                elif self.path == "/v1/queue":
                    result = application.queue_status(payload)
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
                    operation_ids = validated_operation_ids(payload.get("operation_ids"))
                    stopped = application.runner.stop(chat_id, operation_ids)
                    project = application.store.active_project(chat_id)
                    if project is not None:
                        application.runner.cancel_control(project)
                    result = {
                        "ok": True,
                        "stopped": stopped,
                    }
                elif self.path == "/v1/progress":
                    project = application.store.active_project(chat_id)
                    control = (
                        application.runner.control_status(project)
                        if project is not None
                        else {"paused": False, "queue": []}
                    )
                    result = {
                        "ok": True,
                        **application.runner.progress(chat_id),
                        **control,
                        "observer_context": (
                            project_observer_context(project) if project is not None else {}
                        ),
                    }
                elif self.path == "/v1/status":
                    project = application.store.active_project(chat_id)
                    progress = application.runner.progress(chat_id)
                    result = {
                        "ok": True,
                        "running": progress["running"],
                        "progress": progress,
                        "project": public_project(project) if project else None,
                        "auto_compact_input_tokens": application.config.auto_compact_input_tokens,
                        "max_concurrent": application.config.max_concurrent,
                        "permission_mode": application.config.permission_mode,
                        "unrestricted": application.config.unrestricted,
                        "agents_enabled": configured_tool_enabled(
                            application.config.allowed_tools, "Agent"
                        ),
                        "tools_unrestricted": application.config.allowed_tools is None,
                        "gemma_max_output_tokens": application.config.gemma_max_output_tokens,
                        "autonomous_plan": True,
                        "durable_memory": True,
                        "live_steering": True,
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
