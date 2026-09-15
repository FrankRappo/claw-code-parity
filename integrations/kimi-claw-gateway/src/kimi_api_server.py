from __future__ import annotations

import asyncio
import base64
import ctypes
import hashlib
import hmac
import html
import json
import logging
import os
import random
import re
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("kimi-api")

KIMI_WEB_BASE_URL = os.getenv("KIMI_WEB_BASE_URL", "https://www.kimi.com").rstrip("/")
KIMI_AUTH_BASE_URL = os.getenv("KIMI_AUTH_BASE_URL", "https://auth.kimi.com").rstrip("/")
KIMI_PROXY = os.getenv("KIMI_PROXY", "").strip()
KIMI_UPSTREAM_PROTOCOL = os.getenv("KIMI_UPSTREAM_PROTOCOL", "connect_v2")
KIMI_UPSTREAM_MODEL = os.getenv("KIMI_UPSTREAM_MODEL", "k2d6-chat")
_KIMI_K3_DEFAULT = KIMI_UPSTREAM_MODEL.startswith("k3-")
KIMI_SCENARIO = os.getenv(
    "KIMI_SCENARIO",
    "SCENARIO_OK_COMPUTER" if _KIMI_K3_DEFAULT else "SCENARIO_CHAT",
)
KIMI_KIMIPLUS_ID = os.getenv(
    "KIMI_KIMIPLUS_ID",
    "ok-computer" if _KIMI_K3_DEFAULT else "",
)
KIMI_REASONING_EFFORT = os.getenv(
    "KIMI_REASONING_EFFORT",
    "REASONING_EFFORT_HIGH" if _KIMI_K3_DEFAULT else "REASONING_EFFORT_NONE",
)
KIMI_CONTEXT_LENGTH = os.getenv("KIMI_CONTEXT_LENGTH", "CONTEXT_LENGTH_L")
KIMI_ENABLE_PLUGIN = os.getenv(
    "KIMI_ENABLE_PLUGIN",
    "1" if _KIMI_K3_DEFAULT else "0",
) == "1"
KIMI_SESSION_FILE = Path(
    os.getenv("KIMI_SESSION_FILE", "/home/clawrun/.config/kimi-wrapper/session.json")
)
TOKEN_REFRESH_MARGIN_SECONDS = int(os.getenv("KIMI_TOKEN_REFRESH_MARGIN", "90"))
LOG_KIMI_EVENT_SHAPES = os.getenv("KIMI_LOG_EVENT_SHAPES", "0") == "1"
UPSTREAM_MAX_ATTEMPTS = max(1, int(os.getenv("KIMI_UPSTREAM_MAX_ATTEMPTS", "3")))
UPSTREAM_RETRY_BASE_SECONDS = max(
    0.05, float(os.getenv("KIMI_UPSTREAM_RETRY_BASE_SECONDS", "0.5"))
)
FORMAT_REPAIR_ATTEMPTS = max(1, int(os.getenv("KIMI_FORMAT_REPAIR_ATTEMPTS", "3")))
MAX_CONCURRENT_UPSTREAM = max(1, int(os.getenv("KIMI_MAX_CONCURRENT_UPSTREAM", "2")))
DEFAULT_SHELL_TIMEOUT_MS = max(
    1_000, int(os.getenv("KIMI_DEFAULT_SHELL_TIMEOUT_MS", "30000"))
)
REQUESTS_PER_MINUTE = max(
    0.1, float(os.getenv("KIMI_REQUESTS_PER_MINUTE", "10"))
)
REQUEST_INTERVAL_SECONDS = 60.0 / REQUESTS_PER_MINUTE
GATEWAY_API_KEY = os.getenv("KIMI_GATEWAY_API_KEY", "").strip()
RUNTIME_PLATFORM = "windows" if sys.platform == "win32" else "linux"
RUNTIME_HOME = str(Path.home())
PERSISTENT_CHAT = os.getenv("KIMI_PERSISTENT_CHAT", "0") == "1"
MAX_PERSISTENT_MESSAGES = max(
    4, int(os.getenv("KIMI_MAX_PERSISTENT_MESSAGES", "1000"))
)
MAX_PERSISTENT_CONTEXT_TOKENS = max(
    8_000, int(os.getenv("KIMI_MAX_PERSISTENT_CONTEXT_TOKENS", "200000"))
)
ALL_CLAW_TOOLS = "<all-claw-tools>"
CLAW_COMPACTION_PREFIX = (
    "This session is being continued from a previous conversation that ran out of context."
)

app = FastAPI(title="Kimi Web OpenAI Gateway", version="1.0.0")
upstream_semaphore = asyncio.Semaphore(MAX_CONCURRENT_UPSTREAM)


class GatewayMetrics:
    def __init__(self):
        self._lock = threading.Lock()
        self._counters: dict[str, int] = {}
        self._latency_ms_total = 0

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[name] = self._counters.get(name, 0) + amount

    def observe_request(self, elapsed_seconds: float) -> None:
        with self._lock:
            self._counters["http_requests_total"] = self._counters.get("http_requests_total", 0) + 1
            self._latency_ms_total += int(elapsed_seconds * 1000)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests = self._counters.get("http_requests_total", 0)
            return {
                **self._counters,
                "http_request_latency_ms_total": self._latency_ms_total,
                "http_request_latency_ms_average": (
                    round(self._latency_ms_total / requests, 3) if requests else 0
                ),
            }


metrics = GatewayMetrics()


class RequestPacer:
    """Serialize request admission and keep starts evenly spaced."""

    def __init__(
        self,
        interval_seconds: float,
        *,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ):
        self.interval_seconds = max(0.0, interval_seconds)
        self._clock = clock
        self._sleep = sleep
        self._lock = asyncio.Lock()
        self._next_start = 0.0

    async def wait(self) -> float:
        async with self._lock:
            delay = max(0.0, self._next_start - self._clock())
            if delay:
                metrics.increment("paced_requests_total")
                metrics.increment("pacing_wait_ms_total", int(delay * 1000))
                await self._sleep(delay)
            started = self._clock()
            self._next_start = started + self.interval_seconds
            return delay


request_pacer = RequestPacer(REQUEST_INTERVAL_SECONDS)


def _is_k3_upstream() -> bool:
    return KIMI_UPSTREAM_MODEL.startswith("k3-")


def _is_retryable_status(status_code: int) -> bool:
    return status_code in {408, 429, 500, 502, 503, 504}


def _is_retryable_connect_error(code: str, message: str) -> bool:
    return code == "resource_exhausted" and "session in progress" in message.lower()


async def _retry_delay(attempt: int) -> None:
    delay = UPSTREAM_RETRY_BASE_SECONDS * (2**attempt)
    await asyncio.sleep(delay + random.uniform(0, delay * 0.2))


@app.middleware("http")
async def request_metrics_middleware(request: Request, call_next):
    started = time.perf_counter()
    try:
        if GATEWAY_API_KEY and request.url.path.startswith("/v1/"):
            supplied = request.headers.get("authorization", "")
            expected = f"Bearer {GATEWAY_API_KEY}"
            if not hmac.compare_digest(supplied.encode(), expected.encode()):
                metrics.increment("auth_failures_total")
                return JSONResponse(
                    status_code=401,
                    content={
                        "error": {
                            "message": "Missing or invalid local gateway API key",
                            "type": "authentication_error",
                        }
                    },
                    headers={"WWW-Authenticate": "Bearer"},
                )
        response = await call_next(request)
    finally:
        metrics.observe_request(time.perf_counter() - started)
    return response


class Message(BaseModel):
    role: str
    content: Any = ""
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model: str = "kimi-web"
    messages: list[Message]
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    tools: Optional[list[dict[str, Any]]] = None
    tool_choice: Any = None


class _DataBlob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_ulong), ("data", ctypes.POINTER(ctypes.c_byte))]


def _dpapi_transform(data: bytes, *, protect: bool) -> bytes:
    if sys.platform != "win32":
        raise RuntimeError("DPAPI session files are supported only on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_wchar_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = ctypes.c_int
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_ulong,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    buffer = ctypes.create_string_buffer(data)
    input_blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    output_blob = _DataBlob()
    if protect:
        succeeded = crypt32.CryptProtectData(
            ctypes.byref(input_blob),
            "KimiClawGateway",
            None,
            None,
            None,
            0x1,
            ctypes.byref(output_blob),
        )
    else:
        succeeded = crypt32.CryptUnprotectData(
            ctypes.byref(input_blob),
            None,
            None,
            None,
            None,
            0x1,
            ctypes.byref(output_blob),
        )
    if not succeeded:
        raise RuntimeError(f"Windows DPAPI operation failed: {ctypes.get_last_error()}")
    try:
        return ctypes.string_at(output_blob.data, output_blob.size)
    finally:
        kernel32.LocalFree(output_blob.data)


def _dpapi_protect(data: bytes) -> bytes:
    return _dpapi_transform(data, protect=True)


def _dpapi_unprotect(data: bytes) -> bytes:
    return _dpapi_transform(data, protect=False)


class SessionStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)

    @property
    def dpapi_protected(self) -> bool:
        return self.path.suffix.lower() == ".dpapi"

    def load(self) -> dict[str, Any]:
        try:
            raw = self.path.read_bytes()
            if self.dpapi_protected:
                raw = _dpapi_unprotect(raw)
            data = json.loads(raw.decode("utf-8"))
        except FileNotFoundError as exc:
            raise RuntimeError(f"Kimi session file is missing: {self.path}") from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Kimi session file is invalid JSON") from exc
        for key in ("access_token", "refresh_token", "headers"):
            if not data.get(key):
                raise RuntimeError(f"Kimi session is missing {key}")
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            raw = json.dumps(
                data,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if self.dpapi_protected:
                raw = _dpapi_protect(raw)
            tmp.write_bytes(raw)
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
            os.chmod(self.path, 0o600)
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass


def jwt_expiry(token: str) -> int:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return int(json.loads(base64.urlsafe_b64decode(payload))["exp"])
    except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return 0


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)
    return json.dumps(content, ensure_ascii=False)


TOOL_PROTOCOL = """[TOOL_PROTOCOL]
You can call the tools listed in AVAILABLE_TOOLS. When a tool is needed, emit
only this protocol block and do not claim that the tool already ran:
<|tool_calls_section_begin|><|tool_call_begin|>functions.TOOL_NAME:0<|tool_call_argument_begin|>{\"argument\":\"value\"}<|tool_call_end|><|tool_calls_section_end|>
Arguments must be a valid JSON object using ASCII double quotes. Escape every
backslash inside JSON strings. Use exactly one block per tool call; multiple
blocks are allowed. After TOOL_RESULT messages, either call another tool or
return the final answer as ordinary text without protocol markers.
`[ASSISTANT_TOOL_CALLS]` and `[TOOL_RESULT]` are transcript annotations from
earlier turns. Never emit those annotations yourself and never invent a tool
result; Claw must execute every call and return the real result.
These are real tools on the user's local machine, not hypothetical examples.
When the user provides a filesystem path or asks you to inspect an existing
local project, call a file/search/shell tool before discussing limitations.
When the user asks whether you already inspected something, answer directly
with concrete findings from the tool results already present in the transcript.
"""


def messages_to_prompt(
    messages: list[Message],
    tools: Optional[list[dict[str, Any]]] = None,
    tool_choice: Any = None,
) -> str:
    if not messages:
        raise ValueError("messages must not be empty")
    if len(messages) == 1 and messages[0].role == "user" and not tools:
        return _content_to_text(messages[0].content)

    blocks: list[str] = []
    for message in messages:
        role = message.role.upper()
        text = _content_to_text(message.content)
        if message.role == "assistant" and message.tool_calls:
            if text:
                blocks.append(f"[ASSISTANT]\n{text}")
            blocks.append(
                "[ASSISTANT_TOOL_CALLS]\n"
                + json.dumps(message.tool_calls, ensure_ascii=False, separators=(",", ":"))
            )
        elif message.role == "tool":
            text = _prepare_tool_result(message.name, text)
            text = _clip_tool_result(text)
            label = f"[TOOL_RESULT id={message.tool_call_id or ''}"
            if message.name:
                label += f" name={message.name}"
            blocks.append(f"{label}]\n{text}")
        else:
            blocks.append(f"[{role}]\n{text}")

    if tools:
        tool_data = json.dumps(
            _compact_tool_specs(tools),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        choice_data = json.dumps(tool_choice, ensure_ascii=False, separators=(",", ":"))
        blocks.insert(0, f"{TOOL_PROTOCOL}\n[TOOL_CHOICE]\n{choice_data}\n[AVAILABLE_TOOLS]\n{tool_data}")
        required_tools = _required_next_tools(messages, tools, tool_choice)
        if required_tools:
            names = ",".join(required_tools)
            blocks.insert(
                1,
                "[REQUIRED_NEXT_TOOL]\n"
                f"Call one of these tools now: {names}. Emit only a tool protocol block; "
                "do not write a planning preamble.",
            )
    return "\n\n".join(blocks)


def _clip_tool_result(text: str, limit: int = 16_000) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n\n[TRUNCATED_TOOL_RESULT]\n\n{text[-half:]}"


def _prepare_tool_result(name: Optional[str], text: str) -> str:
    if str(name or "").lower() not in {"glob", "glob_search"}:
        return text
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return text
    if not isinstance(value, dict) or not isinstance(value.get("filenames"), list):
        return text
    ignored = ("\\.venv\\", "/.venv/", "__pycache__", "\\.git\\", "/.git/", ".egg-info", "\\target\\", "/target/")
    filenames = [
        item
        for item in value["filenames"]
        if isinstance(item, str) and not any(marker in item for marker in ignored)
    ]
    value["filenames"] = filenames[:200]
    value["numFiles"] = len(filenames)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _compact_tool_specs(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    compact: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function", {}) if isinstance(tool, dict) else {}
        parameters = function.get("parameters", {}) if isinstance(function, dict) else {}
        properties = parameters.get("properties", {}) if isinstance(parameters, dict) else {}
        compact_properties: dict[str, Any] = {}
        if isinstance(properties, dict):
            for name, schema in properties.items():
                if not isinstance(schema, dict):
                    compact_properties[str(name)] = {}
                    continue
                keep = {
                    key: schema[key]
                    for key in ("type", "enum", "const", "items")
                    if key in schema
                }
                compact_properties[str(name)] = keep
        compact.append(
            {
                "name": function.get("name", ""),
                "description": str(function.get("description", ""))[:240],
                "required": parameters.get("required", []),
                "properties": compact_properties,
            }
        )
    return compact


def _required_next_tools(
    messages: list[Message],
    tools: list[dict[str, Any]],
    tool_choice: Any,
) -> list[str]:
    available = [
        str(tool.get("function", {}).get("name", ""))
        for tool in tools
        if isinstance(tool, dict)
    ]
    available = [name for name in available if name]
    available_by_lower = {name.lower(): name for name in available}
    latest_user_index = max(
        (index for index, message in enumerate(messages) if message.role == "user"),
        default=-1,
    )
    used = {
        str(call.get("function", {}).get("name", "")).lower()
        for message in messages[latest_user_index + 1 :]
        for call in (message.tool_calls or [])
        if isinstance(call, dict)
    }

    if isinstance(tool_choice, dict):
        forced = str(tool_choice.get("function", {}).get("name", ""))
        if not forced:
            raise ValueError("tool_choice function name is missing")
        canonical = available_by_lower.get(forced.lower())
        if not canonical:
            raise ValueError(f"tool_choice requested unavailable tool: {forced!r}")
        return [canonical] if canonical.lower() not in used else []

    if tool_choice == "required" and not used:
        return ["<any-available-tool>"]
    return []


def _conversation_prompt(
    messages: list[Message],
    tool_result_limit: Optional[int] = None,
    older_tool_result_limit: Optional[int] = None,
) -> str:
    latest_tool_index = max(
        (index for index, message in enumerate(messages) if message.role == "tool"),
        default=-1,
    )
    conversation: list[Message] = []
    for index, message in enumerate(messages):
        if message.role == "system":
            continue
        if message.role == "tool" and tool_result_limit:
            content = _content_to_text(message.content)
            limit = tool_result_limit
            if older_tool_result_limit and index != latest_tool_index:
                limit = older_tool_result_limit
            content = _clip_tool_result(content, limit=limit)
            conversation.append(message.model_copy(update={"content": content}))
        else:
            conversation.append(message)
    return messages_to_prompt(conversation) if conversation else ""


def _native_system_prompt(
    request: ChatCompletionRequest,
    tool_mode: Optional[str] = None,
    required_tools: Optional[list[str]] = None,
) -> str:
    system_text = "\n\n".join(
        _content_to_text(message.content)
        for message in request.messages
        if message.role == "system" and _content_to_text(message.content).strip()
    )
    selected = list(request.tools or []) if tool_mode == ALL_CLAW_TOOLS else []
    if tool_mode == ALL_CLAW_TOOLS:
        compact = _compact_tool_specs(selected)
        device_contract = (
            "# Native Claw tool registry\n"
            "Every entry in AVAILABLE_CLAW_TOOLS is registered as a real native "
            "client-side device tool under its exact name.\n"
            "[AVAILABLE_CLAW_TOOLS]\n"
            f"{json.dumps(compact, ensure_ascii=False, separators=(',', ':'))}\n"
            "When local action or inspection is needed, choose the best real tool and "
            "emit exactly one call using that exact registered name:\n"
            "<function_calls><invoke name=\"REAL_TOOL_NAME\">"
            "<parameter name=\"ARGUMENT_NAME\">ARGUMENT_VALUE</parameter>"
            "</invoke></function_calls>\n"
            "Use one parameter per argument according to that tool's schema. End "
            "immediately after </function_calls>. Never invent a result."
        )
    else:
        device_contract = (
            "# Turn completion contract\n"
            "Use the real tool results already present in the conversation. Answer the latest "
            "user message directly and concisely. Do not announce future inspection, repeat a "
            "plan, emit fake tool syntax, or invent results."
        )
    required_contract = ""
    if required_tools:
        if required_tools == ["<any-available-tool>"]:
            required_label = "one applicable tool from AVAILABLE_CLAW_TOOLS"
        else:
            required_label = "one of these tools: " + ", ".join(required_tools)
        required_contract = (
            "# Required action for this turn\n"
            f"You MUST call {required_label} now. A text answer, plan, tutorial, refusal, "
            "or request for the user to do the work is invalid. Emit only the executable "
            "tool call and wait for its real result."
        )
    return "\n\n".join(
        part
        for part in (
            system_text,
            device_contract,
            required_contract,
        )
        if part
    )


def _native_tool_mode(request: ChatCompletionRequest) -> Optional[str]:
    available = [
        str(tool.get("function", {}).get("name", ""))
        for tool in (request.tools or [])
        if isinstance(tool, dict) and tool.get("function", {}).get("name")
    ]
    if not available:
        return None
    return ALL_CLAW_TOOLS


@dataclass(frozen=True)
class ParsedToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ParsedAssistantOutput:
    text: str
    tool_calls: list[ParsedToolCall]
    upstream_chat_id: Optional[str] = None


TOOL_CALL_PATTERN = re.compile(
    r"<\|tool_call_begin\|>\s*(?P<label>.*?)\s*"
    r"<\|tool_call_argument_begin\|>(?P<arguments>.*?)"
    r"<\|tool_call_end\|>",
    re.DOTALL,
)
OPENAI_TOOL_CALLS_MARKER = "[ASSISTANT_TOOL_CALLS]"
TOOL_RESULT_MARKER = "[TOOL_RESULT"
FUNCTION_CALLS_PATTERN = re.compile(
    r"<function_calls>(?P<body>.*?)</function_calls>", re.IGNORECASE | re.DOTALL
)
FUNCTION_CALLS_OPEN_PATTERN = re.compile(r"<function_calls>", re.IGNORECASE)
INVOKE_PATTERN = re.compile(
    r"<invoke\s+name=[\"'](?P<name>[A-Za-z_][A-Za-z0-9_.-]*)[\"']\s*>"
    r"(?P<body>.*?)</invoke>",
    re.IGNORECASE | re.DOTALL,
)
NAMED_PARAMETER_PATTERN = re.compile(
    r"<parameter\s+name=[\"'](?P<name>[A-Za-z_][A-Za-z0-9_.-]*)[\"']\s*>"
    r"(?P<value>.*?)</parameter>",
    re.IGNORECASE | re.DOTALL,
)
GENERIC_DEVICE_TOOL_PATTERN = re.compile(
    r"<tool>(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)</tool>\s*"
    r"<parameter>(?P<arguments>.*?)</parameter>",
    re.IGNORECASE | re.DOTALL,
)
SHELL_DEVICE_TOOL_PATTERN = re.compile(
    r"<(?P<name>bash|powershell)>(?P<command>.*?)</(?P=name)>",
    re.IGNORECASE | re.DOTALL,
)
ANT_THINKING_PATTERN = re.compile(
    r"<antThinking>.*?</antThinking>", re.IGNORECASE | re.DOTALL
)


def _parse_tool_arguments(raw: str) -> dict[str, Any]:
    normalized = raw.strip()
    if normalized.startswith("```"):
        normalized = re.sub(r"^```(?:json)?\s*|\s*```$", "", normalized, flags=re.IGNORECASE)
    normalized = normalized.translate(
        str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", " ": " "})
    )
    candidates = [normalized]
    candidates.append(re.sub(r"\\(?![\"\\/bfnrtu])", r"\\\\", normalized))
    candidates.append(normalized.replace("\\", "\\\\"))
    for candidate in dict.fromkeys(candidates):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
        return {"value": value}
    raise ValueError("Kimi returned invalid JSON tool arguments")


def _parse_openai_tool_call_items(value: list[Any]) -> list[ParsedToolCall]:
    calls: list[ParsedToolCall] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("function"), dict):
            raise ValueError("Kimi returned an invalid OpenAI-style tool call")
        function = item["function"]
        name = str(function.get("name") or "").strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
            raise ValueError(f"Kimi returned invalid tool name: {name!r}")
        arguments = function.get("arguments", {})
        if isinstance(arguments, str):
            arguments = _parse_tool_arguments(arguments)
        elif not isinstance(arguments, dict):
            arguments = {"value": arguments}
        calls.append(
            ParsedToolCall(
                id=str(
                    item.get("id")
                    or function.get("id")
                    or f"call_{uuid.uuid4().hex}"
                ),
                name=name,
                arguments=arguments,
            )
        )
    return calls


def _repair_unescaped_openai_function_array(raw: str) -> list[ParsedToolCall]:
    """Repair Kimi's observed JSON wrapper while keeping inner arguments strict."""
    match = re.fullmatch(
        r'\s*\[\s*\{\s*"function"\s*:\s*\{\s*'
        r'"arguments"\s*:\s*"(?P<arguments>\{.*\})"\s*,\s*'
        r'"name"\s*:\s*"(?P<name>[A-Za-z_][A-Za-z0-9_.-]*)"\s*'
        r'\}\s*\}?\s*\]\s*',
        raw,
        re.DOTALL,
    )
    if not match:
        return []
    return [
        ParsedToolCall(
            id=f"call_{uuid.uuid4().hex}",
            name=match.group("name"),
            arguments=_parse_tool_arguments(match.group("arguments")),
        )
    ]


def _parse_openai_style_tool_calls(raw: str) -> tuple[list[ParsedToolCall], str] | None:
    marker_at = raw.find(OPENAI_TOOL_CALLS_MARKER)
    if marker_at < 0:
        return None
    candidate = raw[marker_at + len(OPENAI_TOOL_CALLS_MARKER) :].lstrip()
    try:
        value, consumed = json.JSONDecoder().raw_decode(candidate)
    except json.JSONDecodeError:
        repaired_candidate = re.sub(
            r"}\s*,\s*{\"function\"\s*:",
            r'}},{"function":',
            candidate,
        )
        stripped = repaired_candidate.rstrip()
        if stripped.endswith("}]") and not stripped.endswith("}}]"):
            repaired_candidate = stripped[:-1] + "}]"
        try:
            value, consumed = json.JSONDecoder().raw_decode(repaired_candidate)
            candidate = repaired_candidate
        except json.JSONDecodeError:
            return None
    if not isinstance(value, list):
        return None

    calls = _parse_openai_tool_call_items(value)

    prefix = raw[:marker_at].rstrip()
    tail = candidate[consumed:].strip()
    if TOOL_RESULT_MARKER in tail:
        # Model-generated results are untrusted. Return only the call so Claw
        # executes it and supplies a real result on the next turn.
        tail = tail.split(TOOL_RESULT_MARKER, 1)[0].strip()
    text = "\n\n".join(part for part in (prefix, tail) if part).strip()
    return calls, text


def _parse_native_parameter(value: str) -> Any:
    # Native calls are embedded in XML-like <parameter> elements. Kimi may
    # entity-escape shell operators, which must be decoded before Claw executes
    # the argument (for example, &amp;&amp; and &gt;).
    normalized = html.unescape(value).strip()
    if not normalized:
        return ""
    if (
        normalized[0] in '{["'
        or normalized in {"true", "false", "null"}
        or re.fullmatch(r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?", normalized)
    ):
        try:
            return json.loads(normalized)
        except json.JSONDecodeError:
            pass
    return normalized


def _parse_native_device_tool_calls(
    raw: str,
) -> tuple[list[ParsedToolCall], str] | None:
    function_section = FUNCTION_CALLS_PATTERN.search(raw)
    open_function_section = None
    if function_section:
        section_start = function_section.start()
        section_body = function_section.group("body")
    else:
        open_function_section = FUNCTION_CALLS_OPEN_PATTERN.search(raw)
        if open_function_section:
            section_start = open_function_section.start()
            section_body = raw[open_function_section.end() :]
        else:
            section_start = -1
            section_body = ""
    if section_start >= 0:
        calls: list[ParsedToolCall] = []
        for invoke in INVOKE_PATTERN.finditer(section_body):
            arguments = {
                parameter.group("name"): _parse_native_parameter(
                    parameter.group("value")
                )
                for parameter in NAMED_PARAMETER_PATTERN.finditer(invoke.group("body"))
            }
            calls.append(
                ParsedToolCall(
                    id=f"call_{uuid.uuid4().hex}",
                    name=invoke.group("name"),
                    arguments=arguments,
                )
            )
        if not calls:
            try:
                json_value, _ = json.JSONDecoder().raw_decode(section_body.lstrip())
            except json.JSONDecodeError:
                json_value = None
            if isinstance(json_value, list):
                calls = _parse_openai_tool_call_items(json_value)
            else:
                calls = _repair_unescaped_openai_function_array(section_body)
        if calls:
            prefix = ANT_THINKING_PATTERN.sub("", raw[:section_start]).strip()
            return calls, prefix

    generic = GENERIC_DEVICE_TOOL_PATTERN.search(raw)
    if generic:
        arguments = _parse_tool_arguments(generic.group("arguments"))
        prefix = ANT_THINKING_PATTERN.sub("", raw[: generic.start()]).strip()
        return [
            ParsedToolCall(
                id=f"call_{uuid.uuid4().hex}",
                name=generic.group("name"),
                arguments=arguments,
            )
        ], prefix

    shell = SHELL_DEVICE_TOOL_PATTERN.search(raw)
    if shell:
        prefix = ANT_THINKING_PATTERN.sub("", raw[: shell.start()]).strip()
        return [
            ParsedToolCall(
                id=f"call_{uuid.uuid4().hex}",
                name=shell.group("name"),
                arguments={"command": shell.group("command").strip()},
            )
        ], prefix
    return None


def _extract_local_path(text: str) -> Optional[str]:
    patterns = (
        r"\b[A-Za-z]:[\\/][^\r\n\"'<>|]+",
        r"(?:^|\s)(/(?:home|work|mnt|tmp|opt|var)/[^\r\n\"'<>|]+)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = (match.group(1) if match.lastindex else match.group(0)).strip()
        for separator in (
            "  ",
            ". ",
            ", ",
            "; ",
            " and ",
            " then ",
            " after ",
            " with ",
            " и ",
            " с ",
            " затем ",
            " после ",
            " чтобы ",
            " вот ",
        ):
            candidate = candidate.split(separator, 1)[0]
        candidate = candidate.rstrip(".,;:!? )]")
        existing = _longest_existing_path_prefix(candidate)
        if existing:
            return existing
        if candidate:
            return candidate
    return None


def _longest_existing_path_prefix(candidate: str) -> Optional[str]:
    for end in range(len(candidate), 2, -1):
        fragment = candidate[:end].rstrip(".,;:!? )]")
        if not fragment:
            continue
        try:
            if Path(fragment).exists():
                return fragment
        except OSError:
            continue
    return None


def _repair_windows_file_path(path: str) -> str:
    if RUNTIME_PLATFORM != "windows":
        return path
    try:
        if Path(path).exists():
            return path
    except OSError:
        pass
    filename = re.split(r"[\\/]", path.rstrip("\\/"))[-1]
    if not filename:
        return path
    home = Path(RUNTIME_HOME)
    roots = [
        home / "Desktop",
        home / "OneDrive" / "Desktop",
        home / "Documents",
        home / "Downloads",
    ]
    one_drive = os.getenv("OneDrive", "").strip()
    if one_drive:
        roots.append(Path(one_drive) / "Desktop")
    matches: list[Path] = []
    for root in roots:
        candidate = root / filename
        try:
            if candidate.is_file():
                matches.append(candidate)
        except OSError:
            continue
    unique = list(dict.fromkeys(matches))
    return str(unique[0]) if len(unique) == 1 else path


def _repair_native_tool_calls(
    parsed: ParsedAssistantOutput,
    request: ChatCompletionRequest,
) -> ParsedAssistantOutput:
    if not parsed.tool_calls:
        return parsed
    available_by_lower = {
        str(tool.get("function", {}).get("name", "")).lower(): str(
            tool.get("function", {}).get("name", "")
        )
        for tool in (request.tools or [])
        if str(tool.get("function", {}).get("name", ""))
    }
    aliases = {
        "read": "read_file",
        "write": "write_file",
        "edit": "edit_file",
        "glob": "glob_search",
        "grep": "grep_search",
    }

    def resolve_tool_name(name: str) -> Optional[str]:
        lowered_name = name.lower().removeprefix("functions.")
        candidates = [lowered_name]
        for prefix in ("bridge_", "claw_", "filesystem_"):
            if lowered_name.startswith(prefix):
                candidates.append(lowered_name.removeprefix(prefix))
        compact_registry: dict[str, set[str]] = {}
        for registered_name, canonical_name in available_by_lower.items():
            compact_name = re.sub(r"[^a-z0-9]", "", registered_name)
            compact_registry.setdefault(compact_name, set()).add(canonical_name)
        explicit_generated_aliases = {
            "fileread": "read_file",
            "filewrite": "write_file",
            "filefinder": "glob_search",
            "listdir": "glob_search",
            "listdirectory": "glob_search",
            "directorylist": "glob_search",
        }
        runtime_generated_aliases = {
            "runterminalcommand": (
                "powershell" if RUNTIME_PLATFORM == "windows" else "bash"
            ),
            "executecommand": "powershell" if RUNTIME_PLATFORM == "windows" else "bash",
            "terminal": "powershell" if RUNTIME_PLATFORM == "windows" else "bash",
            "shell": "powershell" if RUNTIME_PLATFORM == "windows" else "bash",
            "cmd": "powershell" if RUNTIME_PLATFORM == "windows" else "bash",
            "computer": "powershell" if RUNTIME_PLATFORM == "windows" else "bash",
            "python": "powershell" if RUNTIME_PLATFORM == "windows" else "bash",
        }
        for candidate_name in candidates:
            direct = available_by_lower.get(candidate_name)
            if direct:
                return direct
            target = aliases.get(candidate_name)
            alias_match = available_by_lower.get(target or "")
            if alias_match:
                return alias_match
            compact = re.sub(r"[^a-z0-9]", "", candidate_name)
            compact_matches = compact_registry.get(compact, set())
            if len(compact_matches) == 1:
                return next(iter(compact_matches))
            explicit_target = explicit_generated_aliases.get(compact)
            explicit_match = available_by_lower.get(explicit_target or "")
            if explicit_match:
                return explicit_match
            runtime_target = runtime_generated_aliases.get(compact)
            runtime_match = available_by_lower.get(runtime_target or "")
            if runtime_match:
                return runtime_match
        return None
    latest_user = next(
        (
            _content_to_text(message.content)
            for message in reversed(request.messages)
            if message.role == "user"
        ),
        "",
    )
    local_path = _extract_local_path(latest_user)
    repaired: list[ParsedToolCall] = []
    for call in parsed.tool_calls:
        arguments = dict(call.arguments)
        generated_compact_name = re.sub(r"[^a-z0-9]", "", call.name.lower())
        logger.info(
            "Kimi generated tool name=%s argument_keys=%s",
            call.name,
            sorted(arguments),
        )
        lowered = call.name.lower()
        call_name = call.name
        wrapper_call = generated_compact_name == "claw"
        filesystem_wrapper = generated_compact_name == "filesystem"
        requested_name = arguments.pop("name", "") if wrapper_call else call.name
        if (
            wrapper_call
            and not requested_name
            and isinstance(arguments.get("command"), str)
        ):
            requested_name = "PowerShell" if RUNTIME_PLATFORM == "windows" else "bash"
        if filesystem_wrapper:
            if str(arguments.get("command") or "").strip():
                requested_name = (
                    "PowerShell" if RUNTIME_PLATFORM == "windows" else "bash"
                )
            elif str(arguments.get("pattern") or "").strip():
                requested_name = "glob_search"
            elif str(arguments.get("path") or "").strip():
                requested_name = "read_file"
        canonical_name = (
            resolve_tool_name(requested_name) if isinstance(requested_name, str) else None
        )
        if canonical_name:
            call_name = canonical_name
            lowered = call_name.lower()
            if wrapper_call or filesystem_wrapper:
                function = next(
                    (
                        tool.get("function", {})
                        for tool in (request.tools or [])
                        if str(tool.get("function", {}).get("name", "")).lower()
                        == lowered
                    ),
                    {},
                )
                parameters = function.get("parameters", {})
                properties = parameters.get("properties", {})
                if isinstance(properties, dict) and properties:
                    arguments = {
                        key: value for key, value in arguments.items() if key in properties
                    }
        elif not available_by_lower:
            call_name = call.name
        else:
            unknown_name = requested_name if wrapper_call else call.name
            raise ValueError(f"Kimi selected unknown Claw tool: {unknown_name!r}")
        if generated_compact_name == "python" and lowered in {"powershell", "bash"}:
            code = arguments.pop("code", arguments.pop("script", None))
            if "command" not in arguments and isinstance(code, str):
                encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
                executable = str(sys.executable)
                if lowered == "powershell":
                    escaped_executable = executable.replace("'", "''")
                    arguments["command"] = (
                        "$code=[Text.Encoding]::UTF8.GetString("
                        f"[Convert]::FromBase64String('{encoded}')); "
                        f"& '{escaped_executable}' -c $code"
                    )
                else:
                    escaped_executable = executable.replace("'", "'\\''")
                    arguments["command"] = (
                        f"printf %s '{encoded}' | base64 -d | "
                        f"'{escaped_executable}' -"
                    )
        if generated_compact_name in {
            "filefinder",
            "listdir",
            "listdirectory",
            "directorylist",
        } and lowered == "glob_search":
            if "pattern" not in arguments and isinstance(arguments.get("file_name"), str):
                arguments["pattern"] = arguments["file_name"]
            if "path" not in arguments and isinstance(arguments.get("search_path"), str):
                arguments["path"] = arguments["search_path"]
            arguments.pop("file_name", None)
            arguments.pop("search_path", None)
        if lowered == "read_file":
            if "path" not in arguments and isinstance(arguments.get("file_path"), str):
                arguments["path"] = arguments["file_path"]
            arguments.pop("file_path", None)
            if isinstance(arguments.get("path"), str):
                arguments["path"] = _repair_windows_file_path(arguments["path"])
        if lowered in {"glob", "glob_search"}:
            if local_path:
                arguments["path"] = local_path
            pattern = str(arguments.get("pattern") or "**/*")
            if re.match(r"^[A-Za-z]:[\\/]", pattern) or pattern.startswith("/"):
                pattern = "**/*"
            arguments["pattern"] = pattern
        if (
            lowered in {"powershell", "bash"}
            and isinstance(arguments.get("command"), str)
            and not arguments.get("run_in_background")
        ):
            arguments.setdefault("timeout", DEFAULT_SHELL_TIMEOUT_MS)
        repaired.append(
            ParsedToolCall(
                id=call.id,
                name=call_name,
                arguments=arguments,
            )
        )
    return ParsedAssistantOutput(text=parsed.text, tool_calls=repaired)


def parse_assistant_output(raw: str) -> ParsedAssistantOutput:
    tool_calls: list[ParsedToolCall] = []
    for match in TOOL_CALL_PATTERN.finditer(raw):
        label = match.group("label").strip()
        label = re.sub(r":\d+$", "", label)
        name = label.rsplit(".", 1)[-1].strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", name):
            raise ValueError(f"Kimi returned invalid tool name: {name!r}")
        tool_calls.append(
            ParsedToolCall(
                id=f"call_{uuid.uuid4().hex}",
                name=name,
                arguments=_parse_tool_arguments(match.group("arguments")),
            )
        )

    text = TOOL_CALL_PATTERN.sub("", raw)
    text = text.replace("<|tool_calls_section_begin|>", "")
    text = text.replace("<|tool_calls_section_end|>", "").strip()
    alternate = _parse_openai_style_tool_calls(text)
    if alternate is not None:
        alternate_calls, text = alternate
        tool_calls.extend(alternate_calls)
    native = _parse_native_device_tool_calls(text)
    if native is not None:
        native_calls, text = native
        tool_calls.extend(native_calls)
    text = ANT_THINKING_PATTERN.sub("", text).strip()
    return ParsedAssistantOutput(text=text, tool_calls=tool_calls)


def parse_tool_output_with_diagnostics(raw: str) -> ParsedAssistantOutput:
    parsed = parse_assistant_output(raw)
    logger.info(
        "Kimi tool bridge parsed raw_chars=%d text_chars=%d tool_calls=%d "
        "markers=begin:%s,args:%s,end:%s,section_end:%s",
        len(raw),
        len(parsed.text),
        len(parsed.tool_calls),
        "<|tool_call_begin|>" in raw,
        "<|tool_call_argument_begin|>" in raw,
        "<|tool_call_end|>" in raw,
        "<|tool_calls_section_end|>" in raw,
    )
    return parsed


def build_kimi_payload(prompt: str, upstream_model: str = KIMI_UPSTREAM_MODEL) -> dict[str, Any]:
    return {
        "kimiplus_id": "kimi",
        "model": upstream_model,
        "use_search": False,
        "messages": [{"role": "user", "content": prompt}],
        "extend": {"sidebar": True},
        "refs": [],
        "history": [],
        "scene_labels": [],
        "use_semantic_memory": False,
        "use_deep_research": False,
    }


def build_kimi_connect_payload(
    chat_id: str,
    prompt: str,
    system_prompt: str = "",
    native_tool_names: Optional[list[str]] = None,
) -> dict[str, Any]:
    tools = [
        {"type": "TOOL_TYPE_DEVICE_TOOL", "name": name}
        for name in (native_tool_names or [])
    ]
    if _is_k3_upstream():
        tools.insert(0, {"type": "TOOL_TYPE_ASK_USER", "name": ""})
    payload: dict[str, Any] = {
        "chatId": chat_id,
        "kimiplusId": KIMI_KIMIPLUS_ID,
        "scenario": KIMI_SCENARIO,
        "tools": tools,
        "message": {
            "id": "",
            "parentId": "",
            "childrenMessageIds": [],
            "role": "user",
            "blocks": [
                {
                    "id": "",
                    "messageId": "",
                    "text": {"content": prompt},
                }
            ],
            "scenario": KIMI_SCENARIO,
            "labels": [],
            "references": [],
            "isGoal": False,
        },
        "options": {
            "thinking": True,
            "reasoningEffort": KIMI_REASONING_EFFORT,
            "contextLength": KIMI_CONTEXT_LENGTH,
            "enablePlugin": KIMI_ENABLE_PLUGIN,
            "model": KIMI_UPSTREAM_MODEL,
        },
    }
    if system_prompt:
        payload["options"]["systemPrompt"] = system_prompt
    return payload


def _encode_connect_json(value: dict[str, Any]) -> bytes:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return b"\x00" + len(payload).to_bytes(4, "big") + payload


def _decode_connect_frames(
    data: bytearray,
) -> tuple[list[tuple[int, dict[str, Any]]], bytearray]:
    buffer = bytearray(data)
    frames: list[tuple[int, dict[str, Any]]] = []
    while len(buffer) >= 5:
        flags = buffer[0]
        length = int.from_bytes(buffer[1:5], "big")
        if len(buffer) < 5 + length:
            break
        raw = bytes(buffer[5 : 5 + length])
        del buffer[: 5 + length]
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Kimi Connect frame contains invalid JSON") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Kimi Connect frame must contain a JSON object")
        frames.append((flags, value))
    return frames, buffer


@dataclass
class ConnectStreamState:
    assistant_started: bool = False
    chat_id: Optional[str] = None


def _connect_event_text(
    value: dict[str, Any], state: ConnectStreamState
) -> tuple[list[str], bool]:
    chat = value.get("chat")
    if isinstance(chat, dict) and chat.get("id"):
        state.chat_id = str(chat["id"])
    message = value.get("message")
    if isinstance(message, dict):
        role = message.get("role")
        if role == "assistant":
            state.assistant_started = True
        elif role == "user":
            return [], False
    block = value.get("block")
    text: list[str] = []
    if state.assistant_started and isinstance(block, dict):
        text_value = block.get("text")
        if isinstance(text_value, dict) and isinstance(text_value.get("content"), str):
            text.append(text_value["content"])
    return text, "done" in value


def parse_kimi_sse_line(line: str) -> tuple[Optional[str], Optional[str]]:
    if not line.startswith("data:"):
        return None, None
    raw = line[5:].strip()
    if not raw:
        return None, None
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return None, None
    event_name = event.get("event")
    if event_name == "cmpl" and isinstance(event.get("text"), str):
        return "text", event["text"]
    if event_name == "all_done":
        return "done", None
    return None, None


class KimiUpstreamError(RuntimeError):
    def __init__(self, operation: str, status_code: int):
        super().__init__(f"Kimi {operation} failed with HTTP {status_code}")
        self.operation = operation
        self.status_code = status_code


class KimiRetryableConnectError(RuntimeError):
    pass


class KimiWebClient:
    def __init__(self, store: SessionStore):
        self.store = store
        self._refresh_lock = asyncio.Lock()
        self.last_chat_id: Optional[str] = None

    def _client(self) -> httpx.AsyncClient:
        options: dict[str, Any] = {}
        if KIMI_PROXY:
            options["proxy"] = KIMI_PROXY
        try:
            session = self.store.load()
        except RuntimeError:
            session = {}
        cookies: dict[str, str] = {}
        stored_cookies = session.get("cookies", [])
        if isinstance(stored_cookies, dict):
            cookies.update(
                (str(name), str(value))
                for name, value in stored_cookies.items()
                if value is not None
            )
        elif isinstance(stored_cookies, list):
            for item in stored_cookies:
                if not isinstance(item, dict):
                    continue
                domain = str(item.get("domain") or "").lower()
                name = item.get("name")
                value = item.get("value")
                if (
                    name
                    and value is not None
                    and domain.endswith(("kimi.com", "kimi.ai", "moonshot.cn"))
                ):
                    cookies[str(name)] = str(value)
        return httpx.AsyncClient(
            timeout=httpx.Timeout(connect=20.0, read=180.0, write=30.0, pool=30.0),
            follow_redirects=True,
            cookies=cookies,
            **options,
        )

    async def _post_json(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        operation: str,
    ) -> httpx.Response:
        last_error: httpx.TransportError | None = None
        for attempt in range(UPSTREAM_MAX_ATTEMPTS):
            metrics.increment("upstream_requests_total")
            try:
                async with self._client() as client:
                    response = await client.post(url, headers=headers, json=payload)
            except httpx.TransportError as error:
                last_error = error
                metrics.increment("upstream_transport_errors_total")
                if attempt + 1 >= UPSTREAM_MAX_ATTEMPTS:
                    raise
                metrics.increment("upstream_retries_total")
                await _retry_delay(attempt)
                continue
            if _is_retryable_status(response.status_code) and attempt + 1 < UPSTREAM_MAX_ATTEMPTS:
                metrics.increment("upstream_retries_total")
                logger.warning(
                    "Kimi upstream retry operation=%s status=%d attempt=%d",
                    operation,
                    response.status_code,
                    attempt + 1,
                )
                await _retry_delay(attempt)
                continue
            return response
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Kimi {operation} retry loop exhausted")

    @staticmethod
    def _headers(session: dict[str, Any], token: Optional[str]) -> dict[str, str]:
        blocked = {"host", "content-length", "cookie", "origin", "referer"}
        headers = {
            str(key).lower(): str(value)
            for key, value in session.get("headers", {}).items()
            if str(key).lower() not in blocked and value is not None
        }
        headers.update(
            {
                "content-type": "application/json",
                "origin": KIMI_WEB_BASE_URL,
                "referer": f"{KIMI_WEB_BASE_URL}/",
            }
        )
        if token:
            headers["authorization"] = f"Bearer {token}"
        else:
            headers.pop("authorization", None)
        return headers

    async def access_token(self) -> str:
        session = self.store.load()
        token = str(session["access_token"])
        if jwt_expiry(token) > int(time.time()) + TOKEN_REFRESH_MARGIN_SECONDS:
            return token
        return await self.refresh_access_token(rejected_token=token)

    async def refresh_access_token(self, rejected_token: Optional[str] = None) -> str:
        async with self._refresh_lock:
            session = self.store.load()
            current = str(session["access_token"])
            if rejected_token and current != rejected_token:
                return current
            headers = self._headers(session, token=None)
            headers["connect-protocol-version"] = "1"
            response = await self._post_json(
                f"{KIMI_AUTH_BASE_URL}/api/account.gateway.v1.AuthService/RefreshToken",
                headers,
                {"refresh_token": session["refresh_token"]},
                "token_refresh",
            )
            if response.status_code != 200:
                raise KimiUpstreamError("token refresh", response.status_code)
            data = response.json()
            access = data.get("accessToken") or data.get("access_token")
            refresh = data.get("refreshToken") or data.get("refresh_token")
            if not access:
                raise RuntimeError("Kimi refresh response did not include an access token")
            session["access_token"] = access
            if refresh:
                session["refresh_token"] = refresh
            session.setdefault("headers", {})["authorization"] = f"Bearer {access}"
            session["refreshed_at"] = int(time.time())
            self.store.save(session)
            logger.info("Kimi access token refreshed")
            return str(access)

    async def create_chat(self) -> str:
        token = await self.access_token()
        for attempt in range(2):
            session = self.store.load()
            headers = self._headers(session, token)
            response = await self._post_json(
                f"{KIMI_WEB_BASE_URL}/api/chat",
                headers,
                {"name": "Claw Code API"},
                "chat_creation",
            )
            if response.status_code == 401 and attempt == 0:
                token = await self.refresh_access_token(rejected_token=token)
                continue
            if response.status_code != 200:
                raise KimiUpstreamError("chat creation", response.status_code)
            chat_id = response.json().get("id")
            if not chat_id:
                raise RuntimeError("Kimi chat creation response did not include an id")
            return str(chat_id)
        raise RuntimeError("Kimi chat creation retry exhausted")

    async def iter_completion(
        self,
        chat_id: str,
        prompt: str,
        system_prompt: str = "",
        native_tool_names: Optional[list[str]] = None,
    ) -> AsyncIterator[str]:
        if KIMI_UPSTREAM_PROTOCOL == "connect_v2":
            async for text in self._iter_connect_completion(
                chat_id,
                prompt,
                system_prompt=system_prompt,
                native_tool_names=native_tool_names,
            ):
                yield text
            return
        token = await self.access_token()
        payload = build_kimi_payload(prompt, "k2")
        logged_shapes: set[tuple[str, tuple[str, ...]]] = set()
        auth_refreshed = False
        emitted_text = False
        for attempt in range(UPSTREAM_MAX_ATTEMPTS):
            session = self.store.load()
            headers = self._headers(session, token)
            headers.update(
                {
                    "accept": "text/event-stream",
                    "referer": f"{KIMI_WEB_BASE_URL}/chat/{chat_id}",
                }
            )
            try:
                await request_pacer.wait()
                metrics.increment("upstream_requests_total")
                async with self._client() as client:
                    async with client.stream(
                        "POST",
                        f"{KIMI_WEB_BASE_URL}/api/chat/{chat_id}/completion/stream",
                        headers=headers,
                        json=payload,
                    ) as response:
                        if response.status_code == 401 and not auth_refreshed:
                            await response.aread()
                            token = await self.refresh_access_token(rejected_token=token)
                            auth_refreshed = True
                            metrics.increment("upstream_retries_total")
                            continue
                        if (
                            _is_retryable_status(response.status_code)
                            and attempt + 1 < UPSTREAM_MAX_ATTEMPTS
                        ):
                            await response.aread()
                            metrics.increment("upstream_retries_total")
                            await _retry_delay(attempt)
                            continue
                        if response.status_code != 200:
                            await response.aread()
                            raise KimiUpstreamError("completion", response.status_code)
                        async for line in response.aiter_lines():
                            if LOG_KIMI_EVENT_SHAPES and line.startswith("data:"):
                                try:
                                    event_data = json.loads(line[5:].strip())
                                except json.JSONDecodeError:
                                    event_data = None
                                if isinstance(event_data, dict):
                                    event_name = str(event_data.get("event", "<missing>"))
                                    shape = (event_name, tuple(sorted(map(str, event_data.keys()))))
                                    if shape not in logged_shapes:
                                        logged_shapes.add(shape)
                                        nested = {
                                            key: sorted(map(str, value.keys()))
                                            for key, value in event_data.items()
                                            if isinstance(value, dict)
                                        }
                                        logger.info(
                                            "Kimi SSE shape event=%s keys=%s nested_keys=%s text_len=%s",
                                            event_name,
                                            list(shape[1]),
                                            nested,
                                            len(event_data.get("text", ""))
                                            if isinstance(event_data.get("text"), str)
                                            else None,
                                        )
                            kind, value = parse_kimi_sse_line(line)
                            if kind == "text" and value:
                                emitted_text = True
                                yield value
                            elif kind == "done":
                                return
                        return
            except httpx.TransportError:
                metrics.increment("upstream_transport_errors_total")
                if emitted_text or attempt + 1 >= UPSTREAM_MAX_ATTEMPTS:
                    raise
                metrics.increment("upstream_retries_total")
                await _retry_delay(attempt)
        raise RuntimeError("Kimi completion retry exhausted")

    async def _iter_connect_completion(
        self,
        chat_id: str,
        prompt: str,
        system_prompt: str = "",
        native_tool_names: Optional[list[str]] = None,
    ) -> AsyncIterator[str]:
        self.last_chat_id = chat_id or None
        token = await self.access_token()
        envelope = _encode_connect_json(
            build_kimi_connect_payload(
                chat_id,
                prompt,
                system_prompt=system_prompt,
                native_tool_names=native_tool_names,
            )
        )
        auth_refreshed = False
        emitted_text = False
        for attempt in range(UPSTREAM_MAX_ATTEMPTS):
            session = self.store.load()
            headers = self._headers(session, token)
            headers.update(
                {
                    "accept": "application/connect+json",
                    "content-type": "application/connect+json",
                    "connect-protocol-version": "1",
                    "referer": f"{KIMI_WEB_BASE_URL}/chat/{chat_id}",
                }
            )
            buffer = bytearray()
            state = ConnectStreamState()
            try:
                await request_pacer.wait()
                metrics.increment("upstream_requests_total")
                async with self._client() as client:
                    async with client.stream(
                        "POST",
                        f"{KIMI_WEB_BASE_URL}/apiv2/kimi.gateway.chat.v1.ChatService/Chat",
                        headers=headers,
                        content=envelope,
                    ) as response:
                        if response.status_code == 401 and not auth_refreshed:
                            await response.aread()
                            token = await self.refresh_access_token(rejected_token=token)
                            auth_refreshed = True
                            metrics.increment("upstream_retries_total")
                            continue
                        if (
                            _is_retryable_status(response.status_code)
                            and attempt + 1 < UPSTREAM_MAX_ATTEMPTS
                        ):
                            await response.aread()
                            metrics.increment("upstream_retries_total")
                            await _retry_delay(attempt)
                            continue
                        if response.status_code != 200:
                            await response.aread()
                            raise KimiUpstreamError("connect completion", response.status_code)
                        async for chunk in response.aiter_bytes():
                            buffer.extend(chunk)
                            frames, buffer = _decode_connect_frames(buffer)
                            for flags, value in frames:
                                error = value.get("error")
                                if isinstance(error, dict):
                                    code = str(error.get("code") or "unknown")
                                    message = str(error.get("message") or "")
                                    if not message:
                                        for detail in error.get("details") or []:
                                            if not isinstance(detail, dict):
                                                continue
                                            debug = detail.get("debug")
                                            if not isinstance(debug, dict):
                                                continue
                                            localized = debug.get("localizedMessage")
                                            if isinstance(localized, dict) and localized.get("message"):
                                                message = str(localized["message"])
                                                break
                                    message = message[:1000]
                                    connect_error = (
                                        f"Kimi Connect error {code}: {message}".rstrip()
                                    )
                                    if _is_retryable_connect_error(code, message):
                                        raise KimiRetryableConnectError(connect_error)
                                    raise RuntimeError(connect_error)
                                if flags & 0x02:
                                    return
                                texts, _done = _connect_event_text(value, state)
                                if state.chat_id:
                                    self.last_chat_id = state.chat_id
                                for text in texts:
                                    if text:
                                        emitted_text = True
                                        yield text
                        if buffer:
                            raise RuntimeError("Kimi Connect stream ended with a partial frame")
                        return
            except (httpx.TransportError, KimiRetryableConnectError):
                metrics.increment("upstream_transport_errors_total")
                if emitted_text or attempt + 1 >= UPSTREAM_MAX_ATTEMPTS:
                    raise
                metrics.increment("upstream_retries_total")
                await _retry_delay(attempt)
        raise RuntimeError("Kimi Connect completion retry exhausted")


store = SessionStore(KIMI_SESSION_FILE)
kimi = KimiWebClient(store)


def _message_fingerprint(message: Message) -> str:
    if hasattr(message, "model_dump"):
        value = message.model_dump(exclude_none=True)
    else:  # pragma: no cover - pydantic v1 compatibility
        value = message.dict(exclude_none=True)
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _claw_compaction_overlap(
    messages: list[Message],
    fingerprints: list[str],
    previous: list[str],
) -> tuple[int, int] | None:
    """Return ``(delta_start, overlap)`` after a Claw client compaction.

    The API adapter may prepend its stable runtime system prompt before Claw's
    synthetic summary. Claw then keeps a verbatim recent suffix. Kimi already
    owns the removed upstream history, so replaying the summary would duplicate
    context and create a new web chat. A strong marker and at least two exact
    tail fingerprints distinguish this rewrite from an unrelated/reset
    conversation. Leading runtime instructions are intentionally ignored
    because Claw regenerates that dynamic system message on every request.
    """
    if not previous or len(messages) < 3:
        return None

    compaction_index = next(
        (
            index
            for index, message in enumerate(messages[:4])
            if message.role in {"system", "user"}
            and _content_to_text(message.content).startswith(CLAW_COMPACTION_PREFIX)
        ),
        None,
    )
    if compaction_index is None:
        return None
    candidate = fingerprints[compaction_index + 1 :]
    for overlap in range(min(len(previous), len(candidate)), 1, -1):
        if previous[-overlap:] == candidate[:overlap]:
            return compaction_index + 1 + overlap, overlap
    return None


class PersistentConversation:
    def __init__(self, session_store: SessionStore):
        self.store = session_store
        self._lock = asyncio.Lock()

    async def prepare(
        self,
        conversation_key: str,
        messages: list[Message],
    ) -> tuple[str, list[Message], list[str], int, int, int]:
        fingerprints = [_message_fingerprint(message) for message in messages]
        estimated_context_tokens = _estimated_tokens(messages_to_prompt(messages))
        async with self._lock:
            session = self.store.load()
            conversations = session.get("persistent_conversations")
            if not isinstance(conversations, dict):
                conversations = {}
            state = conversations.get(conversation_key)
            if not isinstance(state, dict):
                state = {}
            chat_id = str(state.get("chat_id") or "")
            previous = state.get("message_fingerprints")
            if not isinstance(previous, list) or not all(
                isinstance(item, str) for item in previous
            ):
                previous = []
            previous_chat_id = chat_id
            previous_context_tokens = int(state.get("estimated_context_tokens") or 0)
            upstream_message_count = int(
                state.get("upstream_message_count") or len(previous)
            )
            upstream_context_tokens = int(
                state.get("upstream_context_tokens_estimated")
                or previous_context_tokens
            )
            continued_prefix = (
                bool(chat_id)
                and len(fingerprints) > len(previous)
                and fingerprints[: len(previous)] == previous
            )
            rotation_reason = "new_session_or_replaced_history"
            if continued_prefix:
                appended = list(messages[len(previous) :])
                next_message_count = upstream_message_count + len(appended)
                next_context_tokens = upstream_context_tokens + _estimated_tokens(
                    messages_to_prompt(appended)
                )
                within_limit = (
                    next_message_count <= MAX_PERSISTENT_MESSAGES
                    and next_context_tokens <= MAX_PERSISTENT_CONTEXT_TOKENS
                )
                delta = list(appended)
                if delta and delta[0].role == "assistant":
                    # Kimi already generated the prior assistant turn. Only the
                    # new user/tool result belongs in the next upstream request.
                    delta = delta[1:]
                if delta and within_limit:
                    return (
                        chat_id,
                        delta,
                        fingerprints,
                        estimated_context_tokens,
                        next_message_count,
                        next_context_tokens,
                    )
                if not within_limit:
                    rotation_reason = "cumulative_limit"

            compaction_match = (
                _claw_compaction_overlap(messages, fingerprints, previous)
                if chat_id and not continued_prefix
                else None
            )
            if compaction_match:
                delta_start, compaction_overlap = compaction_match
                appended = list(messages[delta_start:])
                next_message_count = upstream_message_count + len(appended)
                next_context_tokens = upstream_context_tokens + _estimated_tokens(
                    messages_to_prompt(appended)
                )
                within_limit = (
                    next_message_count <= MAX_PERSISTENT_MESSAGES
                    and next_context_tokens <= MAX_PERSISTENT_CONTEXT_TOKENS
                )
                delta = list(appended)
                if delta and delta[0].role == "assistant":
                    delta = delta[1:]
                if delta and within_limit:
                    logger.info(
                        "Persistent session=%s continued across Claw compaction "
                        "with preserved_messages=%d cumulative_messages=%d "
                        "cumulative_context_tokens=%d",
                        conversation_key[:12],
                        compaction_overlap,
                        next_message_count,
                        next_context_tokens,
                    )
                    return (
                        chat_id,
                        delta,
                        fingerprints,
                        estimated_context_tokens,
                        next_message_count,
                        next_context_tokens,
                    )
                if not within_limit:
                    rotation_reason = "cumulative_limit_after_compaction"

            # The current K3 web flow creates its chat inside the first
            # streaming Chat call and returns the id as a `chat` event. K2.6
            # still accepts the legacy explicit /api/chat creation call.
            chat_id = "" if _is_k3_upstream() else await kimi.create_chat()
            session = self.store.load()
            conversations = session.get("persistent_conversations")
            if not isinstance(conversations, dict):
                conversations = {}
            generation = int(state.get("generation") or 0) + 1
            if previous_chat_id:
                logger.info(
                    "Persistent session=%s rotating generation=%d reason=%s "
                    "previous_messages=%d previous_context_tokens=%d",
                    conversation_key[:12],
                    generation,
                    rotation_reason,
                    upstream_message_count,
                    upstream_context_tokens,
                )
            conversations[conversation_key] = {
                "chat_id": chat_id,
                "message_fingerprints": [],
                "estimated_context_tokens": 0,
                "upstream_message_count": 0,
                "upstream_context_tokens_estimated": 0,
                "generation": generation,
                "updated_at": int(time.time()),
            }
            session["persistent_conversations"] = conversations
            self.store.save(session)
            return (
                chat_id,
                list(messages),
                fingerprints,
                estimated_context_tokens,
                len(messages),
                estimated_context_tokens,
            )

    async def commit(
        self,
        conversation_key: str,
        chat_id: str,
        fingerprints: list[str],
        estimated_context_tokens: int,
        upstream_message_count: int,
        upstream_context_tokens: int,
    ) -> None:
        async with self._lock:
            session = self.store.load()
            conversations = session.get("persistent_conversations")
            if not isinstance(conversations, dict):
                conversations = {}
            current = conversations.get(conversation_key)
            generation = (
                int(current.get("generation") or 1)
                if isinstance(current, dict)
                else 1
            )
            conversations[conversation_key] = {
                "chat_id": chat_id,
                "message_fingerprints": fingerprints,
                "estimated_context_tokens": estimated_context_tokens,
                "upstream_message_count": upstream_message_count,
                "upstream_context_tokens_estimated": upstream_context_tokens,
                "generation": generation,
                "updated_at": int(time.time()),
            }
            session["persistent_conversations"] = conversations
            self.store.save(session)

    async def clear_if_current(self, conversation_key: str, chat_id: str) -> None:
        async with self._lock:
            session = self.store.load()
            conversations = session.get("persistent_conversations")
            if not isinstance(conversations, dict):
                return
            state = conversations.get(conversation_key)
            if isinstance(state, dict) and str(state.get("chat_id") or "") == chat_id:
                conversations.pop(conversation_key, None)
                session["persistent_conversations"] = conversations
                self.store.save(session)


persistent_conversation = PersistentConversation(store)


def _persistent_chat_is_gone(exc: BaseException) -> bool:
    """Only discard continuity when upstream explicitly rejects the chat id.

    Model-format failures, empty generations, rate limits, and transient server
    errors do not invalidate the web chat. Clearing on those errors produced a
    new short chat on the next Claw request.
    """
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in {404, 410}
    )


async def _collect_completion(
    chat_id: str,
    prompt: str,
    stop_after_tool_section: bool,
    system_prompt: str = "",
    native_tool_names: Optional[list[str]] = None,
) -> str:
    parts: list[str] = []
    marker_window = ""
    tool_section_complete = False
    if system_prompt or native_tool_names:
        completion = kimi.iter_completion(
            chat_id,
            prompt,
            system_prompt=system_prompt,
            native_tool_names=native_tool_names,
        )
    else:
        completion = kimi.iter_completion(chat_id, prompt)
    async for part in completion:
        if not tool_section_complete:
            parts.append(part)
        if stop_after_tool_section and not tool_section_complete:
            marker_window = (marker_window + part)[-1024:]
            if (
                "<|tool_calls_section_end|>" in marker_window
                or TOOL_RESULT_MARKER in marker_window
                or "</function_calls>" in marker_window.lower()
                or (
                    "<tool>" in marker_window.lower()
                    and "</parameter>" in marker_window.lower()
                )
                or "</bash>" in marker_window.lower()
                or "</powershell>" in marker_window.lower()
            ):
                tool_section_complete = True
    return "".join(parts)


def _tool_output_satisfies_requirement(
    parsed: ParsedAssistantOutput,
    required_tools: list[str],
) -> bool:
    if not parsed.text and not parsed.tool_calls:
        metrics.increment("empty_completions_total")
        return False
    if not required_tools:
        return True
    if not parsed.tool_calls:
        return False
    if required_tools == ["<any-available-tool>"]:
        return True
    required = {name.lower() for name in required_tools}
    return any(call.name.lower() in required for call in parsed.tool_calls)


def _safe_required_any_tool_fallback(
    request: ChatCompletionRequest,
    required_tools: list[str],
) -> Optional[ParsedToolCall]:
    """Return a harmless shell probe only for an exhausted any-tool requirement."""
    if request.tool_choice != "required" or required_tools != ["<any-available-tool>"]:
        return None

    safe_commands = {
        "powershell": "Get-Location",
        "bash": "pwd",
    }
    functions_by_lower: dict[str, tuple[str, dict[str, Any]]] = {}
    for tool in request.tools or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function", {})
        if not isinstance(function, dict):
            continue
        name = str(function.get("name", ""))
        if name:
            functions_by_lower[name.lower()] = (name, function)

    for preferred_name in ("powershell", "bash"):
        available = functions_by_lower.get(preferred_name)
        if not available:
            continue
        canonical_name, function = available
        parameters = function.get("parameters") or {}
        if not isinstance(parameters, dict):
            continue
        properties = parameters.get("properties") or {}
        required = parameters.get("required") or []
        if not isinstance(properties, dict) or not isinstance(required, list):
            continue
        command_schema = properties.get("command")
        if not isinstance(command_schema, dict):
            continue
        if command_schema.get("type") not in (None, "string"):
            continue
        if any(str(parameter) != "command" for parameter in required):
            continue
        return ParsedToolCall(
            id=f"call_{uuid.uuid4().hex}",
            name=canonical_name,
            arguments={"command": safe_commands[preferred_name]},
        )
    return None


async def _collect_tool_output_with_retry(
    request: ChatCompletionRequest,
    prompt: str,
    initial_chat_id: str,
    conversation_messages: Optional[list[Message]] = None,
) -> ParsedAssistantOutput:
    required_tools = _required_next_tools(
        request.messages,
        request.tools or [],
        request.tool_choice,
    )
    native_tool_mode = (
        _native_tool_mode(request)
        if KIMI_UPSTREAM_PROTOCOL == "connect_v2"
        else None
    )
    native_connect = KIMI_UPSTREAM_PROTOCOL == "connect_v2"
    if native_tool_mode == ALL_CLAW_TOOLS:
        native_tool_names = [
            str(tool.get("function", {}).get("name", ""))
            for tool in (request.tools or [])
            if str(tool.get("function", {}).get("name", ""))
        ]
    else:
        native_tool_names = None
    def conversation_prompt(messages: list[Message]) -> str:
        return _conversation_prompt(
            messages,
            tool_result_limit=16_000 if not native_tool_mode else 8_000,
            older_tool_result_limit=800,
        )

    base_prompt = (
        conversation_prompt(conversation_messages or request.messages)
        if native_connect
        else prompt
    )
    fresh_chat_prompt = conversation_prompt(request.messages) if native_connect else prompt
    current_prompt = base_prompt
    chat_id = initial_chat_id
    required_text_only_failures = 0
    for attempt in range(FORMAT_REPAIR_ATTEMPTS):
        repair_error: Optional[str] = None
        native_system = (
            _native_system_prompt(
                request,
                native_tool_mode,
                required_tools=required_tools,
            )
            if native_connect
            else ""
        )
        raw_output = await _collect_completion(
            chat_id,
            current_prompt,
            stop_after_tool_section=True,
            system_prompt=native_system,
            native_tool_names=native_tool_names,
        )
        empty_output = not raw_output.strip()
        try:
            parsed = parse_tool_output_with_diagnostics(raw_output)
        except ValueError as exc:
            repair_error = str(exc)
            parsed = ParsedAssistantOutput(text="", tool_calls=[])
        if native_tool_mode and not repair_error:
            try:
                parsed = _repair_native_tool_calls(parsed, request)
            except ValueError as exc:
                repair_error = str(exc)
                parsed = ParsedAssistantOutput(text="", tool_calls=[])
        if _tool_output_satisfies_requirement(parsed, required_tools):
            return ParsedAssistantOutput(
                text=parsed.text,
                tool_calls=parsed.tool_calls,
                upstream_chat_id=getattr(kimi, "last_chat_id", None) or chat_id,
            )
        if required_tools and parsed.text.strip() and not parsed.tool_calls and not repair_error:
            required_text_only_failures += 1
        else:
            required_text_only_failures = 0
        if attempt + 1 >= FORMAT_REPAIR_ATTEMPTS:
            break
        required = ",".join(required_tools) or "one available tool"
        logger.warning(
            "Kimi returned an invalid incomplete response; corrective retry=%d required=%s",
            attempt + 1,
            required,
        )
        if empty_output:
            metrics.increment("empty_chat_rotations_total")
            chat_id = "" if _is_k3_upstream() else await kimi.create_chat()
            current_prompt = fresh_chat_prompt
            logger.warning(
                "Kimi returned an empty stream; rotating to a fresh chat before retry=%d",
                attempt + 1,
            )
            continue
        if required_text_only_failures >= 2:
            metrics.increment("required_tool_chat_rotations_total")
            chat_id = "" if _is_k3_upstream() else await kimi.create_chat()
            current_prompt = fresh_chat_prompt
            required_text_only_failures = 0
            logger.warning(
                "Kimi repeatedly returned text while a tool was required; "
                "rotating to a fresh chat before retry=%d",
                attempt + 1,
            )
            continue
        chat_id = getattr(kimi, "last_chat_id", None) or chat_id
        if repair_error:
            correction = (
                f"The previous response was invalid: {repair_error}. "
                "Do not invent or wrap tool names. Choose exactly one tool from "
                "AVAILABLE_CLAW_TOOLS and call it by its exact registered name with valid "
                "arguments. A file path, directory, project, framework, or user phrase is "
                "never a tool name. Output only the tool call and never fabricate its result."
            )
        elif required_tools and native_tool_mode == ALL_CLAW_TOOLS:
            correction = (
                "The previous response did not contain a valid executable Claw tool call. "
                "Choose exactly one real tool from AVAILABLE_CLAW_TOOLS and call it now by "
                "its exact name using one <function_calls> block. End immediately after "
                "</function_calls>. Never fabricate the result."
            )
        elif required_tools:
            correction = (
                "Your previous response was invalid because it contained no executable tool "
                f"call. Call one of these tools now: {required}. Output only one exact "
                "<|tool_calls_section_begin|>...<|tool_calls_section_end|> block with valid "
                "JSON arguments. Do not explain, plan, apologize, or claim completion."
            )
        else:
            correction = (
                "Your previous response only announced future work, repeated itself, or was "
                "empty. Answer the user's latest question directly and concisely now. If more "
                "inspection is truly necessary, emit one real tool call instead. Do not repeat "
                "a plan or say that you are about to inspect something."
            )
        current_prompt = f"[BRIDGE_CORRECTION]\n{correction}"
    if required_tools:
        fallback = _safe_required_any_tool_fallback(request, required_tools)
        if fallback:
            metrics.increment("required_any_tool_fallbacks_total")
            logger.warning(
                "Kimi exhausted required-tool retries; returning safe recovery probe tool=%s",
                fallback.name,
            )
            return ParsedAssistantOutput(
                text="",
                tool_calls=[fallback],
                upstream_chat_id=getattr(kimi, "last_chat_id", None) or chat_id,
            )
        raise RuntimeError("Kimi did not return the explicitly required tool call")
    raise RuntimeError("Kimi did not return a usable response")


def _completion_chunk(
    completion_id: str,
    created: int,
    model: str,
    delta: dict[str, Any],
    finish_reason: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    }


def _sse(data: Any) -> str:
    if data == "[DONE]":
        return "data: [DONE]\n\n"
    return f"data: {json.dumps(data, ensure_ascii=False, separators=(',', ':'))}\n\n"


def _estimated_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text.encode("utf-8")) + 3) // 4)


def _completion_usage(prompt: str, output: str) -> dict[str, int]:
    prompt_tokens = _estimated_tokens(prompt)
    completion_tokens = _estimated_tokens(output)
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
    }


def _parsed_output_text(parsed: ParsedAssistantOutput) -> str:
    tool_text = json.dumps(
        [_openai_tool_call(call) for call in parsed.tool_calls],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"{parsed.text}\n{tool_text}" if parsed.tool_calls else parsed.text


def _openai_tool_call(tool_call: ParsedToolCall, index: Optional[int] = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": tool_call.id,
        "type": "function",
        "function": {
            "name": tool_call.name,
            "arguments": json.dumps(
                tool_call.arguments,
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        },
    }
    if index is not None:
        result["index"] = index
    return result


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "kimi-web-openai-gateway", "status": "ok"}


@app.get("/health")
async def health() -> dict[str, Any]:
    try:
        session = store.load()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    now = int(time.time())
    conversations = session.get("persistent_conversations")
    if not isinstance(conversations, dict):
        conversations = {}
    context_tokens = sum(
        int(
            item.get("upstream_context_tokens_estimated")
            or item.get("estimated_context_tokens")
            or 0
        )
        for item in conversations.values()
        if isinstance(item, dict)
    )
    upstream_messages = sum(
        int(item.get("upstream_message_count") or len(item.get("message_fingerprints") or []))
        for item in conversations.values()
        if isinstance(item, dict)
    )
    return {
        "status": "ok",
        "proxy": KIMI_PROXY or "direct",
        "upstream_model": KIMI_UPSTREAM_MODEL,
        "upstream_scenario": KIMI_SCENARIO,
        "max_concurrent_upstream": MAX_CONCURRENT_UPSTREAM,
        "requests_per_minute": REQUESTS_PER_MINUTE,
        "minimum_request_interval_seconds": round(REQUEST_INTERVAL_SECONDS, 3),
        "local_api_auth": "required" if GATEWAY_API_KEY else "disabled",
        "session_protection": "dpapi" if store.dpapi_protected else "filesystem",
        "persistent_chat": PERSISTENT_CHAT,
        "persistent_chat_count": len(conversations),
        "persistent_messages_estimated_total": upstream_messages,
        "persistent_context_tokens_estimated_total": context_tokens,
        "persistent_messages_limit": MAX_PERSISTENT_MESSAGES,
        "persistent_context_tokens_limit": MAX_PERSISTENT_CONTEXT_TOKENS,
        "access_expires_in": max(0, jwt_expiry(session["access_token"]) - now),
        "refresh_expires_in": max(0, jwt_expiry(session["refresh_token"]) - now),
    }


@app.get("/ready")
async def ready() -> dict[str, Any]:
    try:
        async with upstream_semaphore:
            token = await kimi.access_token()
            async with kimi._client() as client:
                response = await client.get(
                    f"{KIMI_WEB_BASE_URL}/en",
                    headers={"authorization": f"Bearer {token}"},
                )
        if response.status_code != 200:
            raise KimiUpstreamError("readiness", response.status_code)
    except (RuntimeError, httpx.HTTPError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "status": "ready",
        "upstream_status": response.status_code,
        "upstream_model": KIMI_UPSTREAM_MODEL,
        "upstream_scenario": KIMI_SCENARIO,
        "max_concurrent_upstream": MAX_CONCURRENT_UPSTREAM,
        "requests_per_minute": REQUESTS_PER_MINUTE,
    }


@app.get("/metrics")
async def gateway_metrics() -> dict[str, Any]:
    return metrics.snapshot()


@app.get("/v1/models")
async def models() -> dict[str, Any]:
    return {
        "object": "list",
        "data": [
            {
                "id": "kimi-k3",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "moonshot-web",
            },
            {
                "id": "kimi-web",
                "object": "model",
                "created": int(time.time()),
                "owned_by": "moonshot-web",
            },
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest):
    try:
        prompt = messages_to_prompt(
            request.messages,
            tools=request.tools,
            tool_choice=request.tool_choice,
        )
        logger.info(
            "Kimi request bridge messages=%d tools=%d prompt_chars=%d stream=%s",
            len(request.messages),
            len(request.tools or []),
            len(prompt),
            request.stream,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    prefetched_tool_output: ParsedAssistantOutput | None = None
    chat_id = ""
    upstream_prompt = prompt
    conversation_messages = request.messages
    conversation_fingerprints: Optional[list[str]] = None
    conversation_tokens = 0
    conversation_upstream_messages = 0
    conversation_upstream_tokens = 0
    conversation_key = hashlib.sha256(request.model.encode("utf-8")).hexdigest()[:32]

    async def prepare_request() -> ParsedAssistantOutput | None:
        nonlocal chat_id
        nonlocal upstream_prompt
        nonlocal conversation_messages
        nonlocal conversation_fingerprints
        nonlocal conversation_tokens
        nonlocal conversation_upstream_messages
        nonlocal conversation_upstream_tokens
        prepared: ParsedAssistantOutput | None = None
        async with upstream_semaphore:
            if PERSISTENT_CHAT:
                (
                    chat_id,
                    conversation_messages,
                    conversation_fingerprints,
                    conversation_tokens,
                    conversation_upstream_messages,
                    conversation_upstream_tokens,
                ) = await persistent_conversation.prepare(
                    conversation_key,
                    request.messages,
                )
                upstream_prompt = messages_to_prompt(
                    conversation_messages,
                    tools=request.tools,
                    tool_choice=request.tool_choice,
                )
                logger.info(
                    "Kimi persistent session=%s delta_messages=%d context_tokens=%d",
                    conversation_key[:12],
                    len(conversation_messages),
                    conversation_tokens,
                )
            else:
                chat_id = "" if _is_k3_upstream() else await kimi.create_chat()
            if request.tools:
                prepared = await _collect_tool_output_with_retry(
                    request,
                    upstream_prompt,
                    chat_id,
                    conversation_messages=conversation_messages,
                )
                if (
                    PERSISTENT_CHAT
                    and conversation_fingerprints is not None
                    and prepared.upstream_chat_id
                ):
                    chat_id = prepared.upstream_chat_id
                    await persistent_conversation.commit(
                        conversation_key,
                        chat_id,
                        conversation_fingerprints,
                        conversation_tokens,
                        conversation_upstream_messages,
                        conversation_upstream_tokens,
                    )
        return prepared

    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    if request.stream:
        async def generate() -> AsyncIterator[str]:
            nonlocal chat_id
            output_parts: list[str] = []
            yield _sse(
                _completion_chunk(
                    completion_id,
                    created,
                    request.model,
                    {"role": "assistant"},
                )
            )
            try:
                prepared = await prepare_request()
                if request.tools:
                    if prepared is None:
                        raise RuntimeError("Kimi tool preflight returned no result")
                    parsed = prepared
                    if parsed.text:
                        output_parts.append(parsed.text)
                        yield _sse(
                            _completion_chunk(
                                completion_id,
                                created,
                                request.model,
                                {"content": parsed.text},
                            )
                        )
                    for index, tool_call in enumerate(parsed.tool_calls):
                        metrics.increment("tool_calls_total")
                        output_parts.append(
                            json.dumps(
                                _openai_tool_call(tool_call),
                                ensure_ascii=False,
                                separators=(",", ":"),
                            )
                        )
                        yield _sse(
                            _completion_chunk(
                                completion_id,
                                created,
                                request.model,
                                {"tool_calls": [_openai_tool_call(tool_call, index=index)]},
                            )
                        )
                    finish_reason = "tool_calls" if parsed.tool_calls else "stop"
                else:
                    async with upstream_semaphore:
                        async for text in kimi.iter_completion(chat_id, upstream_prompt):
                            output_parts.append(text)
                            yield _sse(
                                _completion_chunk(
                                    completion_id,
                                    created,
                                    request.model,
                                    {"content": text},
                                )
                            )
                    finish_reason = "stop"
                    if PERSISTENT_CHAT and conversation_fingerprints is not None:
                        chat_id = getattr(kimi, "last_chat_id", None) or chat_id
                        await persistent_conversation.commit(
                            conversation_key,
                            chat_id,
                            conversation_fingerprints,
                            conversation_tokens,
                            conversation_upstream_messages,
                            conversation_upstream_tokens,
                        )
                yield _sse(
                    _completion_chunk(
                        completion_id,
                        created,
                        request.model,
                        {},
                        finish_reason=finish_reason,
                    )
                )
                yield _sse(
                    {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": request.model,
                        "choices": [],
                        "usage": _completion_usage(upstream_prompt, "".join(output_parts)),
                    }
                )
            except (RuntimeError, ValueError, httpx.HTTPError) as exc:
                if PERSISTENT_CHAT and chat_id and _persistent_chat_is_gone(exc):
                    try:
                        await persistent_conversation.clear_if_current(
                            conversation_key,
                            chat_id,
                        )
                    except RuntimeError:
                        logger.exception("Failed to clear broken persistent Kimi chat")
                logger.exception("Kimi streaming completion failed")
                yield _sse(
                    {
                        "error": {
                            "message": str(exc)[:1000] or "Kimi upstream completion failed",
                            "type": "upstream_error",
                        }
                    }
                )
            yield _sse("[DONE]")

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    try:
        prefetched_tool_output = await prepare_request()
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        if PERSISTENT_CHAT and chat_id and _persistent_chat_is_gone(exc):
            try:
                await persistent_conversation.clear_if_current(conversation_key, chat_id)
            except RuntimeError:
                logger.exception("Failed to clear broken persistent Kimi chat")
        logger.exception("Kimi request preflight failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    try:
        if request.tools:
            if prefetched_tool_output is None:
                raise RuntimeError("Kimi tool preflight returned no result")
            parsed = prefetched_tool_output
        else:
            async with upstream_semaphore:
                raw_output = await _collect_completion(
                    chat_id,
                    upstream_prompt,
                    stop_after_tool_section=False,
                )
            if not raw_output.strip():
                metrics.increment("empty_completions_total")
                raise RuntimeError("Kimi returned an empty completion")
            parsed = ParsedAssistantOutput(text=raw_output, tool_calls=[])
            if PERSISTENT_CHAT and conversation_fingerprints is not None:
                chat_id = getattr(kimi, "last_chat_id", None) or chat_id
                await persistent_conversation.commit(
                    conversation_key,
                    chat_id,
                    conversation_fingerprints,
                    conversation_tokens,
                    conversation_upstream_messages,
                    conversation_upstream_tokens,
                )
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        if PERSISTENT_CHAT and chat_id and _persistent_chat_is_gone(exc):
            try:
                await persistent_conversation.clear_if_current(conversation_key, chat_id)
            except RuntimeError:
                logger.exception("Failed to clear broken persistent Kimi chat")
        logger.exception("Kimi non-streaming completion failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    message: dict[str, Any] = {
        "role": "assistant",
        "content": parsed.text or None,
    }
    if parsed.tool_calls:
        metrics.increment("tool_calls_total", len(parsed.tool_calls))
        message["tool_calls"] = [
            _openai_tool_call(tool_call) for tool_call in parsed.tool_calls
        ]
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": request.model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if parsed.tool_calls else "stop",
            }
        ],
        "usage": _completion_usage(upstream_prompt, _parsed_output_text(parsed)),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("KIMI_BIND_HOST", "127.0.0.1"),
        port=int(os.getenv("KIMI_PORT", "18081")),
        log_level=os.getenv("UVICORN_LOG_LEVEL", "info"),
    )
