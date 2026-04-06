"""Gemma 4 31B API client for the claw-code agent harness.

Requires:  pip install google-genai
Set env:   GOOGLE_API_KEY=<your Google AI Studio key>

Model ID:  gemma-4-31b-it  (instruction-tuned variant)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Generator, Sequence

try:
    from google import genai
    from google.genai import types as genai_types
    GENAI_AVAILABLE = True
except ImportError:
    GENAI_AVAILABLE = False

GEMMA_DEFAULT_MODEL = 'gemma-4-31b-it'


@dataclass
class GemmaMessage:
    role: str   # 'user' or 'model'
    content: str


@dataclass
class GemmaTurnResult:
    prompt: str
    reply: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: list[dict[str, object]] = field(default_factory=list)
    finish_reason: str = 'stop'


@dataclass
class GemmaAgentClient:
    """Thin client around Google GenAI SDK targeting Gemma 4 31B."""

    model_name: str = GEMMA_DEFAULT_MODEL
    api_key: str = field(default_factory=lambda: os.environ.get('GOOGLE_API_KEY', ''))
    system_prompt: str = ''
    temperature: float = 0.7
    max_output_tokens: int = 8192
    history: list[GemmaMessage] = field(default_factory=list)
    _client: object = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if not GENAI_AVAILABLE:
            raise ImportError(
                'google-genai is required. Install with:\n'
                '  pip install google-genai'
            )
        if not self.api_key:
            raise ValueError(
                'GOOGLE_API_KEY environment variable is not set.\n'
                'Get a free key at https://aistudio.google.com/apikey'
            )
        self._client = genai.Client(api_key=self.api_key)

    # ------------------------------------------------------------------ #
    # public interface                                                     #
    # ------------------------------------------------------------------ #

    def chat(self, prompt: str, tools: Sequence[dict[str, object]] | None = None) -> GemmaTurnResult:
        """Send one user message and return the model reply (non-streaming)."""
        self.history.append(GemmaMessage(role='user', content=prompt))
        contents = self._build_contents()
        config = self._build_config(tools)

        response = self._client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )

        reply_text, tool_calls = self._extract_response(response)
        self.history.append(GemmaMessage(role='model', content=reply_text))

        usage = getattr(response, 'usage_metadata', None)
        return GemmaTurnResult(
            prompt=prompt,
            reply=reply_text,
            model=self.model_name,
            input_tokens=getattr(usage, 'prompt_token_count', 0) if usage else 0,
            output_tokens=getattr(usage, 'candidates_token_count', 0) if usage else 0,
            tool_calls=tool_calls,
            finish_reason=self._finish_reason(response),
        )

    def stream_chat(
        self,
        prompt: str,
        tools: Sequence[dict[str, object]] | None = None,
    ) -> Generator[str, None, GemmaTurnResult]:
        """Stream the reply chunk-by-chunk; yields text deltas."""
        self.history.append(GemmaMessage(role='user', content=prompt))
        contents = self._build_contents()
        config = self._build_config(tools)

        full_text = ''
        for chunk in self._client.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=config,
        ):
            delta = self._chunk_text(chunk)
            if delta:
                full_text += delta
                yield delta

        self.history.append(GemmaMessage(role='model', content=full_text))
        return GemmaTurnResult(
            prompt=prompt,
            reply=full_text,
            model=self.model_name,
            finish_reason='stop',
        )

    def reset(self) -> None:
        """Clear conversation history."""
        self.history.clear()

    # ------------------------------------------------------------------ #
    # private helpers                                                     #
    # ------------------------------------------------------------------ #

    def _build_contents(self) -> list[dict[str, str]]:
        return [{'role': msg.role, 'parts': [{'text': msg.content}]} for msg in self.history]

    def _build_config(self, tools: Sequence[dict[str, object]] | None) -> object:
        kwargs: dict[str, object] = {
            'temperature': self.temperature,
            'max_output_tokens': self.max_output_tokens,
        }
        if self.system_prompt:
            kwargs['system_instruction'] = self.system_prompt
        if tools:
            fn_declarations = [
                genai_types.FunctionDeclaration(**t) for t in tools
            ]
            kwargs['tools'] = [genai_types.Tool(function_declarations=fn_declarations)]
        return genai_types.GenerateContentConfig(**kwargs)

    @staticmethod
    def _extract_response(response: object) -> tuple[str, list[dict[str, object]]]:
        text_parts: list[str] = []
        tool_calls: list[dict[str, object]] = []
        try:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'text') and part.text:
                    text_parts.append(part.text)
                if hasattr(part, 'function_call') and part.function_call:
                    fc = part.function_call
                    tool_calls.append({'name': fc.name, 'args': dict(fc.args)})
        except (AttributeError, IndexError):
            pass
        return ''.join(text_parts), tool_calls

    @staticmethod
    def _chunk_text(chunk: object) -> str:
        try:
            return chunk.candidates[0].content.parts[0].text or ''
        except (AttributeError, IndexError):
            return ''

    @staticmethod
    def _finish_reason(response: object) -> str:
        try:
            reason = response.candidates[0].finish_reason
            return reason.name if hasattr(reason, 'name') else str(reason)
        except (AttributeError, IndexError):
            return 'unknown'
