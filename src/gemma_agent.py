"""Gemma 4 31B agent runtime for the claw-code harness.

Wraps GemmaAgentClient in an agentic loop that:
  - Injects the system prompt with available tools
  - Parses tool calls from the model response
  - Executes them via ExecutionRegistry
  - Feeds results back for the next turn
  - Iterates until the model stops calling tools or max_turns is reached
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field

from .execution_registry import ExecutionRegistry, build_execution_registry
from .gemma_client import GemmaAgentClient, GemmaTurnResult
from .tools import PORTED_TOOLS

# Tools exposed to Gemma as callable functions.
# Kept to a safe, read-only subset of the mirrored inventory.
_AGENT_TOOL_NAMES = frozenset({
    'BashTool',
    'FileReadTool',
    'FileEditTool',
    'GlobTool',
    'GrepTool',
    'WebSearchTool',
    'WebFetchTool',
    'TodoReadTool',
    'TodoWriteTool',
    'MemoryReadTool',
})

_SYSTEM_PROMPT_TEMPLATE = """\
You are Claw Code — an AI software-engineering agent powered by Gemma 4 31B.

You have access to a set of tools described below. When you want to use a tool,
respond with a JSON block like:

```tool_call
{{"tool": "<tool_name>", "payload": "<argument>"}}
```

After seeing the tool result, continue your reasoning.
When you have enough information to fully answer the user, respond normally without
a tool_call block.

Available tools:
{tool_list}
"""


def _build_tool_list() -> str:
    lines: list[str] = []
    for module in PORTED_TOOLS:
        if module.name in _AGENT_TOOL_NAMES:
            lines.append(f'- {module.name}: {module.responsibility}')
    return '\n'.join(lines) if lines else '(none)'


def _build_gemma_tool_declarations() -> list[dict[str, object]]:
    """Build FunctionDeclaration-compatible dicts for Gemma native tool calling."""
    declarations: list[dict[str, object]] = []
    for module in PORTED_TOOLS:
        if module.name in _AGENT_TOOL_NAMES:
            declarations.append({
                'name': module.name,
                'description': module.responsibility,
                'parameters': {
                    'type': 'object',
                    'properties': {
                        'payload': {
                            'type': 'string',
                            'description': 'The argument or input for this tool.',
                        },
                    },
                    'required': ['payload'],
                },
            })
    return declarations


@dataclass
class AgentTurn:
    turn: int
    prompt: str
    reply: str
    tool_calls: list[dict[str, object]]
    tool_results: list[str]
    finish_reason: str
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class AgentSession:
    model: str
    turns: list[AgentTurn] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0

    def add(self, turn: AgentTurn) -> None:
        self.turns.append(turn)
        self.total_input_tokens += turn.input_tokens
        self.total_output_tokens += turn.output_tokens

    def as_markdown(self) -> str:
        lines = [f'# Gemma Agent Session — {self.model}', '']
        for t in self.turns:
            lines += [
                f'## Turn {t.turn}',
                f'**User:** {t.prompt}',
                '',
                f'**Gemma:** {t.reply}',
            ]
            if t.tool_calls:
                lines.append('')
                lines.append('**Tool calls:**')
                for i, (tc, tr) in enumerate(zip(t.tool_calls, t.tool_results), 1):
                    lines.append(f'  {i}. `{tc["name"]}({tc.get("args", {}).get("payload", tc.get("payload", ""))})` → {tr}')
            lines.append('')
        lines += [
            '---',
            f'Total input tokens:  {self.total_input_tokens}',
            f'Total output tokens: {self.total_output_tokens}',
        ]
        return '\n'.join(lines)


class GemmaAgentRuntime:
    """Runs a multi-turn agent loop using Gemma 4 31B."""

    def __init__(
        self,
        model_name: str = 'gemma-4-31b-it',
        api_key: str = '',
        max_turns: int = 10,
        use_native_tools: bool = True,
        temperature: float = 0.7,
    ) -> None:
        system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(tool_list=_build_tool_list())
        self.client = GemmaAgentClient(
            model_name=model_name,
            api_key=api_key,
            system_prompt=system_prompt,
            temperature=temperature,
        )
        self.max_turns = max_turns
        self.use_native_tools = use_native_tools
        self.registry: ExecutionRegistry = build_execution_registry()
        self._tool_declarations = _build_gemma_tool_declarations() if use_native_tools else []

    # ------------------------------------------------------------------ #
    # public                                                               #
    # ------------------------------------------------------------------ #

    def run(self, initial_prompt: str) -> AgentSession:
        """Run a complete agentic session starting from *initial_prompt*."""
        session = AgentSession(model=self.client.model_name)
        prompt = initial_prompt
        turn_index = 0

        while turn_index < self.max_turns:
            turn_index += 1
            result = self.client.chat(prompt, tools=self._tool_declarations or None)
            tool_results = self._execute_tool_calls(result)

            session.add(AgentTurn(
                turn=turn_index,
                prompt=prompt,
                reply=result.reply,
                tool_calls=result.tool_calls,
                tool_results=tool_results,
                finish_reason=result.finish_reason,
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
            ))

            if not result.tool_calls or not tool_results:
                break

            # Feed tool results back as the next user turn
            prompt = self._format_tool_results(tool_results)

        return session

    def run_interactive(self) -> None:
        """Run an interactive REPL session in the terminal."""
        print(f'Gemma Agent ({self.client.model_name}) — type "exit" or Ctrl-C to quit\n')
        while True:
            try:
                user_input = input('You: ').strip()
            except (EOFError, KeyboardInterrupt):
                print('\nBye!')
                break
            if not user_input:
                continue
            if user_input.lower() in {'exit', 'quit', '/exit', '/quit'}:
                print('Bye!')
                break

            try:
                session = self.run(user_input)
            except Exception as exc:  # noqa: BLE001
                print(f'[error] {exc}', file=sys.stderr)
                continue

            last_turn = session.turns[-1] if session.turns else None
            if last_turn:
                print(f'\nGemma: {last_turn.reply}')
                if last_turn.tool_calls:
                    for tc, tr in zip(last_turn.tool_calls, last_turn.tool_results):
                        name = tc.get('name', '?')
                        args = tc.get('args', {}).get('payload', tc.get('payload', ''))
                        print(f'  [tool] {name}({args!r}) → {tr}')
            print()
            # Keep conversation going — history lives in self.client.history

    # ------------------------------------------------------------------ #
    # private                                                              #
    # ------------------------------------------------------------------ #

    def _execute_tool_calls(self, result: GemmaTurnResult) -> list[str]:
        outputs: list[str] = []
        for tc in result.tool_calls:
            name = tc.get('name', '')
            # Native function call (from google-genai)
            args = tc.get('args', {})
            payload = args.get('payload', '') if isinstance(args, dict) else ''
            output = self._dispatch_tool(name, str(payload))
            outputs.append(output)
        return outputs

    def _dispatch_tool(self, name: str, payload: str) -> str:
        tool = self.registry.tool(name)
        if tool:
            return tool.execute(payload)
        return f'[unknown tool: {name}]'

    @staticmethod
    def _format_tool_results(results: list[str]) -> str:
        parts = [f'Tool result {i + 1}: {r}' for i, r in enumerate(results)]
        return '\n'.join(parts)
