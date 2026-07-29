# Claw Telegram status delivery incident — 2026-07-29

## Symptom

The operator did not receive the latest Claw status/final-result message in
Telegram even though the Claw bridge itself remained available.

## Root cause

The active Telegram host recorded transient Telegram API failures (`HTTP 502`
and read timeouts). `claw_status_text()` successfully obtained bridge status,
but the common `send_message()` path made only one `sendMessage` request. A
single transport failure therefore discarded the reply. The bridge and reverse
tunnel were healthy, so restarting or changing the Claw runtime was not the
correct fix.

## Fix

`telegram_claw_bot.py` now retries one outbound message after:

- transport/OS errors such as a read timeout or disconnected socket;
- Telegram HTTP 429;
- Telegram HTTP 500, 502, 503, or 504.

The sender makes at most three attempts, uses bounded exponential backoff, and
honors Telegram's `retry_after` response value up to 30 seconds. Non-retryable
4xx errors fail immediately. Logs include only the attempt, delay, exception
type or HTTP code; the bot token, URL, chat ID, and message body are not logged.

Because Telegram Bot API has no idempotency key, an ambiguous disconnect after
Telegram accepted a request can rarely yield a duplicate. This is preferable
to silently losing the final Claw result.

## Verification

Fresh validation on 2026-07-29:

- the three new retry regression tests first reproduced the missing-retry
  behavior and passed after the patch;
- all `72` Telegram/bridge unit tests passed;
- Python compilation and `git diff --check` passed;
- the active bot was backed up, replaced atomically, and restarted;
- `tg-gemma-bot.service` reported `active/running` with `NRestarts=0`;
- the deployed bot built live Claw status through the reverse tunnel and
  successfully sent one diagnostic status message through Telegram;
- no token or credential was written to this document or test output.

## Deployment and rollback

Active bot path:

```text
/opt/tg-gemma-bot/telegram_gemma_bot.py
```

The deployment created a timestamped sibling backup named
`telegram_gemma_bot.py.before-status-retry-<UTC timestamp>`. To roll back, copy
that backup over the active file, compile it with `python3 -m py_compile`, and
restart `tg-gemma-bot.service`.

The retry settings have safe defaults and do not require an environment-file
change. Optional overrides are documented in
`telegram-claw-bot.env.example`.
