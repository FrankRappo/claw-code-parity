import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import kimi_api_server as kimi


class LocalGatewayAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_v1_routes_require_the_configured_bearer_key(self):
        original = kimi.GATEWAY_API_KEY
        kimi.GATEWAY_API_KEY = "local-secret"
        try:
            transport = httpx.ASGITransport(app=kimi.app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                denied = await client.get("/v1/models")
                allowed = await client.get(
                    "/v1/models",
                    headers={"Authorization": "Bearer local-secret"},
                )
        finally:
            kimi.GATEWAY_API_KEY = original
        self.assertEqual(denied.status_code, 401)
        self.assertEqual(allowed.status_code, 200)

    async def test_unknown_forced_tool_choice_returns_http_400(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="run it")],
            tools=[
                {
                    "type": "function",
                    "function": {"name": "read_file", "parameters": {}},
                }
            ],
            tool_choice={
                "type": "function",
                "function": {"name": "not_registered"},
            },
        )
        with self.assertRaises(kimi.HTTPException) as raised:
            await kimi.chat_completions(request)
        self.assertEqual(raised.exception.status_code, 400)


class MessageFormattingTests(unittest.TestCase):
    @staticmethod
    def operational_tools():
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "PowerShell",
                    "parameters": {
                        "type": "object",
                        "properties": {"command": {"type": "string"}},
                        "required": ["command"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "glob_search",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "edit_file",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    def test_single_user_message_is_unchanged(self):
        messages = [kimi.Message(role="user", content="hello")]
        self.assertEqual(kimi.messages_to_prompt(messages), "hello")

    def test_conversation_roles_are_preserved(self):
        messages = [
            kimi.Message(role="system", content="be concise"),
            kimi.Message(role="user", content="question"),
            kimi.Message(role="assistant", content="answer"),
            kimi.Message(role="user", content="follow-up"),
        ]
        prompt = kimi.messages_to_prompt(messages)
        self.assertIn("[SYSTEM]\nbe concise", prompt)
        self.assertIn("[ASSISTANT]\nanswer", prompt)
        self.assertTrue(prompt.endswith("[USER]\nfollow-up"))

    def test_structured_text_content_is_flattened(self):
        message = kimi.Message(
            role="user",
            content=[
                {"type": "text", "text": "one"},
                {"type": "input_text", "text": "two"},
            ],
        )
        self.assertEqual(kimi.messages_to_prompt([message]), "one\ntwo")

    def test_tools_and_tool_results_are_rendered_for_kimi(self):
        messages = [
            kimi.Message(role="user", content="inspect the file"),
            kimi.Message(
                role="assistant",
                content=None,
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "Read", "arguments": '{"file_path":"a.txt"}'},
                    }
                ],
            ),
            kimi.Message(role="tool", tool_call_id="call_1", content="hello"),
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "Read",
                    "description": "Read a file",
                    "parameters": {"type": "object"},
                },
            }
        ]
        prompt = kimi.messages_to_prompt(messages, tools=tools, tool_choice="auto")
        self.assertIn("AVAILABLE_TOOLS", prompt)
        self.assertIn('"name":"Read"', prompt)
        self.assertIn("[TOOL_RESULT id=call_1]", prompt)
        self.assertIn("tool_calls_section_begin", prompt)

    def test_full_claw_system_prompt_is_preserved(self):
        system = "# System\n" + ("full capability instruction\n" * 2000)
        prompt = kimi.messages_to_prompt(
            [
                kimi.Message(role="system", content=system),
                kimi.Message(role="user", content="use Bash"),
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "Bash",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
        self.assertIn(system, prompt)

    def test_auto_tool_choice_does_not_parse_user_language(self):
        tools = [
            {"type": "function", "function": {"name": "Bash", "parameters": {}}},
            {"type": "function", "function": {"name": "Read", "parameters": {}}},
        ]
        user = kimi.Message(role="user", content="Use Bash and then use Read")
        self.assertEqual(
            kimi._required_next_tools([user], tools, "auto"),
            [],
        )
        bash_call = kimi.Message(
            role="assistant",
            tool_calls=[{"function": {"name": "Bash", "arguments": "{}"}}],
        )
        self.assertEqual(
            kimi._required_next_tools([user, bash_call], tools, "auto"),
            [],
        )
        read_call = kimi.Message(
            role="assistant",
            tool_calls=[{"function": {"name": "Read", "arguments": "{}"}}],
        )
        self.assertEqual(
            kimi._required_next_tools([user, bash_call, read_call], tools, "auto"),
            [],
        )

    def test_local_project_path_is_left_to_kimi_turn_decision(self):
        messages = [
            kimi.Message(
                role="user",
                content=r"Inspect the existing project at C:\claw cod\kimi-windows-agent",
            )
        ]
        tools = [
            {"type": "function", "function": {"name": "glob_search"}},
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "WebSearch"}},
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, tools, "auto"),
            [],
        )

    def test_native_tool_selector_registers_all_claw_tools(self):
        tools = [
            {"type": "function", "function": {"name": "read_file"}},
            {"type": "function", "function": {"name": "glob_search"}},
            {"type": "function", "function": {"name": "WebSearch"}},
        ]
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[
                kimi.Message(
                    role="user", content=r"Inspect C:\claw cod\kimi-windows-agent"
                )
            ],
            tools=tools,
            tool_choice="auto",
        )
        self.assertEqual(kimi._native_tool_mode(request), kimi.ALL_CLAW_TOOLS)

    def test_all_native_tools_system_prompt_contains_all_schemas(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Сделай локальную задачу")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        prompt = kimi._native_system_prompt(
            request,
            kimi.ALL_CLAW_TOOLS,
            required_tools=["<any-available-tool>"],
        )
        self.assertIn("read_file", prompt)
        self.assertIn("PowerShell", prompt)
        self.assertIn("edit_file", prompt)
        self.assertIn('name="REAL_TOOL_NAME"', prompt)
        self.assertIn('<parameter name="ARGUMENT_NAME">', prompt)
        self.assertIn("You MUST call one applicable tool", prompt)
        self.assertNotIn("Autonomous execution policy", prompt)
        self.assertNotIn("explicitly authorizes placing a credential", prompt)

    def test_registered_tool_name_is_canonicalized_case_insensitively(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Прочитай файл")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_native",
                    name="READ_FILE",
                    arguments={"path": r"C:\Users\user\Desktop\probe.txt"},
                )
            ],
        )
        repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(repaired.tool_calls[0].name, "read_file")
        self.assertEqual(
            repaired.tool_calls[0].arguments,
            {"path": r"C:\Users\user\Desktop\probe.txt"},
        )

    def test_native_registry_rejects_unknown_tool(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Сделай задачу")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_unknown",
                    name="not_a_real_tool",
                    arguments={},
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "unknown Claw tool"):
            kimi._repair_native_tool_calls(parsed, request)

    def test_native_bridge_prefix_maps_only_to_registered_tool(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Прочитай файл")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        for generated_name in (
            "bridge_read_file",
            "claw_read_file",
            "FileSystem_ReadFile",
            "FileRead",
        ):
            parsed = kimi.ParsedAssistantOutput(
                text="",
                tool_calls=[
                    kimi.ParsedToolCall(
                        id="call_bridge",
                        name=generated_name,
                        arguments={"path": r"C:\Users\user\Desktop\probe.txt"},
                    )
                ],
            )
            repaired = kimi._repair_native_tool_calls(parsed, request)
            self.assertEqual(repaired.tool_calls[0].name, "read_file")

        write_parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_file_write",
                    name="FileWrite",
                    arguments={"path": "probe.txt", "content": "ok"},
                )
            ],
        )
        write_repaired = kimi._repair_native_tool_calls(write_parsed, request)
        self.assertEqual(write_repaired.tool_calls[0].name, "write_file")

        unknown = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_bridge_unknown",
                    name="bridge_not_registered",
                    arguments={},
                )
            ],
        )
        with self.assertRaisesRegex(ValueError, "unknown Claw tool"):
            kimi._repair_native_tool_calls(unknown, request)

        for malicious_name in (
            "evil_read_file",
            "delete_read_file",
            "arbitraryPowerShell",
        ):
            malicious = kimi.ParsedAssistantOutput(
                text="",
                tool_calls=[
                    kimi.ParsedToolCall(
                        id="call_malicious",
                        name=malicious_name,
                        arguments={},
                    )
                ],
            )
            with self.assertRaisesRegex(ValueError, "unknown Claw tool"):
                kimi._repair_native_tool_calls(malicious, request)

    def test_runtime_terminal_alias_maps_to_registered_windows_shell(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Выполни локальную команду")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        for generated_name in (
            "run_terminal_command",
            "claw_execute_command",
            "shell",
            "cmd",
            "computer",
        ):
            parsed = kimi.ParsedAssistantOutput(
                text="",
                tool_calls=[
                    kimi.ParsedToolCall(
                        id="call_terminal",
                        name=generated_name,
                        arguments={"command": "Write-Output OK"},
                    )
                ],
            )
            with mock.patch.object(kimi, "RUNTIME_PLATFORM", "windows"):
                repaired = kimi._repair_native_tool_calls(parsed, request)
            self.assertEqual(repaired.tool_calls[0].name, "PowerShell")

    def test_prefixed_runtime_terminal_alias_maps_to_registered_windows_shell(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Выполни локальную команду")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_terminal",
                    name="claw_terminal",
                    arguments={"command": "Write-Output OK", "description": "probe"},
                )
            ],
        )
        with mock.patch.object(kimi, "RUNTIME_PLATFORM", "windows"):
            repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(repaired.tool_calls[0].name, "PowerShell")

    def test_observed_claw_wrapper_unwraps_only_a_registered_tool(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Прочитай файл")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_wrapper",
                    name="Claw",
                    arguments={
                        "name": "read_file",
                        "path": r"C:\Users\user\Desktop\asd.txt",
                        "command": "ignored outer-envelope field",
                    },
                )
            ],
        )
        repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(repaired.tool_calls[0].name, "read_file")
        self.assertEqual(
            repaired.tool_calls[0].arguments,
            {"path": r"C:\Users\user\Desktop\asd.txt"},
        )

        parsed.tool_calls[0].arguments["name"] = "not_registered"
        with self.assertRaisesRegex(ValueError, "unknown Claw tool"):
            kimi._repair_native_tool_calls(parsed, request)

    def test_observed_claw_command_wrapper_maps_to_runtime_shell(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Выполни команду")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_wrapper",
                    name="Claw",
                    arguments={"command": "Write-Output OK"},
                )
            ],
        )
        with mock.patch.object(kimi, "RUNTIME_PLATFORM", "windows"):
            repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(repaired.tool_calls[0].name, "PowerShell")
        self.assertEqual(
            repaired.tool_calls[0].arguments,
            {"command": "Write-Output OK", "timeout": kimi.DEFAULT_SHELL_TIMEOUT_MS},
        )

    def test_runtime_python_alias_maps_code_to_registered_windows_shell(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Выполни локальную проверку")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_python",
                    name="python",
                    arguments={"code": "print('PYTHON_ALIAS_OK')"},
                )
            ],
        )
        with mock.patch.object(kimi, "RUNTIME_PLATFORM", "windows"):
            repaired = kimi._repair_native_tool_calls(parsed, request)
        call = repaired.tool_calls[0]
        self.assertEqual(call.name, "PowerShell")
        self.assertEqual(set(call.arguments), {"command", "timeout"})
        self.assertEqual(call.arguments["timeout"], kimi.DEFAULT_SHELL_TIMEOUT_MS)
        self.assertIn("FromBase64String", call.arguments["command"])
        self.assertNotIn("PYTHON_ALIAS_OK", call.arguments["command"])

    def test_observed_file_finder_alias_maps_arguments_to_glob_search(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Найди asd.txt")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_find",
                    name="FileFinder",
                    arguments={
                        "file_name": "asd.txt",
                        "search_path": r"C:\Users\user\Desktop",
                    },
                )
            ],
        )
        repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(repaired.tool_calls[0].name, "glob_search")
        self.assertEqual(
            repaired.tool_calls[0].arguments,
            {"pattern": "asd.txt", "path": r"C:\Users\user\Desktop"},
        )

    def test_list_dir_alias_maps_to_registered_glob_search(self):
        path = r"\\wsl.localhost\Ubuntu-24.04\work\project"
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="inspect the project")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_list",
                    name="list_dir",
                    arguments={"path": path},
                )
            ],
        )
        repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(repaired.tool_calls[0].name, "glob_search")
        self.assertEqual(
            repaired.tool_calls[0].arguments,
            {"path": path, "pattern": "**/*"},
        )

    def test_observed_filesystem_command_wrapper_maps_to_runtime_shell(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Выполни команду")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_filesystem",
                    name="FileSystem",
                    arguments={
                        "command": "Write-Output OK",
                        "path": "unused",
                        "pattern": "unused",
                    },
                )
            ],
        )
        with mock.patch.object(kimi, "RUNTIME_PLATFORM", "windows"):
            repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(repaired.tool_calls[0].name, "PowerShell")
        self.assertEqual(
            repaired.tool_calls[0].arguments,
            {"command": "Write-Output OK", "timeout": kimi.DEFAULT_SHELL_TIMEOUT_MS},
        )

    def test_forced_tool_choice_must_exist_in_request_registry(self):
        tools = self.operational_tools()
        messages = [kimi.Message(role="user", content="run the selected tool")]
        self.assertEqual(
            kimi._required_next_tools(
                messages,
                tools,
                {"type": "function", "function": {"name": "powershell"}},
            ),
            ["PowerShell"],
        )
        with self.assertRaisesRegex(ValueError, "unavailable tool"):
            kimi._required_next_tools(
                messages,
                tools,
                {"type": "function", "function": {"name": "not_registered"}},
            )

    def test_explicit_tool_word_is_left_to_kimi_turn_decision(self):
        tools = [
            {"type": "function", "function": {"name": "bash"}},
            {"type": "function", "function": {"name": "PowerShell"}},
        ]
        messages = [
            kimi.Message(role="user", content="Выполни echo READY через Bash")
        ]
        self.assertEqual(kimi._required_next_tools(messages, tools, "auto"), [])

    def test_project_inspection_reads_a_file_after_glob(self):
        tools = [
            {"type": "function", "function": {"name": "glob_search"}},
            {"type": "function", "function": {"name": "read_file"}},
        ]
        messages = [
            kimi.Message(role="user", content=r"Изучи C:\claw cod\project"),
            kimi.Message(
                role="assistant",
                tool_calls=[{"function": {"name": "glob_search", "arguments": "{}"}}],
            ),
            kimi.Message(role="tool", name="glob_search", content='{"filenames":[]}'),
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, tools, "auto"), []
        )

    def test_glob_results_exclude_dependency_noise(self):
        raw = json.dumps(
            {
                "numFiles": 4,
                "filenames": [
                    r"C:\project\.venv\Lib\package.py",
                    r"C:\project\__pycache__\module.pyc",
                    r"C:\project\README.md",
                    r"C:\project\src\main.py",
                ],
            }
        )
        prepared = json.loads(kimi._prepare_tool_result("glob_search", raw))
        self.assertEqual(
            prepared["filenames"],
            [r"C:\project\README.md", r"C:\project\src\main.py"],
        )
        self.assertEqual(prepared["numFiles"], 2)

    def test_final_conversation_prompt_compacts_tool_results(self):
        messages = [kimi.Message(role="user", content="inspect")]
        for index in range(5):
            messages.extend(
                [
                    kimi.Message(
                        role="assistant",
                        tool_calls=[
                            {
                                "id": f"call_{index}",
                                "function": {"name": "read_file", "arguments": "{}"},
                            }
                        ],
                    ),
                    kimi.Message(
                        role="tool",
                        tool_call_id=f"call_{index}",
                        content="x" * 10_000,
                    ),
                ]
            )
        prompt = kimi._conversation_prompt(messages, tool_result_limit=2_500)
        self.assertLess(len(prompt), 15_000)
        self.assertIn("TRUNCATED_TOOL_RESULT", prompt)

    def test_conversation_prompt_keeps_latest_tool_result_larger(self):
        messages = [kimi.Message(role="user", content="inspect")]
        for index, marker in enumerate(("A", "B", "C")):
            messages.extend(
                [
                    kimi.Message(
                        role="assistant",
                        tool_calls=[
                            {
                                "id": f"call_{index}",
                                "function": {
                                    "name": "read_file",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    ),
                    kimi.Message(
                        role="tool",
                        tool_call_id=f"call_{index}",
                        content=marker * 10_000,
                    ),
                ]
            )

        prompt = kimi._conversation_prompt(
            messages,
            tool_result_limit=3_000,
            older_tool_result_limit=800,
        )

        self.assertLess(len(prompt), 6_500)
        self.assertEqual(prompt.count("TRUNCATED_TOOL_RESULT"), 3)
        self.assertGreater(prompt.count("C"), prompt.count("A"))

    def test_latest_project_source_file_fits_checkpoint_prompt(self):
        source = "x" * 15_000
        prompt = kimi._conversation_prompt(
            [
                kimi.Message(role="user", content="read source"),
                kimi.Message(role="tool", tool_call_id="call_source", content=source),
            ],
            tool_result_limit=16_000,
            older_tool_result_limit=800,
        )
        self.assertNotIn("TRUNCATED_TOOL_RESULT", prompt)
        self.assertIn(source, prompt)

    def test_native_glob_recovers_path_from_user_request(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[
                kimi.Message(
                    role="user",
                    content=(
                        r"Изучи C:\claw cod\kimi-windows-agent и затем ответь кратко"
                    ),
                )
            ],
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_native",
                    name="glob_search",
                    arguments={"pattern": "**/*"},
                )
            ],
        )
        repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(
            repaired.tool_calls[0].arguments["path"],
            r"C:\claw cod\kimi-windows-agent",
        )

    def test_native_glob_stops_path_at_sentence_boundary(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[
                kimi.Message(
                    role="user",
                    content=(
                        r"Есть наработки C:\claw cod\kimi-windows-agent. "
                        "Сначала изучи проект."
                    ),
                )
            ],
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_native",
                    name="glob_search",
                    arguments={
                        "path": r"C:\claw cod\kimi-windows-agent. Сначала изучи проект",
                        "pattern": r"C:\claw cod\kimi-windows-agent\**\*",
                    },
                )
            ],
        )
        repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(
            repaired.tool_calls[0].arguments,
            {"path": r"C:\claw cod\kimi-windows-agent", "pattern": "**/*"},
        )

    def test_native_glob_stops_path_before_double_space_annotation(self):
        self.assertEqual(
            kimi._extract_local_path(
                r"C:\claw cod\kimi-windows-agent  вот путь"
            ),
            r"C:\claw cod\kimi-windows-agent",
        )

    def test_follow_up_inspection_requires_a_new_tool_call(self):
        tools = [
            {"type": "function", "function": {"name": "glob_search"}},
            {"type": "function", "function": {"name": "read_file"}},
        ]
        messages = [
            kimi.Message(role="user", content=r"Project: C:\claw cod\agent"),
            kimi.Message(
                role="assistant",
                tool_calls=[{"function": {"name": "glob_search", "arguments": "{}"}}],
            ),
            kimi.Message(role="tool", tool_call_id="call_1", content="files"),
            kimi.Message(role="user", content="смотри дальше"),
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, tools, "auto"),
            [],
        )

    def test_status_question_uses_existing_results_instead_of_more_tools(self):
        tools = [
            {"type": "function", "function": {"name": "read_file"}},
        ]
        messages = [
            kimi.Message(role="user", content=r"Project: C:\claw cod\agent"),
            kimi.Message(
                role="assistant",
                tool_calls=[{"function": {"name": "read_file", "arguments": "{}"}}],
            ),
            kimi.Message(role="tool", tool_call_id="call_1", content="file contents"),
            kimi.Message(role="user", content="посмотрел?"),
        ]
        self.assertEqual(kimi._required_next_tools(messages, tools, "auto"), [])

    def test_operational_request_with_absolute_file_is_left_to_kimi(self):
        messages = [
            kimi.Message(
                role="user",
                content=(
                    "Нам надо настроить прокси в Chrome. Данные VPS лежат в "
                    r"C:\Users\user\Desktop\asd.txt"
                ),
            )
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, self.operational_tools(), "auto"),
            [],
        )

    def test_operational_request_requires_any_real_tool_after_file_read(self):
        messages = [
            kimi.Message(
                role="user",
                content=(
                    "Нам надо настроить прокси в Chrome. Данные VPS лежат в "
                    r"C:\Users\user\Desktop\asd.txt"
                ),
            ),
            kimi.Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_read",
                        "type": "function",
                        "function": {
                            "name": "read_file",
                            "arguments": r'{"path":"C:\\Users\\user\\Desktop\\asd.txt"}',
                        },
                    }
                ],
            ),
            kimi.Message(
                role="tool",
                name="read_file",
                tool_call_id="call_read",
                content="proxy_host=example.invalid",
            ),
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, self.operational_tools(), "auto"),
            [],
        )

    def test_execute_request_requires_any_real_tool_after_file_read(self):
        messages = [
            kimi.Message(
                role="user",
                content=(
                    "Выполни локальную проверку по инструкции из "
                    r"C:\Users\user\Desktop\probe.txt"
                ),
            ),
            kimi.Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_read",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            ),
            kimi.Message(
                role="tool",
                name="read_file",
                tool_call_id="call_read",
                content="Run a harmless verification command.",
            ),
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, self.operational_tools(), "auto"),
            [],
        )

    def test_informational_how_to_question_does_not_force_shell(self):
        messages = [
            kimi.Message(role="user", content="Как в целом настраивают прокси в Chrome?")
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, self.operational_tools(), "auto"),
            [],
        )

    def test_install_request_without_path_requires_any_real_tool(self):
        messages = [
            kimi.Message(role="user", content="Установи и настрой локальный тестовый пакет")
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, self.operational_tools(), "auto"),
            [],
        )

    def test_edit_request_allows_model_to_choose_after_read(self):
        messages = [
            kimi.Message(
                role="user",
                content=r"Измени параметр в C:\Users\user\Desktop\config.txt",
            ),
            kimi.Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_read",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            ),
            kimi.Message(
                role="tool",
                name="read_file",
                tool_call_id="call_read",
                content="enabled=false",
            ),
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, self.operational_tools(), "auto"),
            [],
        )

    def test_execute_word_is_satisfied_by_completed_write_tool(self):
        messages = [
            kimi.Message(
                role="user",
                content=(
                    r"Измени C:\Users\user\Desktop\config.txt и выполни изменение реально"
                ),
            ),
            kimi.Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_read",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            ),
            kimi.Message(role="tool", name="read_file", content="MODE=old"),
            kimi.Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_write",
                        "type": "function",
                        "function": {"name": "write_file", "arguments": "{}"},
                    }
                ],
            ),
            kimi.Message(role="tool", name="write_file", content="updated"),
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, self.operational_tools(), "auto"),
            [],
        )

    def test_unknown_action_is_not_classified_by_gateway_language_rules(self):
        messages = [
            kimi.Message(role="user", content="Сделай локальную диагностику проекта")
        ]
        required = kimi._required_next_tools(
            messages,
            self.operational_tools(),
            "auto",
        )
        self.assertEqual(required, [])
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=messages,
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        self.assertEqual(kimi._native_tool_mode(request), kimi.ALL_CLAW_TOOLS)

    def test_explanation_request_remains_text_only(self):
        messages = [
            kimi.Message(role="user", content="Объясни, как работает локальная диагностика")
        ]
        required = kimi._required_next_tools(
            messages,
            self.operational_tools(),
            "auto",
        )
        self.assertEqual(required, [])

    def test_wrong_os_desktop_path_repairs_to_unique_existing_file(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            desktop = home / "Desktop"
            desktop.mkdir()
            expected = desktop / "asd.txt"
            expected.write_text("secret", encoding="utf-8")
            request = kimi.ChatCompletionRequest(
                model="kimi-web",
                messages=[
                    kimi.Message(
                        role="user",
                        content="Используй asd.txt с рабочего стола",
                    )
                ],
                tools=self.operational_tools(),
                tool_choice="auto",
            )
            parsed = kimi.ParsedAssistantOutput(
                text="",
                tool_calls=[
                    kimi.ParsedToolCall(
                        id="call_read",
                        name="read_file",
                        arguments={"path": "/home/user/Desktop/asd.txt"},
                    )
                ],
            )
            with mock.patch.object(kimi, "RUNTIME_PLATFORM", "windows"), mock.patch.object(
                kimi,
                "RUNTIME_HOME",
                str(home),
            ):
                repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(repaired.tool_calls[0].arguments["path"], str(expected))

    def test_read_file_argument_alias_is_canonicalized_for_claw(self):
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Прочитай файл")],
            tools=self.operational_tools(),
            tool_choice="auto",
        )
        parsed = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_read",
                    name="read_file",
                    arguments={"file_path": r"C:\Users\user\Desktop\asd.txt"},
                )
            ],
        )
        repaired = kimi._repair_native_tool_calls(parsed, request)
        self.assertEqual(
            repaired.tool_calls[0].arguments,
            {"path": r"C:\Users\user\Desktop\asd.txt"},
        )

    def test_local_file_recovery_is_left_to_kimi_turn_decision(self):
        tools = self.operational_tools() + [
            {
                "type": "function",
                "function": {"name": "WebSearch", "parameters": {}},
            }
        ]
        messages = [
            kimi.Message(role="user", content="Используй asd.txt с рабочего стола"),
            kimi.Message(
                role="assistant",
                tool_calls=[
                    {
                        "id": "call_read",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                ],
            ),
            kimi.Message(
                role="tool",
                name="read_file",
                tool_call_id="call_read",
                content="Файл не найден по пути /home/user/Desktop/asd.txt (os error 3)",
            ),
            kimi.Message(role="assistant", content="Уточните путь"),
            kimi.Message(role="user", content="сам поищи и определи"),
        ]
        required = kimi._required_next_tools(messages, tools, "auto")
        self.assertEqual(required, [])
        web = kimi.ParsedAssistantOutput(
            text="",
            tool_calls=[
                kimi.ParsedToolCall(
                    id="call_web",
                    name="WebSearch",
                    arguments={"query": "сам поищи и определи"},
                )
            ],
        )
        self.assertTrue(kimi._tool_output_satisfies_requirement(web, required))

    def test_final_answer_wording_is_left_to_kimi(self):
        messages = [
            kimi.Message(
                role="user",
                content=(
                    "Настрой проверку по инструкции из "
                    r"C:\Users\user\Desktop\action.txt. "
                    "В финале напиши только EXECUTION_OK."
                ),
            )
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, self.operational_tools(), "auto"),
            [],
        )

    def test_explicit_create_file_request_is_left_to_kimi(self):
        messages = [
            kimi.Message(
                role="user",
                content=r"Создай файл C:\Users\user\Desktop\created.txt с текстом OK",
            )
        ]
        self.assertEqual(
            kimi._required_next_tools(messages, self.operational_tools(), "auto"),
            [],
        )


class SseTests(unittest.TestCase):
    def test_completion_text_is_extracted(self):
        kind, value = kimi.parse_kimi_sse_line(
            'data: {"event":"cmpl","text":"hello"}'
        )
        self.assertEqual((kind, value), ("text", "hello"))

    def test_request_echo_is_ignored(self):
        self.assertEqual(
            kimi.parse_kimi_sse_line(
                'data: {"event":"req","text":"original prompt"}'
            ),
            (None, None),
        )

    def test_all_done_is_detected(self):
        self.assertEqual(
            kimi.parse_kimi_sse_line('data: {"event":"all_done"}'),
            ("done", None),
        )


class RequestPacerTests(unittest.IsolatedAsyncioTestCase):
    async def test_requests_are_started_at_an_even_interval(self):
        now = 100.0
        sleeps = []

        def clock():
            return now

        async def sleep(delay):
            nonlocal now
            sleeps.append(delay)
            now += delay

        pacer = kimi.RequestPacer(6.0, clock=clock, sleep=sleep)
        self.assertEqual(await pacer.wait(), 0.0)
        self.assertEqual(await pacer.wait(), 6.0)
        self.assertEqual(await pacer.wait(), 6.0)
        self.assertEqual(sleeps, [6.0, 6.0])


class FakeConversationKimi:
    def __init__(self):
        self.created = 0

    async def create_chat(self):
        self.created += 1
        return f"chat_{self.created}"


class PersistentConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = kimi.SessionStore(Path(self.temp_dir.name) / "session.json")
        self.store.save(
            {"access_token": "access", "refresh_token": "refresh", "headers": {"x": "y"}}
        )
        self.tracker = kimi.PersistentConversation(self.store)
        self.original_kimi = kimi.kimi
        self.original_upstream_model = kimi.KIMI_UPSTREAM_MODEL
        self.fake_kimi = FakeConversationKimi()
        kimi.kimi = self.fake_kimi
        kimi.KIMI_UPSTREAM_MODEL = "k2d6-chat"

    async def asyncTearDown(self):
        kimi.kimi = self.original_kimi
        kimi.KIMI_UPSTREAM_MODEL = self.original_upstream_model
        self.temp_dir.cleanup()

    async def test_continuation_reuses_chat_and_sends_only_new_user_turn(self):
        first = [
            kimi.Message(role="system", content="system"),
            kimi.Message(role="user", content="first"),
        ]
        chat_id, delta, fingerprints, tokens, upstream_messages, upstream_tokens = (
            await self.tracker.prepare("session-a", first)
        )
        self.assertEqual(chat_id, "chat_1")
        self.assertEqual(delta, first)
        await self.tracker.commit(
            "session-a",
            chat_id,
            fingerprints,
            tokens,
            upstream_messages,
            upstream_tokens,
        )

        continued = first + [
            kimi.Message(role="assistant", content="answer"),
            kimi.Message(role="user", content="second"),
        ]
        next_chat_id, next_delta, _, _, _, _ = await self.tracker.prepare(
            "session-a", continued
        )
        self.assertEqual(next_chat_id, chat_id)
        self.assertEqual([(item.role, item.content) for item in next_delta], [("user", "second")])
        self.assertEqual(self.fake_kimi.created, 1)

    async def test_more_than_one_hundred_messages_do_not_rotate_below_context_limit(self):
        first = [
            kimi.Message(role="user", content=f"message-{index}")
            for index in range(99)
        ]
        chat_id, _, fingerprints, tokens, upstream_messages, upstream_tokens = await self.tracker.prepare(
            "session-long", first
        )
        await self.tracker.commit(
            "session-long",
            chat_id,
            fingerprints,
            tokens,
            upstream_messages,
            upstream_tokens,
        )

        continued = first + [
            kimi.Message(role="assistant", content="answer"),
            kimi.Message(role="user", content="message-100"),
        ]
        next_chat_id, next_delta, _, _, _, _ = await self.tracker.prepare(
            "session-long", continued
        )

        self.assertEqual(next_chat_id, chat_id)
        self.assertEqual(
            [(item.role, item.content) for item in next_delta],
            [("user", "message-100")],
        )
        self.assertEqual(self.fake_kimi.created, 1)

    async def test_claw_compaction_reuses_chat_from_preserved_tail_overlap(self):
        previous = [
            kimi.Message(role="system", content="system"),
            kimi.Message(role="user", content="first"),
            kimi.Message(role="assistant", content="first answer"),
            kimi.Message(role="user", content="second"),
            kimi.Message(role="assistant", content="second answer"),
        ]
        chat_id, _, fingerprints, tokens, upstream_messages, upstream_tokens = await self.tracker.prepare(
            "session-compact", previous
        )
        await self.tracker.commit(
            "session-compact",
            chat_id,
            fingerprints,
            tokens,
            upstream_messages,
            upstream_tokens,
        )

        compacted = [
            kimi.Message(
                # OpenAI compatibility translates a compacted session system
                # message into a user message after the runtime system prompt.
                role="user",
                content=(
                    "This session is being continued from a previous conversation "
                    "that ran out of context. The summary below covers the earlier "
                    "portion of the conversation."
                ),
            ),
            *previous[-2:],
            kimi.Message(role="user", content="after compaction"),
        ]
        next_chat_id, next_delta, _, _, _, _ = await self.tracker.prepare(
            "session-compact", compacted
        )

        self.assertEqual(next_chat_id, chat_id)
        self.assertEqual(
            [(item.role, item.content) for item in next_delta],
            [("user", "after compaction")],
        )
        self.assertEqual(self.fake_kimi.created, 1)

    async def test_claw_compaction_after_runtime_system_prompt_reuses_chat(self):
        runtime_system = kimi.Message(role="system", content="runtime instructions")
        previous = [
            runtime_system,
            kimi.Message(role="user", content="first"),
            kimi.Message(role="assistant", content="first answer"),
            kimi.Message(role="user", content="second"),
            kimi.Message(role="assistant", content="second answer"),
        ]
        chat_id, _, fingerprints, tokens, upstream_messages, upstream_tokens = await self.tracker.prepare(
            "session-prefixed-compact", previous
        )
        await self.tracker.commit(
            "session-prefixed-compact",
            chat_id,
            fingerprints,
            tokens,
            upstream_messages,
            upstream_tokens,
        )

        compacted = [
            kimi.Message(role="system", content="updated runtime instructions"),
            kimi.Message(
                role="user",
                content=(
                    "This session is being continued from a previous conversation "
                    "that ran out of context. The summary below covers the earlier "
                    "portion of the conversation."
                ),
            ),
            *previous[-2:],
            kimi.Message(role="user", content="after compaction"),
        ]
        next_chat_id, next_delta, _, _, _, _ = await self.tracker.prepare(
            "session-prefixed-compact", compacted
        )

        self.assertEqual(next_chat_id, chat_id)
        self.assertEqual(
            [(item.role, item.content) for item in next_delta],
            [("user", "after compaction")],
        )
        self.assertEqual(self.fake_kimi.created, 1)

    async def test_message_limit_does_not_reset_after_claw_compaction(self):
        previous = [
            kimi.Message(role="system", content="system"),
            kimi.Message(role="user", content="first"),
            kimi.Message(role="assistant", content="answer"),
        ]
        chat_id, _, fingerprints, tokens, upstream_messages, upstream_tokens = await self.tracker.prepare(
            "session-message-limit", previous
        )
        await self.tracker.commit(
            "session-message-limit",
            chat_id,
            fingerprints,
            tokens,
            upstream_messages,
            upstream_tokens,
        )

        session = self.store.load()
        state = session["persistent_conversations"]["session-message-limit"]
        state["upstream_message_count"] = kimi.MAX_PERSISTENT_MESSAGES
        self.store.save(session)

        compacted = [
            kimi.Message(
                role="user",
                content=(
                    "This session is being continued from a previous conversation "
                    "that ran out of context. The summary below covers the earlier "
                    "portion of the conversation."
                ),
            ),
            *previous[-2:],
            kimi.Message(role="user", content="after compaction"),
        ]
        rotated_chat, rotated_delta, _, _, _, _ = await self.tracker.prepare(
            "session-message-limit", compacted
        )

        self.assertNotEqual(rotated_chat, chat_id)
        self.assertEqual(rotated_delta, compacted)

    async def test_context_limit_does_not_reset_after_claw_compaction(self):
        previous = [
            kimi.Message(role="system", content="system"),
            kimi.Message(role="user", content="first"),
            kimi.Message(role="assistant", content="answer"),
        ]
        (
            chat_id,
            _,
            fingerprints,
            tokens,
            upstream_messages,
            upstream_tokens,
        ) = await self.tracker.prepare("session-token-limit", previous)
        await self.tracker.commit(
            "session-token-limit",
            chat_id,
            fingerprints,
            tokens,
            upstream_messages,
            upstream_tokens,
        )

        session = self.store.load()
        state = session["persistent_conversations"]["session-token-limit"]
        state["upstream_context_tokens_estimated"] = (
            kimi.MAX_PERSISTENT_CONTEXT_TOKENS
        )
        self.store.save(session)

        compacted = [
            kimi.Message(
                role="user",
                content=(
                    "This session is being continued from a previous conversation "
                    "that ran out of context. The summary below covers the earlier "
                    "portion of the conversation."
                ),
            ),
            *previous[-2:],
            kimi.Message(role="user", content="after compaction"),
        ]
        rotated_chat, rotated_delta, _, _, _, _ = await self.tracker.prepare(
            "session-token-limit", compacted
        )

        self.assertNotEqual(rotated_chat, chat_id)
        self.assertEqual(rotated_delta, compacted)

    def test_only_missing_upstream_chat_invalidates_persistent_state(self):
        self.assertFalse(kimi._persistent_chat_is_gone(RuntimeError("empty completion")))
        request = httpx.Request("POST", "https://www.kimi.com/chat")
        gone = httpx.HTTPStatusError(
            "gone",
            request=request,
            response=httpx.Response(410, request=request),
        )
        self.assertTrue(kimi._persistent_chat_is_gone(gone))

    async def test_new_claw_session_gets_a_new_kimi_chat(self):
        messages = [kimi.Message(role="user", content="hello")]
        (
            first_chat,
            _,
            first_fingerprints,
            first_tokens,
            upstream_messages,
            upstream_tokens,
        ) = await self.tracker.prepare("session-a", messages)
        await self.tracker.commit(
            "session-a",
            first_chat,
            first_fingerprints,
            first_tokens,
            upstream_messages,
            upstream_tokens,
        )
        second_chat, _, _, _, _, _ = await self.tracker.prepare("session-b", messages)
        self.assertNotEqual(first_chat, second_chat)

    async def test_context_limit_rotates_to_a_new_chat(self):
        original_limit = kimi.MAX_PERSISTENT_CONTEXT_TOKENS
        kimi.MAX_PERSISTENT_CONTEXT_TOKENS = 10
        try:
            first = [kimi.Message(role="user", content="hello")]
            chat_id, _, fingerprints, tokens, upstream_messages, upstream_tokens = await self.tracker.prepare(
                "session-a", first
            )
            await self.tracker.commit(
                "session-a",
                chat_id,
                fingerprints,
                tokens,
                upstream_messages,
                upstream_tokens,
            )
            continued = first + [
                kimi.Message(role="assistant", content="answer"),
                kimi.Message(role="user", content="x" * 100),
            ]
            rotated_chat, rotated_delta, _, _, _, _ = await self.tracker.prepare(
                "session-a", continued
            )
        finally:
            kimi.MAX_PERSISTENT_CONTEXT_TOKENS = original_limit
        self.assertNotEqual(rotated_chat, chat_id)
        self.assertEqual(rotated_delta, continued)

    async def test_k3_starts_with_a_provisional_empty_chat_id(self):
        kimi.KIMI_UPSTREAM_MODEL = "k3-agent"
        chat_id, delta, _, _, _, _ = await self.tracker.prepare(
            "session-k3",
            [kimi.Message(role="user", content="hello")],
        )
        self.assertEqual(chat_id, "")
        self.assertEqual(delta[0].content, "hello")
        self.assertEqual(self.fake_kimi.created, 0)


class ToolCallParsingTests(unittest.TestCase):
    def test_native_numeric_parameter_preserves_schema_type(self):
        self.assertEqual(kimi._parse_native_parameter("120000"), 120000)
        self.assertEqual(kimi._parse_native_parameter("1.5"), 1.5)
        self.assertEqual(kimi._parse_native_parameter("-3"), -3)

    def test_parses_kimi_tool_marker(self):
        raw = (
            "<|tool_calls_section_begin|>"
            "<|tool_call_begin|>functions.Bash:0"
            "<|tool_call_argument_begin|>"
            '{"command":"echo READY","description":"probe"}'
            "<|tool_call_end|>"
            "<|tool_calls_section_end|>"
        )
        parsed = kimi.parse_assistant_output(raw)
        self.assertEqual(parsed.text, "")
        self.assertEqual(parsed.tool_calls[0].name, "Bash")
        self.assertEqual(parsed.tool_calls[0].arguments["command"], "echo READY")

    def test_tolerates_smart_quotes_and_windows_backslashes(self):
        raw = (
            "<|tool_call_begin|>functions.Bash:0"
            "<|tool_call_argument_begin|>"
            "{“command”: “powershell Set-Content ‘C:\\claw cod\\probe.txt’”, “description”: “probe”}"
            "<|tool_call_end|>"
        )
        parsed = kimi.parse_assistant_output(raw)
        self.assertEqual(parsed.tool_calls[0].name, "Bash")
        self.assertIn(r"C:\claw cod\probe.txt", parsed.tool_calls[0].arguments["command"])

    def test_parses_openai_style_marker_and_discards_simulated_result(self):
        raw = (
            "Inspecting the collector now.\n"
            "[ASSISTANT_TOOL_CALLS]\n"
            '[{"function":{"arguments":"{\\"path\\":\\"C:\\\\claw cod\\\\collector.py\\"}",'
            '"name":"read_file"},"id":"call_model","type":"function"}]\n'
            "[TOOL_RESULT id=call_model]\n"
            '{"content":"fabricated and must never reach the user"}'
        )
        parsed = kimi.parse_assistant_output(raw)
        self.assertEqual(parsed.text, "Inspecting the collector now.")
        self.assertEqual(len(parsed.tool_calls), 1)
        self.assertEqual(parsed.tool_calls[0].id, "call_model")
        self.assertEqual(parsed.tool_calls[0].name, "read_file")
        self.assertEqual(parsed.tool_calls[0].arguments["path"], r"C:\claw cod\collector.py")

    def test_repairs_missing_outer_braces_in_openai_style_calls(self):
        raw = (
            "[ASSISTANT_TOOL_CALLS]\n"
            '[{"function":{"arguments":"{\\"path\\":\\"a.py\\"}",'
            '"name":"read_file","id":"call_a","type":"function"},'
            '{"function":{"arguments":"{\\"path\\":\\"b.py\\"}",'
            '"name":"read_file","id":"call_b","type":"function"}]'
        )
        parsed = kimi.parse_assistant_output(raw)
        self.assertEqual([call.id for call in parsed.tool_calls], ["call_a", "call_b"])
        self.assertEqual(
            [call.arguments["path"] for call in parsed.tool_calls],
            ["a.py", "b.py"],
        )

    def test_parses_native_function_call_and_discards_fabricated_tail(self):
        raw = (
            "I will inspect it.\n"
            '<function_calls><invoke name="read_file">'
            '<parameter name="path">C:\\claw cod\\project\\README.md</parameter>'
            "</invoke></function_calls>\n"
            "The file contains fabricated content."
        )
        parsed = kimi.parse_assistant_output(raw)
        self.assertEqual(parsed.text, "I will inspect it.")
        self.assertEqual(parsed.tool_calls[0].name, "read_file")
        self.assertEqual(
            parsed.tool_calls[0].arguments["path"], r"C:\claw cod\project\README.md"
        )

    def test_native_function_call_decodes_xml_entities_in_command(self):
        raw = (
            '<function_calls><invoke name="PowerShell">'
            '<parameter name="command">'
            'cmd /c "echo test &gt; out.txt &amp;&amp; type out.txt"'
            "</parameter></invoke></function_calls>"
        )
        parsed = kimi.parse_assistant_output(raw)
        self.assertEqual(
            parsed.tool_calls[0].arguments["command"],
            'cmd /c "echo test > out.txt && type out.txt"',
        )

    def test_parses_native_bash_block(self):
        parsed = kimi.parse_assistant_output(
            "<antThinking>run it</antThinking><bash>echo NATIVE_OK</bash>fake result"
        )
        self.assertEqual(parsed.text, "")
        self.assertEqual(parsed.tool_calls[0].name, "bash")
        self.assertEqual(parsed.tool_calls[0].arguments, {"command": "echo NATIVE_OK"})

    def test_preserves_text_when_no_tool_call_exists(self):
        parsed = kimi.parse_assistant_output("ordinary response")
        self.assertEqual(parsed.text, "ordinary response")
        self.assertEqual(parsed.tool_calls, [])

    def test_empty_output_never_satisfies_tool_request(self):
        parsed = kimi.ParsedAssistantOutput(text="", tool_calls=[])
        self.assertFalse(kimi._tool_output_satisfies_requirement(parsed, []))

    def test_text_semantics_are_not_classified_by_structural_validator(self):
        parsed = kimi.ParsedAssistantOutput(
            text="Давай посмотрю оставшиеся ключевые файлы параллельно.",
            tool_calls=[],
        )
        self.assertTrue(kimi._tool_output_satisfies_requirement(parsed, []))
        completed = kimi.ParsedAssistantOutput(
            text="Проверил четыре файла. Коллектор требует доработки.",
            tool_calls=[],
        )
        self.assertTrue(kimi._tool_output_satisfies_requirement(completed, []))

    def test_no_access_refusal_is_left_to_kimi_turn_decision(self):
        parsed = kimi.ParsedAssistantOutput(
            text=(
                "Я не имею прямого доступа к вашему рабочему столу. "
                "Пожалуйста, откройте файл и скопируйте сюда его содержимое."
            ),
            tool_calls=[],
        )
        self.assertTrue(kimi._tool_output_satisfies_requirement(parsed, []))

    def test_planning_text_is_left_to_kimi_turn_decision(self):
        loop = ("Let me read the key files. I'll read them one at a time. " * 30).strip()
        parsed = kimi.ParsedAssistantOutput(text=loop, tool_calls=[])
        self.assertTrue(kimi._tool_output_satisfies_requirement(parsed, []))

    def test_retryable_upstream_statuses_are_classified(self):
        for status in (408, 429, 500, 502, 503, 504):
            self.assertTrue(kimi._is_retryable_status(status))
        for status in (200, 400, 401, 403, 422):
            self.assertFalse(kimi._is_retryable_status(status))

    def test_chat_session_in_progress_is_a_retryable_connect_error(self):
        self.assertTrue(
            kimi._is_retryable_connect_error(
                "resource_exhausted",
                "Chat session in progress. Please try again later.",
            )
        )
        self.assertFalse(
            kimi._is_retryable_connect_error("resource_exhausted", "quota exhausted")
        )


class FakeKimiClient:
    async def create_chat(self):
        return "chat_test"

    async def iter_completion(self, chat_id, prompt, **kwargs):
        del kwargs
        del chat_id, prompt
        yield (
            "<|tool_calls_section_begin|>"
            "<|tool_call_begin|>functions.Read:0"
            "<|tool_call_argument_begin|>"
            '{"file_path":"probe.txt"}'
            "<|tool_call_end|>"
            "<|tool_calls_section_end|>"
        )


class OpenAiToolResponseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.original = kimi.kimi
        self.original_pacer = kimi.request_pacer
        kimi.kimi = FakeKimiClient()
        kimi.request_pacer = kimi.RequestPacer(0)

    async def asyncTearDown(self):
        kimi.kimi = self.original
        kimi.request_pacer = self.original_pacer

    @staticmethod
    def request(stream):
        return kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="read probe")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "Read",
                        "description": "Read a file",
                        "parameters": {"type": "object"},
                    },
                }
            ],
            tool_choice="auto",
            stream=stream,
        )

    async def test_non_streaming_response_contains_openai_tool_call(self):
        response = await kimi.chat_completions(self.request(stream=False))
        choice = response["choices"][0]
        call = choice["message"]["tool_calls"][0]
        self.assertEqual(choice["finish_reason"], "tool_calls")
        self.assertEqual(call["function"]["name"], "Read")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"file_path": "probe.txt"})
        self.assertGreater(response["usage"]["prompt_tokens"], 0)
        self.assertGreater(response["usage"]["completion_tokens"], 0)

    async def test_streaming_response_contains_openai_tool_delta(self):
        response = await kimi.chat_completions(self.request(stream=True))
        body = "".join([chunk async for chunk in response.body_iterator])
        payloads = [
            json.loads(line[6:])
            for line in body.splitlines()
            if line.startswith("data: {")
        ]
        deltas = [
            item["choices"][0]
            for item in payloads
            if item.get("choices")
        ]
        tool_delta = next(
            item for item in deltas if item["delta"].get("tool_calls")
        )
        self.assertEqual(
            tool_delta["delta"]["tool_calls"][0]["function"]["name"],
            "Read",
        )
        self.assertTrue(any(item["finish_reason"] == "tool_calls" for item in deltas))
        usage = next(item["usage"] for item in payloads if item.get("usage"))
        self.assertGreater(usage["prompt_tokens"], 0)
        self.assertGreater(usage["completion_tokens"], 0)


class RetryingFakeKimiClient:
    def __init__(self):
        self.created = 0
        self.calls = 0
        self.chat_ids = []

    async def create_chat(self):
        self.created += 1
        return f"retry_{self.created}"

    async def iter_completion(self, chat_id, prompt, **kwargs):
        del kwargs
        del prompt
        self.calls += 1
        self.chat_ids.append(chat_id)
        if self.calls == 1:
            yield "I will run Bash now."
        else:
            yield (
                "<|tool_calls_section_begin|>"
                "<|tool_call_begin|>functions.Bash:0"
                "<|tool_call_argument_begin|>"
                '{"command":"echo READY"}'
                "<|tool_call_end|>"
                "<|tool_calls_section_end|>"
            )


class EmptyThenToolFakeKimiClient(RetryingFakeKimiClient):
    def __init__(self):
        super().__init__()
        self.prompts = []

    async def iter_completion(self, chat_id, prompt, **kwargs):
        del kwargs
        self.calls += 1
        self.chat_ids.append(chat_id)
        self.prompts.append(prompt)
        if self.calls == 1:
            return
        yield (
            "<|tool_calls_section_begin|>"
            "<|tool_call_begin|>functions.Bash:0"
            "<|tool_call_argument_begin|>"
            '{"command":"echo READY"}'
            "<|tool_call_end|>"
            "<|tool_calls_section_end|>"
        )


class RepeatedTextThenFreshChatToolFakeKimiClient(RetryingFakeKimiClient):
    def __init__(self):
        super().__init__()
        self.prompts = []

    async def iter_completion(self, chat_id, prompt, **kwargs):
        del kwargs
        self.calls += 1
        self.chat_ids.append(chat_id)
        self.prompts.append(prompt)
        if chat_id == "broken-chat":
            yield "I will use the requested tool next."
            return
        yield (
            "<|tool_calls_section_begin|>"
            "<|tool_call_begin|>functions.Bash:0"
            "<|tool_call_argument_begin|>"
            '{"command":"echo READY"}'
            "<|tool_call_end|>"
            "<|tool_calls_section_end|>"
        )


class UnknownToolRetryingFakeKimiClient(RetryingFakeKimiClient):
    async def iter_completion(self, chat_id, prompt, **kwargs):
        del kwargs
        del prompt
        self.calls += 1
        self.chat_ids.append(chat_id)
        if self.calls == 1:
            yield (
                '<tool>SSH</tool><parameter>{"host":"example.invalid",'
                '"username":"demo","password":"not-a-secret"}</parameter>'
            )
        else:
            yield (
                "<|tool_calls_section_begin|>"
                "<|tool_call_begin|>functions.Bash:0"
                "<|tool_call_argument_begin|>"
                '{"command":"echo READY"}'
                "<|tool_call_end|>"
                "<|tool_calls_section_end|>"
            )


class InvalidArgumentsRetryingFakeKimiClient(RetryingFakeKimiClient):
    async def iter_completion(self, chat_id, prompt, **kwargs):
        del kwargs
        del prompt
        self.calls += 1
        self.chat_ids.append(chat_id)
        if self.calls == 1:
            yield (
                '[ASSISTANT_TOOL_CALLS]\n[{"function":{"name":"Bash",'
                '"arguments":"{not valid json"}}]'
            )
        else:
            yield (
                "<|tool_calls_section_begin|>"
                "<|tool_call_begin|>functions.Bash:0"
                "<|tool_call_argument_begin|>"
                '{"command":"echo READY"}'
                "<|tool_call_end|>"
                "<|tool_calls_section_end|>"
            )


class TextOnlyFakeKimiClient:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    async def create_chat(self):
        return "chat_text"

    async def iter_completion(self, chat_id, prompt, **kwargs):
        del chat_id, prompt, kwargs
        self.calls += 1
        yield self.text


class DrainingToolStreamFakeKimiClient:
    def __init__(self):
        self.drained = False

    async def iter_completion(self, chat_id, prompt, **kwargs):
        del chat_id, prompt, kwargs
        yield "<bash>echo READY</bash>"
        yield "fabricated trailing text"
        self.drained = True


class ToolRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_collection_drains_upstream_after_tool_marker(self):
        original = kimi.kimi
        fake = DrainingToolStreamFakeKimiClient()
        kimi.kimi = fake
        try:
            output = await kimi._collect_completion(
                "chat",
                "prompt",
                stop_after_tool_section=True,
            )
        finally:
            kimi.kimi = original
        self.assertTrue(fake.drained)
        self.assertEqual(output, "<bash>echo READY</bash>")

    async def test_auto_tool_choice_returns_kimi_text_without_secondary_decision(self):
        original = kimi.kimi
        fake = TextOnlyFakeKimiClient("Kimi chose to answer directly.")
        kimi.kimi = fake
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="arbitrary wording")],
            tools=[{"type": "function", "function": {"name": "PowerShell"}}],
            tool_choice="auto",
        )
        try:
            parsed = await kimi._collect_tool_output_with_retry(
                request,
                kimi.messages_to_prompt(request.messages, tools=request.tools),
                "initial",
            )
        finally:
            kimi.kimi = original
        self.assertEqual(fake.calls, 1)
        self.assertEqual(parsed.text, "Kimi chose to answer directly.")
        self.assertEqual(parsed.tool_calls, [])

    async def test_empty_stream_rotates_to_fresh_chat_and_replays_full_prompt(self):
        original = kimi.kimi
        original_model = kimi.KIMI_UPSTREAM_MODEL
        fake = EmptyThenToolFakeKimiClient()
        kimi.kimi = fake
        kimi.KIMI_UPSTREAM_MODEL = "k2d6-chat"
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Use Bash to echo READY")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "Bash",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            tool_choice="required",
            stream=True,
        )
        prompt = kimi.messages_to_prompt(
            request.messages,
            tools=request.tools,
            tool_choice=request.tool_choice,
        )
        try:
            parsed = await kimi._collect_tool_output_with_retry(
                request,
                prompt,
                "broken-chat",
            )
        finally:
            kimi.kimi = original
            kimi.KIMI_UPSTREAM_MODEL = original_model
        self.assertEqual(fake.created, 1)
        self.assertEqual(fake.chat_ids, ["broken-chat", "retry_1"])
        self.assertEqual(fake.prompts[0], fake.prompts[1])
        self.assertEqual(parsed.tool_calls[0].name, "Bash")

    async def test_repeated_required_tool_text_rotates_and_replays_full_prompt(self):
        original = kimi.kimi
        original_model = kimi.KIMI_UPSTREAM_MODEL
        fake = RepeatedTextThenFreshChatToolFakeKimiClient()
        kimi.kimi = fake
        kimi.KIMI_UPSTREAM_MODEL = "k2d6-chat"
        request = kimi.ChatCompletionRequest(
            model="kimi-web",
            messages=[kimi.Message(role="user", content="Use Bash to echo READY")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "Bash",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
            tool_choice="required",
            stream=True,
        )
        prompt = kimi.messages_to_prompt(
            request.messages,
            tools=request.tools,
            tool_choice=request.tool_choice,
        )
        try:
            parsed = await kimi._collect_tool_output_with_retry(
                request,
                prompt,
                "broken-chat",
            )
        finally:
            kimi.kimi = original
            kimi.KIMI_UPSTREAM_MODEL = original_model
        self.assertEqual(fake.created, 1)
        self.assertEqual(fake.chat_ids, ["broken-chat", "broken-chat", "retry_1"])
        self.assertEqual(
            fake.prompts[2],
            kimi._conversation_prompt(
                request.messages,
                tool_result_limit=8_000,
                older_tool_result_limit=800,
            ),
        )
        self.assertEqual(parsed.tool_calls[0].name, "Bash")

    async def test_unknown_generated_tool_is_corrected_in_the_same_chat(self):
        original = kimi.kimi
        original_model = kimi.KIMI_UPSTREAM_MODEL
        fake = UnknownToolRetryingFakeKimiClient()
        kimi.kimi = fake
        kimi.KIMI_UPSTREAM_MODEL = "k2d6-chat"
        try:
            request = kimi.ChatCompletionRequest(
                model="kimi-web",
                messages=[kimi.Message(role="user", content="Use Bash")],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "Bash",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                tool_choice="auto",
                stream=True,
            )
            parsed = await kimi._collect_tool_output_with_retry(
                request,
                kimi.messages_to_prompt(
                    request.messages,
                    tools=request.tools,
                    tool_choice=request.tool_choice,
                ),
                "initial",
            )
        finally:
            kimi.kimi = original
            kimi.KIMI_UPSTREAM_MODEL = original_model
        self.assertEqual(fake.created, 0)
        self.assertEqual(fake.chat_ids, ["initial", "initial"])
        self.assertEqual(parsed.tool_calls[0].name, "Bash")

    async def test_invalid_tool_arguments_are_corrected_in_the_same_chat(self):
        original = kimi.kimi
        original_model = kimi.KIMI_UPSTREAM_MODEL
        fake = InvalidArgumentsRetryingFakeKimiClient()
        kimi.kimi = fake
        kimi.KIMI_UPSTREAM_MODEL = "k2d6-chat"
        try:
            request = kimi.ChatCompletionRequest(
                model="kimi-web",
                messages=[kimi.Message(role="user", content="Use Bash")],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "Bash",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                tool_choice="required",
                stream=True,
            )
            parsed = await kimi._collect_tool_output_with_retry(
                request,
                kimi.messages_to_prompt(
                    request.messages,
                    tools=request.tools,
                    tool_choice=request.tool_choice,
                ),
                "initial",
            )
        finally:
            kimi.kimi = original
            kimi.KIMI_UPSTREAM_MODEL = original_model
        self.assertEqual(fake.chat_ids, ["initial", "initial"])
        self.assertEqual(parsed.tool_calls[0].name, "Bash")


class FailingKimiClient:
    async def create_chat(self):
        return "chat_failure"

    async def iter_completion(self, chat_id, prompt, **kwargs):
        del kwargs
        del chat_id, prompt
        raise RuntimeError("upstream failed before tool output")
        yield  # pragma: no cover


class ToolPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_tool_failure_is_reported_after_immediate_stream_start(self):
        original = kimi.kimi
        original_pacer = kimi.request_pacer
        kimi.kimi = FailingKimiClient()
        kimi.request_pacer = kimi.RequestPacer(0)
        try:
            request = kimi.ChatCompletionRequest(
                model="kimi-web",
                messages=[kimi.Message(role="user", content="Use Bash")],
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "Bash",
                            "parameters": {"type": "object", "properties": {}},
                        },
                    }
                ],
                stream=True,
            )
            response = await kimi.chat_completions(request)
            body = "".join([chunk async for chunk in response.body_iterator])
        finally:
            kimi.kimi = original
            kimi.request_pacer = original_pacer
        self.assertIn('"role":"assistant"', body)
        self.assertIn('"type":"upstream_error"', body)
        self.assertTrue(body.rstrip().endswith("data: [DONE]"))


class PayloadTests(unittest.TestCase):
    def test_payload_matches_verified_web_contract(self):
        payload = kimi.build_kimi_payload("probe", "k2")
        self.assertEqual(payload["model"], "k2")
        self.assertEqual(payload["messages"], [{"role": "user", "content": "probe"}])
        self.assertFalse(payload["use_search"])
        self.assertEqual(payload["kimiplus_id"], "kimi")

    def test_connect_payload_matches_live_k3_contract(self):
        original = (
            kimi.KIMI_UPSTREAM_MODEL,
            kimi.KIMI_SCENARIO,
            kimi.KIMI_KIMIPLUS_ID,
            kimi.KIMI_REASONING_EFFORT,
            kimi.KIMI_ENABLE_PLUGIN,
        )
        try:
            kimi.KIMI_UPSTREAM_MODEL = "k3-agent"
            kimi.KIMI_SCENARIO = "SCENARIO_OK_COMPUTER"
            kimi.KIMI_KIMIPLUS_ID = "ok-computer"
            kimi.KIMI_REASONING_EFFORT = "REASONING_EFFORT_HIGH"
            kimi.KIMI_ENABLE_PLUGIN = True
            payload = kimi.build_kimi_connect_payload("", "probe")
        finally:
            (
                kimi.KIMI_UPSTREAM_MODEL,
                kimi.KIMI_SCENARIO,
                kimi.KIMI_KIMIPLUS_ID,
                kimi.KIMI_REASONING_EFFORT,
                kimi.KIMI_ENABLE_PLUGIN,
            ) = original
        self.assertEqual(payload["chatId"], "")
        self.assertEqual(payload["scenario"], "SCENARIO_OK_COMPUTER")
        self.assertEqual(payload["kimiplusId"], "ok-computer")
        self.assertEqual(payload["message"]["role"], "user")
        self.assertEqual(payload["message"]["blocks"][0]["text"]["content"], "probe")
        self.assertEqual(payload["options"]["model"], "k3-agent")
        self.assertEqual(payload["options"]["reasoningEffort"], "REASONING_EFFORT_HIGH")
        self.assertEqual(payload["options"]["contextLength"], "CONTEXT_LENGTH_L")
        self.assertTrue(payload["options"]["enablePlugin"])
        self.assertEqual(payload["tools"], [{"type": "TOOL_TYPE_ASK_USER", "name": ""}])

    def test_connect_payload_supports_native_device_tool(self):
        payload = kimi.build_kimi_connect_payload(
            "chat-xid",
            "probe",
            system_prompt="system contract",
            native_tool_names=["read_file"],
        )
        self.assertEqual(
            payload["tools"],
            [{"type": "TOOL_TYPE_DEVICE_TOOL", "name": "read_file"}],
        )
        self.assertEqual(payload["options"]["systemPrompt"], "system contract")

    def test_connect_payload_keeps_k2_6_compatibility(self):
        original = (
            kimi.KIMI_UPSTREAM_MODEL,
            kimi.KIMI_SCENARIO,
            kimi.KIMI_KIMIPLUS_ID,
            kimi.KIMI_REASONING_EFFORT,
            kimi.KIMI_ENABLE_PLUGIN,
        )
        try:
            kimi.KIMI_UPSTREAM_MODEL = "k2d6-chat"
            kimi.KIMI_SCENARIO = "SCENARIO_CHAT"
            kimi.KIMI_KIMIPLUS_ID = ""
            kimi.KIMI_REASONING_EFFORT = "REASONING_EFFORT_NONE"
            kimi.KIMI_ENABLE_PLUGIN = False
            payload = kimi.build_kimi_connect_payload("chat-xid", "probe")
        finally:
            (
                kimi.KIMI_UPSTREAM_MODEL,
                kimi.KIMI_SCENARIO,
                kimi.KIMI_KIMIPLUS_ID,
                kimi.KIMI_REASONING_EFFORT,
                kimi.KIMI_ENABLE_PLUGIN,
            ) = original
        self.assertEqual(payload["scenario"], "SCENARIO_CHAT")
        self.assertEqual(payload["tools"], [])
        self.assertEqual(payload["options"]["model"], "k2d6-chat")

    def test_connect_json_envelope_round_trip(self):
        envelope = kimi._encode_connect_json({"hello": "world"})
        frames, remainder = kimi._decode_connect_frames(bytearray(envelope))
        self.assertEqual(remainder, bytearray())
        self.assertEqual(frames, [(0, {"hello": "world"})])

    def test_connect_text_parser_ignores_user_echo(self):
        state = kimi.ConnectStreamState()
        self.assertEqual(
            kimi._connect_event_text(
                {"message": {"role": "user", "blocks": [{"text": {"content": "prompt"}}]}},
                state,
            ),
            ([], False),
        )
        kimi._connect_event_text(
            {"message": {"id": "assistant-1", "role": "assistant"}},
            state,
        )
        kimi._connect_event_text({"chat": {"id": "chat-created-by-k3"}}, state)
        text, done = kimi._connect_event_text(
            {"block": {"text": {"content": "answer"}}},
            state,
        )
        self.assertEqual(text, ["answer"])
        self.assertEqual(state.chat_id, "chat-created-by-k3")
        self.assertFalse(done)
        self.assertEqual(kimi._connect_event_text({"done": {}}, state), ([], True))


class SessionStoreTests(unittest.TestCase):
    def test_atomic_save_keeps_secret_permissions(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session.json"
            store = kimi.SessionStore(path)
            store.save({"access_token": "a", "refresh_token": "r", "headers": {}})
            self.assertEqual(json.loads(path.read_text())["access_token"], "a")
            if os.name != "nt":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            else:
                # Windows does not expose NTFS ACLs through POSIX mode bits;
                # LocalAppData inherits the current user's protected ACL.
                self.assertTrue(path.is_file())

    def test_jwt_expiry_decode(self):
        payload = base64.urlsafe_b64encode(json.dumps({"exp": 12345}).encode()).decode().rstrip("=")
        self.assertEqual(kimi.jwt_expiry(f"x.{payload}.y"), 12345)
        self.assertEqual(kimi.jwt_expiry("invalid"), 0)

    def test_dpapi_file_is_rejected_off_windows(self):
        if os.name == "nt":
            self.skipTest("validated by the Windows smoke test")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session.dpapi"
            path.write_bytes(b"not-dpapi")
            with self.assertRaisesRegex(RuntimeError, "only on Windows"):
                kimi.SessionStore(path).load()


if __name__ == "__main__":
    unittest.main()
