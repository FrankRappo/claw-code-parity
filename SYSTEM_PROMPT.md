# Configurable OpenAI-compatible system prompt

Claw's OpenAI-compatible providers can prepend an operator-controlled system prompt to every `/v1/chat/completions` request.

This is intended for local OpenAI-compatible runtimes such as llama.cpp/Gemma where a deployment-specific persona or model instruction must be sent as a real `role=system` message, not only as project context in `CLAUDE.md`.

## Environment variables

`CLAW_SYSTEM_PROMPT_FILE` is preferred:

```bash
export CLAW_SYSTEM_PROMPT_FILE=/home/ubuntu/second-llm/prompts/system-prompt-gemma4-abliterated-current.txt
```

Or pass the prompt directly:

```bash
export CLAW_SYSTEM_PROMPT='You are the local deployment assistant. Answer directly.'
```

If both are set, `CLAW_SYSTEM_PROMPT_FILE` wins.

## Request shape

When configured, the provider sends the injected prompt as the first system message. If Claw already generated its own system prompt, both are combined into one OpenAI-compatible system message in this order:

```text
<CLAW_SYSTEM_PROMPT_FILE or CLAW_SYSTEM_PROMPT>

<Claw generated system prompt>
```

Then the normal conversation messages follow.

## Changing the prompt later

When `CLAW_SYSTEM_PROMPT_FILE` is used, edit the prompt file and start a new Claw request. The file is read when the OpenAI-compatible request is built, so changing the text does not require rebuilding the binary.

Example VM121 wrapper snippet:

```bash
export GOOGLE_API_KEY="${GOOGLE_API_KEY:-local-gemma}"
export GOOGLE_BASE_URL="${GOOGLE_BASE_URL:-http://192.168.87.22:8081/v1}"
export CLAW_SYSTEM_PROMPT_FILE="${CLAW_SYSTEM_PROMPT_FILE:-/home/ubuntu/second-llm/prompts/system-prompt-gemma4-abliterated-current.txt}"
exec /home/ubuntu/second-llm/bin/claw --model gemma4 --dangerously-skip-permissions "$@"
```

To change the prompt later, edit only:

```text
/home/ubuntu/second-llm/prompts/system-prompt-gemma4-abliterated-current.txt
```

No provider code change is needed after that.
