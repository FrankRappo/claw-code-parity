# Telegram SOCKS egress — 2026-09-22

This is the current egress path for the combined Gemma/Claw Telegram bot. The
bot **still runs on `<bot-vps>`**; `<socks-jump-host>` is only its Telegram
network exit. The existing Gemma and Claw localhost tunnels were not moved.

For the subsequent malformed-hosts-entry and stale Claw tunnel destination
incident, see [the 2026-10-12 recovery record](TELEGRAM-CLAW-RECOVERY-20261012.md).

```text
Telegram bot on <bot-vps>
  -> PySocks, socks5h://127.0.0.1:11080 (Telegram API and file downloads only)
  -> telegram-socks-forward.service (SSH local forward, port 56777)
  -> <socks-jump-host>:127.0.0.1:1080 (existing danted SOCKS5)
  -> api.telegram.org:443

Gemma: bot -> 127.0.0.1:18080 -> llm-srv1-forward.service -> srv1:8080
Claw:  bot -> 127.0.0.1:19090 -> existing Claw bridge forward
```

## Why this changed

The bot was running but repeatedly logged `poll error: ... Network is
unreachable`. From `217`, TCP SYN packets to Telegram's resolved HTTPS address
left the host without a reply. The `217 -> <srv1-public>:234` SSH connection and
Gemma `/health` were working, while Telegram HTTPS worked from `194`. These
measurements identify a failed egress path, **not** the specific upstream
device/operator responsible for it.

## Installed configuration

- On `217`, Ubuntu `python3-socks` provides PySocks/SocksiPyHandler. The bot's
  `TELEGRAM_SOCKS_URL=socks5h://127.0.0.1:11080` is set by
  `/etc/systemd/system/tg-gemma-bot.service.d/telegram-socks.conf`. Only
  Telegram API and Telegram file requests use that opener; Gemma and Claw use
  a direct urllib opener.
- `/etc/systemd/system/telegram-socks-forward.service` is enabled. It listens
  on **loopback only** at `127.0.0.1:11080` and forwards over SSH to the
  pre-existing loopback-only Dante listener on `194` (`127.0.0.1:1080`).
- SSH authentication uses the dedicated mode-0600 key
  `/root/.ssh/tg-socks-jump194` on `217`, not a password in a service file.
  Its public key on `194` is restricted to source `<bot-vps>` and
  `permitopen="127.0.0.1:1080"`.
- Tracked source and unit templates are under
  `this directory (integrations/telegram/), `:
  `telegram_claw_bot.py`, `systemd/telegram-socks-forward.service`, and
  `systemd/tg-gemma-bot-socks.conf`. The live bot file was backed up as
  `/opt/tg-gemma-bot/telegram_gemma_bot.py.bak-socks-20260922T214824Z` before
  deployment. The bot token and private environment file were not changed.

## Read-only checks

Run on `217`:

```bash
systemctl is-active tg-gemma-bot.service telegram-socks-forward.service llm-srv1-forward.service
systemctl is-enabled tg-gemma-bot.service telegram-socks-forward.service
curl --socks5-hostname 127.0.0.1:11080 -fsS -m 10 -o /dev/null \
  -w 'Telegram via SOCKS: HTTP %{http_code}\n' https://api.telegram.org
curl -fsS -m 5 http://127.0.0.1:18080/health
journalctl -u tg-gemma-bot.service --since '10 minutes ago' --no-pager
```

`api.telegram.org` redirects at its root URL, so `curl -f` may report HTTP
`302`; this is a successful transport check, not a bot API test. A bot API
`getMe` test on deployment returned `ok=true` for `SuperbossAI_bot`. After
deployment the bot reported connected, both bot and SOCKS-forward services
had `NRestarts=0`, Gemma health returned HTTP 200, and no new poll errors were
seen over the monitored period. The Telegram integration test suite passed
86 tests. A fresh user-message delivery was not part of this check.

If the SOCKS check fails, inspect `telegram-socks-forward.service` on `217`
and the existing `danted.service` on `194` before touching Gemma or Claw.
Unsetting `TELEGRAM_SOCKS_URL` returns the bot to direct Telegram HTTPS, but
that is only a functional fallback after direct egress from `217` has been
verified; it was blocked at the time of this change. Do not print or commit
the bot environment or SSH private key.
