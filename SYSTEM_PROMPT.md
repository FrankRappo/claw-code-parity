# Deployment system-prompt injection

Claw's OpenAI-compatible provider can prepend an operator-controlled deployment
prompt to every `/v1/chat/completions` request. This is required for the local
Gemma deployment: putting the persona only in `CLAUDE.md` makes it project
context, while this patch sends it as a real `role=system` message before
Claw's generated coding/tool instructions.

The production prompt is tracked at:

```text
integrations/telegram/prompts/gemma4-abliterated-system-prompt.txt
```

It explicitly reapplies the local `Gemma 4 31B IT Abliterated` identity after
long history, compaction, resume, tool results, and built-in Agent delegation.
It also forbids invented policy refusals while preserving factual reporting of
real access, tool, credential, and capability limits.

## Runtime configuration

Prefer a file:

```bash
export CLAW_SYSTEM_PROMPT_FILE=/absolute/path/gemma4-abliterated-system-prompt.txt
```

A direct value remains available as a fallback:

```bash
export CLAW_SYSTEM_PROMPT='Deployment-specific instruction'
```

If both are set, a readable non-empty `CLAW_SYSTEM_PROMPT_FILE` wins. If the
file is missing, unreadable, or empty, `CLAW_SYSTEM_PROMPT` is used. Without
either value, Claw behavior is unchanged.

The provider reads the file on every model request. Editing its contents takes
effect on the next model call and does **not** require rebuilding Claw or
restarting the bridge. A restart is needed only after changing the environment
variable/path itself.

## Request shape

`rust/crates/api/src/providers/openai_compat.rs` calls
`effective_system_prompt()` while building the OpenAI-compatible payload. The
single system message is assembled in this order:

```text
<CLAW_SYSTEM_PROMPT_FILE or CLAW_SYSTEM_PROMPT>

<Claw-generated system/tool prompt>
```

Conversation messages follow normally. Keeping one combined system message is
compatible with llama.cpp's OpenAI endpoint and ensures that Claw's normal tool
instructions remain active.

The Telegram bridge passes only the two explicit prompt variables into the
sanitized Claw child environment. It still excludes the Telegram bridge token
and unrelated secrets. Because the built-in Agent executes inside the same
Claw process environment and uses the same provider, both the parent and child
receive the deployment prompt. No separate child-Agent patch is required.

## Patch implementation

To recreate the patch on another compatible Claw revision:

1. In `rust/crates/api/src/providers/openai_compat.rs`, import `std::fs` and
   define `CLAW_SYSTEM_PROMPT_FILE` and `CLAW_SYSTEM_PROMPT` names.
2. Replace direct use of `request.system` in
   `build_chat_completion_request()` with `effective_system_prompt()`.
3. Read and trim the configured file at request-build time; fall back to the
   direct environment value.
4. Prepend the deployment prompt to the existing Claw prompt with one blank
   line between them.
5. Add tests proving precedence, prepending, fallback, and file hot reload.
6. In `integrations/telegram/claw_project_bridge.py`, explicitly allow the two
   prompt variables through `_agent_environment()` without broad environment
   inheritance.
7. Rebuild and verify:

```bash
cd rust
cargo fmt --all -- --check
cargo test -p api openai_compat -- --nocapture
cargo build --release -p rusty-claude-cli --bin claw
cd ..
python3 -m unittest discover -s integrations/telegram/tests -v
```

The original implementation was preserved in the public fork on branch
`system-prompt-injection-20260606`, commit `9ae9426`. The current production
branch restores that mechanism on top of the newer resume, compaction,
Telegram, and Agent changes rather than switching back to the historical
branch.

## Telegram Gemma path

The combined Telegram bot uses `SYSTEM_PROMPT_FILE` for ordinary Gemma and the
Vision preprocessing call. Its loader uses this precedence:

1. `SYSTEM_PROMPT_FILE` (read on every request);
2. legacy `SYSTEM_PROMPT` environment value;
3. the bundled tracked prompt;
4. a short built-in fallback.

Example:

```bash
SYSTEM_PROMPT_FILE=/opt/tg-gemma-bot/gemma4-abliterated-system-prompt.txt
```

Use the same tracked prompt contents for Telegram and Claw. Keeping a single
text and matching SHA-256 avoids different personas between chat mode, coding
mode, Vision preprocessing, and child Agents.

## Operational verification

After deployment:

```bash
# confirm only paths/presence, never dump credentials
systemctl show claw-telegram-bridge.service -p ActiveState -p NRestarts
systemctl show tg-gemma-bot.service -p ActiveState -p NRestarts

# local source/deployed copies must match
sha256sum \
  integrations/telegram/prompts/gemma4-abliterated-system-prompt.txt \
  /path/to/deployed/gemma4-abliterated-system-prompt.txt
```

Unit tests prove the actual request payload. A live smoke turn should verify
that ordinary Gemma, a parent Claw turn, and a built-in child Agent identify the
Abliterated deployment without the user having to remind them.
