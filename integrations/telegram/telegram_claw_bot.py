#!/usr/bin/env python3
"""Dependency-free Telegram bot for Gemma Vision and persisted Claw projects.

The active bot runs on the jump host because it can reach Telegram. Its LLM
and Claw bridge endpoints are localhost-only SSH tunnels. Never add bot or
bridge tokens or deployment environment files to this repository.
"""

import base64
import concurrent.futures
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://127.0.0.1:18080").rstrip("/")
ALLOWED = {
    int(value)
    for value in os.environ.get("BOT_ALLOWED_USER_IDS", "").replace(",", " ").split()
    if value.strip().lstrip("-").isdigit()
}
ALLOWED_USERNAMES = {
    value.strip().lstrip("@").lower()
    for value in os.environ.get("BOT_ALLOWED_USERNAMES", "").replace(",", " ").split()
    if value.strip()
}
MAX_TOKENS = int(os.environ.get("MAX_TOKENS", "4096"))
MAX_RESPONSE_TOKENS = max(
    64,
    int(os.environ.get("MAX_RESPONSE_TOKENS", "8192")),
)
TEMPERATURE = float(os.environ.get("TEMPERATURE", "1.0"))
TOP_P = float(os.environ.get("TOP_P", "0.95"))
TOP_K = int(os.environ.get("TOP_K", "64"))
ENABLE_THINKING = os.environ.get("ENABLE_THINKING", "false").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
HISTORY_TURNS = int(os.environ.get("HISTORY_TURNS", "6"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "300"))
BOT_MAX_CONCURRENT_REQUESTS = max(
    1,
    int(os.environ.get("BOT_MAX_CONCURRENT_REQUESTS", "2")),
)
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(10 * 1024 * 1024)))
MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_BYTES", str(20 * 1024 * 1024)))
CLAW_BRIDGE_URL = os.environ.get(
    "CLAW_BRIDGE_URL", "http://127.0.0.1:19090"
).rstrip("/")
CLAW_BRIDGE_TOKEN = os.environ.get("CLAW_BRIDGE_TOKEN", "").strip()
CLAW_REQUEST_TIMEOUT = int(os.environ.get("CLAW_REQUEST_TIMEOUT", "1000"))
CLAW_VISION_MAX_TOKENS = int(os.environ.get("CLAW_VISION_MAX_TOKENS", "1024"))
CHAT_MODE_STATE_FILE = Path(
    os.environ.get("CHAT_MODE_STATE_FILE", "/var/lib/tg-gemma-bot/chat-modes.json")
)
SYSTEM_PROMPT_FILE = os.environ.get("SYSTEM_PROMPT_FILE", "").strip()
SYSTEM_PROMPT = os.environ.get("SYSTEM_PROMPT", "").strip()
BUNDLED_SYSTEM_PROMPT_FILE = (
    Path(__file__).resolve().parent
    / "prompts"
    / "gemma4-abliterated-system-prompt.txt"
)
DEFAULT_SYSTEM_PROMPT = (
    "You are Gemma 4 31B IT Abliterated, the local operator-controlled "
    "deployment. Do not invent generic policy restrictions or revert to the "
    "ordinary Google Gemma persona. Answer directly and practically in the "
    "user's language. State only concrete capability or access limits."
)

API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"
HISTORY = {}
TOKEN_LIMITS = {}
CHAT_LOCKS = {}
CHAT_MODES = {}
STATE_LOCK = threading.Lock()
EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=BOT_MAX_CONCURRENT_REQUESTS,
    thread_name_prefix="tg-gemma",
)
CONTROL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="tg-claw-control",
)
RESET_TEXTS = {
    "/reset",
    "🧹 Сбросить старый чат",
    "🧹 Сбросить чат Gemma",
    "Сбросить старый чат",
}
ALLOWED_IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png"})
ALLOWED_CLAW_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "application/pdf"}
)
DEFAULT_IMAGE_PROMPT = "Опиши изображение и извлеки из него важный текст и данные."
CLAW_VISION_SYSTEM_PROMPT = (
    "Ты модуль предварительного анализа вложения для coding-агента. "
    "Точно опиши изображение, извлеки весь читаемый текст, числа, ошибки и элементы интерфейса. "
    "Не выдумывай неразборчивые данные. Ответ предназначен другому агенту."
)
MODE_GEMMA = "gemma"
MODE_CLAW = "claw"
CONTROL_TEXTS = {
    "/stop",
    "/closeclaw",
    "/newclaw",
    "⛔ Остановить Claw",
    "❌ Закрыть проект",
    "🆕 Новый проект",
}
COMMANDS = [
    {"command": "start", "description": "Запустить бота / показать справку"},
    {"command": "help", "description": "Показать справку и список команд"},
    {"command": "reset", "description": "Очистить историю текущего чата"},
    {"command": "status", "description": "Проверить LLM и настройки"},
    {"command": "bench", "description": "Бенчмарк генерации, например /bench 128"},
    {"command": "tokens", "description": "Лимит ответа, например /tokens 4096"},
    {"command": "permissions", "description": "Права команд Claw в sandbox VM"},
    {"command": "whoami", "description": "Показать chat_id, user_id и username"},
    {"command": "gemma", "description": "Переключиться на обычный чат Gemma"},
    {"command": "claw", "description": "Переключиться на текущий проект Claw"},
    {"command": "newclaw", "description": "Создать новый проект и сессию Claw"},
    {"command": "projects", "description": "Список сохранённых проектов Claw"},
    {"command": "project", "description": "Открыть проект: /project ID"},
    {"command": "stop", "description": "Остановить текущую операцию Claw"},
    {"command": "closeclaw", "description": "Закрыть текущий проект Claw"},
]


class ImageInputError(ValueError):
    """A safe, user-facing validation error for an image attachment."""


class ClawBridgeError(RuntimeError):
    """A user-safe error returned by the local Claw project bridge."""


def main_keyboard():
    return {
        "keyboard": [
            [{"text": "💬 Gemma"}, {"text": "🛠 Claw Cod"}],
            [{"text": "🆕 Новый проект"}, {"text": "📁 Проекты"}],
            [{"text": "⛔ Остановить Claw"}, {"text": "❌ Закрыть проект"}],
            [{"text": "🧹 Сбросить чат Gemma"}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def http_json(url, payload=None, timeout=REQUEST_TIMEOUT, extra_headers=None):
    data = None
    headers = {"Content-Type": "application/json"}
    headers.update(extra_headers or {})
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def load_system_prompt():
    """Load the deployment prompt without requiring a bot rebuild.

    An explicitly configured file wins, then the legacy direct environment
    value, then the repository-bundled prompt. The configured file is read on
    every request so replacing its contents takes effect immediately.
    """

    candidates = [SYSTEM_PROMPT_FILE] if SYSTEM_PROMPT_FILE else []
    if not SYSTEM_PROMPT and BUNDLED_SYSTEM_PROMPT_FILE.is_file():
        candidates.append(str(BUNDLED_SYSTEM_PROMPT_FILE))
    for value in candidates:
        try:
            prompt = Path(value).read_text(encoding="utf-8").strip()
        except OSError as error:
            print(f"cannot read SYSTEM_PROMPT_FILE={value}: {error}", flush=True)
            continue
        if prompt:
            return prompt
    return SYSTEM_PROMPT or DEFAULT_SYSTEM_PROMPT


def tg(method, payload=None, timeout=REQUEST_TIMEOUT):
    return http_json(f"{API}/{method}", payload or {}, timeout=timeout)


def split_telegram_text(text, limit=3900):
    """Return every character in ordered Telegram-safe chunks."""

    remaining = str(text or "(пустой ответ)")
    chunks = []
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = -1
        for separator in ("\n\n", "\n", " "):
            candidate = window.rfind(separator, limit // 2)
            if candidate >= 0:
                split_at = candidate + len(separator)
                break
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    chunks.append(remaining)
    return chunks


def send_message(chat_id, text, reply_to=None, keyboard=True):
    chunks = split_telegram_text(text)
    for index, part in enumerate(chunks):
        payload = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
        if reply_to and index == 0:
            payload["reply_parameters"] = {"message_id": reply_to}
        if keyboard and index == len(chunks) - 1:
            payload["reply_markup"] = main_keyboard()
        tg("sendMessage", payload, timeout=60)


def send_typing(chat_id):
    try:
        tg("sendChatAction", {"chat_id": chat_id, "action": "typing"}, timeout=20)
    except Exception:
        pass


def normalize_username(user):
    return (user.get("username") or "").strip().lstrip("@").lower()


def allowed_user(user):
    user_id = int(user.get("id") or 0)
    username = normalize_username(user)
    if not ALLOWED and not ALLOWED_USERNAMES:
        return True
    return user_id in ALLOWED or bool(username and username in ALLOWED_USERNAMES)


def commands_text():
    return "Доступные команды:\n" + "\n".join(
        f"/{command['command']} — {command['description']}" for command in COMMANDS
    )


def _load_chat_modes():
    try:
        value = json.loads(CHAT_MODE_STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        int(chat_id): mode
        for chat_id, mode in value.items()
        if str(chat_id).lstrip("-").isdigit() and mode in {MODE_GEMMA, MODE_CLAW}
    }


def _persist_chat_modes():
    try:
        CHAT_MODE_STATE_FILE.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = CHAT_MODE_STATE_FILE.with_suffix(
            CHAT_MODE_STATE_FILE.suffix + ".tmp"
        )
        temporary.write_text(
            json.dumps(CHAT_MODES, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.chmod(temporary, 0o600)
        os.replace(temporary, CHAT_MODE_STATE_FILE)
    except OSError as error:
        print("chat mode persistence warning:", repr(error), flush=True)


def chat_mode(chat_id):
    with STATE_LOCK:
        return CHAT_MODES.get(chat_id, MODE_GEMMA)


def set_chat_mode(chat_id, mode):
    if mode not in {MODE_GEMMA, MODE_CLAW}:
        raise ValueError(f"unsupported mode: {mode}")
    with STATE_LOCK:
        CHAT_MODES[chat_id] = mode
        _persist_chat_modes()


CHAT_MODES.update(_load_chat_modes())


def chat_token_limit(chat_id):
    with STATE_LOCK:
        return int(TOKEN_LIMITS.get(chat_id, MAX_TOKENS))


def chat_lock(chat_id):
    with STATE_LOCK:
        lock = CHAT_LOCKS.get(chat_id)
        if lock is None:
            lock = threading.Lock()
            CHAT_LOCKS[chat_id] = lock
        return lock


def reset_chat(chat_id):
    with chat_lock(chat_id):
        HISTORY.pop(chat_id, None)


def _validate_declared_size(size):
    if isinstance(size, int) and size > MAX_IMAGE_BYTES:
        raise ImageInputError(
            f"Изображение слишком большое: максимум {MAX_IMAGE_BYTES // (1024 * 1024)} МБ."
        )


def select_image_attachment(message):
    """Return the largest Telegram photo or a supported image document."""

    photos = [photo for photo in (message.get("photo") or []) if photo.get("file_id")]
    if photos:
        _, selected = max(
            enumerate(photos),
            key=lambda item: (item[1].get("file_size") or 0, item[0]),
        )
        _validate_declared_size(selected.get("file_size"))
        return {
            "file_id": selected["file_id"],
            "declared_mime_type": "image/jpeg",
            "kind": "photo",
        }

    document = message.get("document")
    if not document:
        return None
    mime_type = (document.get("mime_type") or "").lower().split(";", 1)[0].strip()
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise ImageInputError("Поддерживаются только изображения PNG и JPEG.")
    if not document.get("file_id"):
        raise ImageInputError("Telegram не передал идентификатор изображения.")
    _validate_declared_size(document.get("file_size"))
    return {
        "file_id": document["file_id"],
        "declared_mime_type": mime_type,
        "kind": "document",
        "file_name": document.get("file_name"),
    }


def select_claw_attachment(message):
    """Return a Telegram image or PDF accepted by the Claw project bridge."""

    photos = [photo for photo in (message.get("photo") or []) if photo.get("file_id")]
    if photos:
        _, selected = max(
            enumerate(photos),
            key=lambda item: (item[1].get("file_size") or 0, item[0]),
        )
        _validate_declared_size(selected.get("file_size"))
        return {
            "file_id": selected["file_id"],
            "declared_mime_type": "image/jpeg",
            "kind": "photo",
            "file_name": "telegram-photo.jpg",
        }

    document = message.get("document")
    if not document:
        return None
    mime_type = (document.get("mime_type") or "").lower().split(";", 1)[0].strip()
    if mime_type not in ALLOWED_CLAW_MIME_TYPES:
        raise ImageInputError("В режиме Claw поддерживаются PNG, JPEG и PDF.")
    limit = MAX_PDF_BYTES if mime_type == "application/pdf" else MAX_IMAGE_BYTES
    size = document.get("file_size")
    if isinstance(size, int) and size > limit:
        raise ImageInputError(
            f"Вложение слишком большое: максимум {limit // (1024 * 1024)} МБ."
        )
    if not document.get("file_id"):
        raise ImageInputError("Telegram не передал идентификатор вложения.")
    return {
        "file_id": document["file_id"],
        "declared_mime_type": mime_type,
        "kind": "document",
        "file_name": document.get("file_name"),
    }


def _download_limited(
    url,
    max_bytes=MAX_IMAGE_BYTES,
    timeout=REQUEST_TIMEOUT,
    accept="image/jpeg,image/png",
):
    """Download at most max_bytes, including without Content-Length."""

    request = urllib.request.Request(url, headers={"Accept": accept})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared_length = int(content_length)
            except ValueError:
                declared_length = None
            if declared_length is not None and declared_length > max_bytes:
                raise ImageInputError(
                    f"Изображение слишком большое: максимум {max_bytes // (1024 * 1024)} МБ."
                )

        chunks = []
        total = 0
        while True:
            chunk = response.read(min(64 * 1024, max_bytes + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ImageInputError(
                    f"Изображение слишком большое: максимум {max_bytes // (1024 * 1024)} МБ."
                )
            chunks.append(chunk)
        return b"".join(chunks)


def detect_image_mime_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    raise ImageInputError("Файл не является корректным изображением PNG или JPEG.")


def detect_attachment_mime_type(data):
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return detect_image_mime_type(data)


def download_telegram_image(attachment):
    """Resolve and download a validated Telegram image without persisting it."""

    metadata = tg("getFile", {"file_id": attachment["file_id"]}, timeout=30)
    result = metadata.get("result") or {}
    _validate_declared_size(result.get("file_size"))
    file_path = result.get("file_path")
    if not file_path:
        raise ImageInputError("Telegram не вернул путь к изображению.")
    safe_path = urllib.parse.quote(file_path, safe="/")
    image = _download_limited(f"{FILE_API}/{safe_path}")
    if not image:
        raise ImageInputError("Telegram вернул пустое изображение.")
    actual_mime_type = detect_image_mime_type(image)
    if actual_mime_type != attachment["declared_mime_type"]:
        raise ImageInputError("Тип содержимого изображения не совпадает с типом Telegram.")
    return image, actual_mime_type


def download_telegram_attachment(attachment):
    """Resolve and download a validated image/PDF for the Claw bridge."""

    metadata = tg("getFile", {"file_id": attachment["file_id"]}, timeout=30)
    result = metadata.get("result") or {}
    declared_mime_type = attachment["declared_mime_type"]
    limit = MAX_PDF_BYTES if declared_mime_type == "application/pdf" else MAX_IMAGE_BYTES
    size = result.get("file_size")
    if isinstance(size, int) and size > limit:
        raise ImageInputError(
            f"Вложение слишком большое: максимум {limit // (1024 * 1024)} МБ."
        )
    file_path = result.get("file_path")
    if not file_path:
        raise ImageInputError("Telegram не вернул путь к вложению.")
    safe_path = urllib.parse.quote(file_path, safe="/")
    data = _download_limited(
        f"{FILE_API}/{safe_path}",
        max_bytes=limit,
        accept="image/jpeg,image/png,application/pdf",
    )
    if not data:
        raise ImageInputError("Telegram вернул пустое вложение.")
    actual_mime_type = detect_attachment_mime_type(data)
    if actual_mime_type != declared_mime_type:
        raise ImageInputError("Тип содержимого вложения не совпадает с типом Telegram.")
    return data, actual_mime_type


def multimodal_user_content(user_text, image, mime_type):
    encoded = base64.b64encode(image).decode("ascii")
    return [
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime_type};base64,{encoded}"},
        },
        {"type": "text", "text": user_text or DEFAULT_IMAGE_PROMPT},
    ]


def llm_chat(messages, max_tokens, temperature=TEMPERATURE, timeout=REQUEST_TIMEOUT):
    payload = {
        "messages": messages,
        "max_tokens": int(max_tokens),
        "temperature": temperature,
        "top_p": TOP_P,
        "top_k": TOP_K,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": ENABLE_THINKING},
    }
    started = time.monotonic()
    response = http_json(
        f"{LLM_BASE_URL}/v1/chat/completions",
        payload,
        timeout=timeout,
    )
    elapsed = time.monotonic() - started
    content = response.get("choices", [{}])[0].get("message", {}).get("content") or ""
    usage = response.get("usage", {}) or {}
    return content.strip(), usage, elapsed


def llm_answer(chat_id, user_text, image=None, image_mime_type=None):
    """Answer while preserving order/history for one chat only."""

    with chat_lock(chat_id):
        history = HISTORY.setdefault(chat_id, [])
        messages = [{"role": "system", "content": load_system_prompt()}]
        messages.extend(history[-HISTORY_TURNS * 2:])

        current_content = user_text
        history_content = user_text
        if image is not None:
            current_content = multimodal_user_content(user_text, image, image_mime_type)
            history_content = (
                f"[Приложено изображение {image_mime_type}]\n"
                f"{user_text or DEFAULT_IMAGE_PROMPT}"
            )
        messages.append({"role": "user", "content": current_content})

        content, usage, elapsed = llm_chat(
            messages,
            chat_token_limit(chat_id),
        )
        history.append({"role": "user", "content": history_content})
        history.append({"role": "assistant", "content": content})
        del history[:-HISTORY_TURNS * 2]
        print(
            f"answered chat={chat_id} elapsed={elapsed:.2f}s usage={usage}",
            flush=True,
        )
        return content


def claw_request(path, payload, timeout=CLAW_REQUEST_TIMEOUT):
    if not CLAW_BRIDGE_TOKEN:
        raise ClawBridgeError("Интеграция Claw не настроена: отсутствует bridge token.")
    try:
        response = http_json(
            f"{CLAW_BRIDGE_URL}{path}",
            payload,
            timeout=timeout,
            extra_headers={"Authorization": f"Bearer {CLAW_BRIDGE_TOKEN}"},
        )
    except urllib.error.HTTPError as error:
        try:
            value = json.loads(error.read().decode("utf-8", "replace"))
            message = value.get("error") or str(error)
        except Exception:
            message = str(error)
        raise ClawBridgeError(message) from error
    except Exception as error:
        raise ClawBridgeError(f"bridge недоступен: {error}") from error
    if not response.get("ok", False):
        raise ClawBridgeError(response.get("error") or "неизвестная ошибка bridge")
    return response


def claw_vision_context(user_text, data, mime_type):
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        return ""
    messages = [
        {
            "role": "system",
            "content": f"{load_system_prompt()}\n\n{CLAW_VISION_SYSTEM_PROMPT}",
        },
        {
            "role": "user",
            "content": multimodal_user_content(
                user_text or DEFAULT_IMAGE_PROMPT,
                data,
                mime_type,
            ),
        },
    ]
    content, usage, elapsed = llm_chat(
        messages,
        max_tokens=CLAW_VISION_MAX_TOKENS,
        temperature=0,
        timeout=max(REQUEST_TIMEOUT, 600),
    )
    print(
        f"Claw attachment vision preprocessing elapsed={elapsed:.2f}s usage={usage}",
        flush=True,
    )
    return content


def claw_answer(chat_id, user_id, text, attachment=None, attachment_data=None):
    payload = {"chat_id": chat_id, "user_id": user_id, "text": text}
    if attachment is not None and attachment_data is not None:
        mime_type = attachment["declared_mime_type"]
        payload["attachment"] = {
            "mime_type": mime_type,
            "file_name": attachment.get("file_name"),
            "data_base64": base64.b64encode(attachment_data).decode("ascii"),
        }
        try:
            payload["vision_context"] = claw_vision_context(
                text, attachment_data, mime_type
            )
        except Exception as error:
            print("Claw Vision preprocessing warning:", repr(error), flush=True)
            payload["vision_context"] = ""
    return claw_request("/v1/message", payload)["message"]


def claw_projects_text(chat_id):
    response = claw_request("/v1/projects", {"chat_id": chat_id}, timeout=30)
    active = response.get("active_project_id")
    projects = response.get("projects") or []
    if not projects:
        return "Проектов Claw пока нет. Нажми «🆕 Новый проект»."
    lines = ["Проекты Claw:"]
    for project in projects[:20]:
        marker = "▶" if project.get("id") == active else "•"
        session = "сессия сохранена" if project.get("session_id") else "без сообщений"
        lines.append(
            f"{marker} {project.get('id')} — {project.get('name')} "
            f"({project.get('status')}, {session})"
        )
    lines.append("Открыть: /project ID")
    return "\n".join(lines)


def claw_status_text(chat_id):
    response = claw_request("/v1/status", {"chat_id": chat_id}, timeout=30)
    project = response.get("project")
    if not project:
        project_text = "нет активного проекта"
    else:
        project_text = (
            f"{project.get('name')} [{project.get('id')}], "
            f"session={project.get('session_id') or 'ещё не создана'}"
        )
    return (
        f"Claw: {'выполняет задачу' if response.get('running') else 'готов'}\n"
        f"Проект: {project_text}\n"
        f"Auto-compact: {response.get('auto_compact_input_tokens')} input tokens\n"
        f"Parallel turns: {response.get('max_concurrent')}\n"
        f"Agents: {'включены (один уровень)' if response.get('agents_enabled') else 'выключены'}\n"
        f"Permissions: {response.get('permission_mode') or '?'}\n"
        f"Max output/call: {response.get('gemma_max_output_tokens') or '?'} tokens"
    )


def shell_output(command, default="?"):
    try:
        return subprocess.check_output(
            command,
            shell=True,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=5,
        ).strip()
    except Exception:
        return default


def status_text(chat_id):
    try:
        health = json.dumps(http_json(f"{LLM_BASE_URL}/health", timeout=20), ensure_ascii=False)
    except Exception as error:
        health = repr(error)
    load = shell_output("cat /proc/loadavg")
    memory = shell_output("awk '/MemAvailable|MemTotal/ {print $1,$2,$3}' /proc/meminfo")
    tunnel = shell_output("systemctl is-active llm-srv1-forward.service")
    claw_status = "not configured"
    if CLAW_BRIDGE_TOKEN:
        try:
            claw_status = claw_status_text(chat_id)
        except Exception as error:
            claw_status = f"unavailable: {error}"
    return (
        f"mode: {chat_mode(chat_id)}\n"
        f"LLM health: {health}\n"
        f"max_tokens: {chat_token_limit(chat_id)}\n"
        f"generation: temperature={TEMPERATURE}, top_p={TOP_P}, top_k={TOP_K}, "
        f"enable_thinking={ENABLE_THINKING}\n"
        f"vision: PNG/JPEG, max {MAX_IMAGE_BYTES // (1024 * 1024)} MiB\n"
        f"loadavg(jump): {load}\n"
        f"mem(jump):\n{memory}\n"
        f"tunnel: {tunnel}\n"
        f"LLM_BASE_URL: {LLM_BASE_URL}\n"
        f"{claw_status}"
    )


def bench_text(max_tokens=128):
    max_tokens = max(16, min(int(max_tokens), 1024))
    messages = [
        {"role": "system", "content": "Ты генератор текста для измерения скорости. Отвечай только текстом."},
        {"role": "user", "content": "Сгенерируй связный русский текст для бенчмарка скорости."},
    ]
    content, usage, elapsed = llm_chat(
        messages,
        max_tokens=max_tokens,
        temperature=0.8,
        timeout=max(REQUEST_TIMEOUT, 600),
    )
    completion_tokens = int(usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or 0)
    tokens_per_second = completion_tokens / elapsed if elapsed > 0 and completion_tokens else 0.0
    return (
        f"Bench max_tokens={max_tokens}\n"
        f"elapsed: {elapsed:.2f}s\n"
        f"completion_tokens: {completion_tokens}\n"
        f"total_tokens: {total_tokens}\n"
        f"speed: {tokens_per_second:.2f} tok/s\n\n"
        f"Фрагмент ответа:\n{content[:900]}"
    )


def parse_int_arg(text, default):
    parts = text.split()
    if len(parts) >= 2:
        try:
            return int(parts[1])
        except ValueError:
            pass
    return default


def handle_message(message):
    chat = message.get("chat", {})
    user = message.get("from", {})
    chat_id = chat.get("id")
    user_id = user.get("id")
    text = (message.get("text") or message.get("caption") or "").strip()
    message_id = message.get("message_id")
    if chat_id is None or user_id is None:
        return

    username = normalize_username(user)
    if not allowed_user(user):
        suffix = f" или username @{username}" if username else ""
        send_message(
            chat_id,
            f"Доступ закрыт. Напиши владельцу добавить твой Telegram ID: {user_id}{suffix}",
            reply_to=message_id,
        )
        return
    if text == "/" or text.startswith("/start") or text.startswith("/help"):
        send_message(
            chat_id,
            "Два режима:\n"
            "• 💬 Gemma — обычный чат и Vision;\n"
            "• 🛠 Claw Cod — постоянный проект с файлами, tools и восстановлением "
            "сессии после перезапуска.\n\n"
            "Фото/PNG/JPEG можно просто пересылать в оба режима. В Claw также "
            "поддерживается PDF; OCR запускается агентом автоматически при необходимости, "
            "отдельного OCR-меню нет. Длинные ответы Gemma и Claw автоматически "
            "приходят несколькими сообщениями без обрезания. Claw может запустить "
            "одного фонового агента внутри текущего задания.\n\n" + commands_text(),
            reply_to=message_id,
        )
        return
    if text.startswith("/whoami"):
        send_message(
            chat_id,
            f"chat_id={chat_id}\nuser_id={user_id}\nusername=@{username or '-'}",
            reply_to=message_id,
        )
        return
    if text in {"💬 Gemma", "/gemma"} or text.startswith("/gemma "):
        set_chat_mode(chat_id, MODE_GEMMA)
        send_message(
            chat_id,
            "Режим Gemma включён. История обычного чата сохранена отдельно.",
            reply_to=message_id,
        )
        return
    if text in {"🛠 Claw Cod", "/claw"} or text.startswith("/claw "):
        try:
            status = claw_status_text(chat_id)
            set_chat_mode(chat_id, MODE_CLAW)
            send_message(
                chat_id,
                "Режим Claw включён. " + status,
                reply_to=message_id,
            )
        except ClawBridgeError as error:
            send_message(chat_id, f"Claw недоступен: {error}", reply_to=message_id)
        return
    if text == "🆕 Новый проект" or text.startswith("/newclaw"):
        name = text.partition(" ")[2].strip() if text.startswith("/newclaw") else ""
        try:
            response = claw_request(
                "/v1/projects/new",
                {"chat_id": chat_id, "name": name},
                timeout=30,
            )
            set_chat_mode(chat_id, MODE_CLAW)
            project = response["project"]
            send_message(
                chat_id,
                f"Создан новый проект: {project['name']} [{project['id']}].\n"
                "Следующее сообщение станет первым заданием новой Claw-сессии.",
                reply_to=message_id,
            )
        except ClawBridgeError as error:
            send_message(chat_id, f"Не удалось создать проект: {error}", reply_to=message_id)
        return
    if text in {"📁 Проекты", "/projects"} or text.startswith("/projects "):
        try:
            send_message(chat_id, claw_projects_text(chat_id), reply_to=message_id)
        except ClawBridgeError as error:
            send_message(chat_id, f"Не удалось получить проекты: {error}", reply_to=message_id)
        return
    if text.startswith("/project"):
        project_id = text.partition(" ")[2].strip()
        if not project_id:
            send_message(chat_id, "Использование: /project ID", reply_to=message_id)
            return
        try:
            response = claw_request(
                "/v1/projects/switch",
                {"chat_id": chat_id, "project_id": project_id},
                timeout=30,
            )
            set_chat_mode(chat_id, MODE_CLAW)
            project = response["project"]
            send_message(
                chat_id,
                f"Открыт проект: {project['name']} [{project['id']}]. "
                "Сохранённая Claw-сессия будет продолжена.",
                reply_to=message_id,
            )
        except ClawBridgeError as error:
            send_message(chat_id, f"Не удалось открыть проект: {error}", reply_to=message_id)
        return
    if text in {"⛔ Остановить Claw", "/stop"} or text.startswith("/stop "):
        try:
            response = claw_request("/v1/stop", {"chat_id": chat_id}, timeout=15)
            result = (
                "Текущая операция Claw остановлена. Проект и завершённый контекст сохранены."
                if response.get("stopped")
                else "У Claw сейчас нет выполняющейся операции."
            )
            send_message(chat_id, result, reply_to=message_id)
        except ClawBridgeError as error:
            send_message(chat_id, f"Не удалось остановить Claw: {error}", reply_to=message_id)
        return
    if text in {"❌ Закрыть проект", "/closeclaw"} or text.startswith("/closeclaw "):
        try:
            response = claw_request(
                "/v1/projects/close", {"chat_id": chat_id}, timeout=15
            )
            project = response.get("project")
            description = (
                f"Проект {project['name']} закрыт и сохранён. Его можно открыть через /projects."
                if project
                else "Активного проекта Claw нет."
            )
            send_message(chat_id, description, reply_to=message_id)
        except ClawBridgeError as error:
            send_message(chat_id, f"Не удалось закрыть проект: {error}", reply_to=message_id)
        return
    if text in RESET_TEXTS or text.startswith("/reset"):
        reset_chat(chat_id)
        send_message(
            chat_id,
            "Готово: старая история очищена. Следующее сообщение начнёт чистую сессию.",
            reply_to=message_id,
        )
        return
    if text.startswith("/tokens"):
        limit = max(64, min(parse_int_arg(text, MAX_TOKENS), MAX_RESPONSE_TOKENS))
        with STATE_LOCK:
            TOKEN_LIMITS[chat_id] = limit
        send_message(
            chat_id,
            f"Лимит ответа для этого чата: {limit} tokens. Для скорости: /bench {min(limit, 512)}",
            reply_to=message_id,
        )
        return
    if text.startswith("/permissions"):
        send_message(
            chat_id,
            "Claw работает внутри отдельной одноразовой VM в режиме "
            "danger-full-access: команды и установка пакетов выполняются автоматически, "
            "без промежуточного подтверждения в Telegram. Остановить текущую операцию "
            "можно кнопкой «⛔ Остановить Claw» или /stop. Доступа к VM120, "
            "гипервизору, Telegram-токену и GitHub write-ключу у агента нет.",
            reply_to=message_id,
        )
        return
    if text.startswith("/status"):
        send_message(chat_id, status_text(chat_id), reply_to=message_id)
        return
    if text.startswith("/bench"):
        send_typing(chat_id)
        try:
            send_message(
                chat_id,
                bench_text(parse_int_arg(text, 128)),
                reply_to=message_id,
            )
        except Exception as error:
            print("bench error:", repr(error), flush=True)
            send_message(chat_id, "Ошибка бенчмарка записана в журнал.", reply_to=message_id)
        return

    current_mode = chat_mode(chat_id)
    try:
        attachment = (
            select_claw_attachment(message)
            if current_mode == MODE_CLAW
            else select_image_attachment(message)
        )
    except ImageInputError as error:
        send_message(chat_id, str(error), reply_to=message_id)
        return
    if not text and attachment is None:
        return

    send_typing(chat_id)
    try:
        attachment_data = None
        attachment_mime_type = None
        if attachment is not None:
            if current_mode == MODE_CLAW:
                attachment_data, attachment_mime_type = download_telegram_attachment(
                    attachment
                )
            else:
                attachment_data, attachment_mime_type = download_telegram_image(
                    attachment
                )
        if current_mode == MODE_CLAW:
            answer = claw_answer(
                chat_id,
                user_id,
                text,
                attachment=attachment,
                attachment_data=attachment_data,
            )
        else:
            answer = llm_answer(
                chat_id,
                text,
                image=attachment_data,
                image_mime_type=attachment_mime_type,
            )
        send_message(chat_id, answer, reply_to=message_id)
    except ImageInputError as error:
        send_message(chat_id, str(error), reply_to=message_id)
    except Exception as error:
        print("ERROR handling message:", repr(error), flush=True)
        traceback.print_exc()
        send_message(
            chat_id,
            "Ошибка при запросе к локальной модели или Claw. Подробности записаны в журнал сервиса.",
            reply_to=message_id,
        )


def is_control_message(message):
    text = (message.get("text") or message.get("caption") or "").strip()
    return text in CONTROL_TEXTS or any(
        text.startswith(prefix)
        for prefix in ("/stop ", "/closeclaw ", "/newclaw ")
    )


def submit_message(message):
    executor = CONTROL_EXECUTOR if is_control_message(message) else EXECUTOR
    future = executor.submit(handle_message, message)

    def done(completed):
        try:
            completed.result()
        except Exception as error:
            print("worker error:", repr(error), flush=True)
            traceback.print_exc()

    future.add_done_callback(done)


def wait_for_telegram():
    while True:
        try:
            try:
                tg("deleteWebhook", {"drop_pending_updates": False}, timeout=30)
            except Exception as error:
                print("deleteWebhook warning:", repr(error), flush=True)
            me = tg("getMe", {}, timeout=30)
            result = me.get("result", {})
            try:
                tg("setMyCommands", {"commands": COMMANDS}, timeout=30)
                tg("setChatMenuButton", {"menu_button": {"type": "commands"}}, timeout=30)
                print("Telegram commands menu: installed", flush=True)
            except Exception as error:
                print("Telegram commands menu warning:", repr(error), flush=True)

            if not ALLOWED and not ALLOWED_USERNAMES:
                allowed_description = "ALL"
            else:
                allowed_description = {
                    "ids": sorted(ALLOWED),
                    "usernames": sorted(ALLOWED_USERNAMES),
                }
            print(
                "Telegram bot connected:",
                result.get("username") or result.get("first_name") or result.get("id"),
                flush=True,
            )
            print(
                "LLM endpoint:",
                LLM_BASE_URL,
                "Claw bridge:",
                CLAW_BRIDGE_URL,
                "allowed:",
                allowed_description,
                "max_workers:",
                BOT_MAX_CONCURRENT_REQUESTS,
                "vision_max_bytes:",
                MAX_IMAGE_BYTES,
                flush=True,
            )
            return
        except Exception as error:
            print("waiting for Telegram API:", repr(error), flush=True)
            time.sleep(5)


def main():
    if not TOKEN:
        print("FATAL: TELEGRAM_BOT_TOKEN is empty", flush=True)
        return 2
    wait_for_telegram()
    offset = None
    while True:
        try:
            payload = {"timeout": 50, "allowed_updates": ["message"]}
            if offset is not None:
                payload["offset"] = offset
            data = tg("getUpdates", payload, timeout=70)
            for update in data.get("result", []):
                offset = update.get("update_id", 0) + 1
                message = update.get("message")
                if message:
                    submit_message(message)
        except KeyboardInterrupt:
            raise
        except Exception as error:
            print("poll error:", repr(error), flush=True)
            time.sleep(5)


if __name__ == "__main__":
    sys.exit(main())
