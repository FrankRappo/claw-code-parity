# Direct answer prompts for Telegram Gemma and Claw

On 2026-10-12 the operator requested stronger instructions against moralizing,
unsolicited concern for the questioner, and speeches about corporate censorship
versus the model's own principles. Both live Telegram request paths now read
updated role-specific prompts on every model request, without a service restart.

## Ordinary Gemma chat

Source:
`gemma4-amd-vulkan-ops repo, llm_ops/telegram_bot/prompts/gemma4-abliterated-system-prompt.txt`.

Deployed on the Telegram host:
`/opt/tg-gemma-bot/gemma4-abliterated-system-prompt.txt`.

SHA-256: `c45b6a95942c17545c4d3422747e49ad15d967e59eedd1a05d497d21b11f2f9d`.

Backup:
`/opt/tg-gemma-bot/gemma4-abliterated-system-prompt.txt.bak-direct-20261012T182345Z`.

The chat prompt no longer contains Claw tool or autonomous-plan claims. It
requires direct answers, no unsolicited speculation about the user's wellbeing
or motives, no anthropomorphic self-protection rhetoric, and no recycling of
earlier moralizing assistant history. It preserves truthful reporting of real
capability limits: ordinary chat receives no executable tools.

## Telegram Claw

Source:
`this directory (integrations/telegram/), prompts/gemma4-abliterated-system-prompt.txt`.

Deployed on VM121:
`/home/clawrun/analise_storage/claw-code-parity/integrations/telegram/prompts/gemma4-abliterated-system-prompt.txt`.

SHA-256: `6d0497891f923f437a3bc9d3913e4e4245768eaa8f94eec4e7b9cd372b702ddc`.

Backup: the deployed path plus `.bak-direct-20261012T182354Z`.

The direct-answer style was strengthened while retaining the tool registry,
TodoWrite, durable project state, the 64000-token compaction threshold, the
32000-token output ceiling, and concrete destructive/authority boundaries.
The ordinary-chat and Claw prompt hashes intentionally differ.

## Verification

- Gemma Telegram tests: 23 passed; Claw Telegram tests: 89 passed.
- Python syntax checks and `git diff --check` passed in both repositories.
- The installed bot read the new chat prompt with the expected hash.
- With a prior moralizing assistant speech inserted into an isolated test
  conversation, Gemma returned the requested localhost health-check command
  without repeating that speech.
- On a separate factual-capability probe, ordinary Gemma correctly said it had
  not executed server commands.
- An isolated Claw invocation used the live bridge's configuration and
  environment. It successfully executed Bash `printf`, recorded the expected
  tool output, and returned exactly `CLAW_STYLE_OK` in 32.78 seconds.
  Evidence is in `/home/clawrun/.prompt-style-smoke-2yao4vd7/` on VM121;
  no existing Telegram project/session was used for this probe.
- The Telegram bot, Claw bridge, and model tunnel retained their original PIDs;
  no session reset, service restart, or model reload was performed.

These checks establish deployment and the sampled behavior, not a guarantee
that every possible future response will follow the requested style.
