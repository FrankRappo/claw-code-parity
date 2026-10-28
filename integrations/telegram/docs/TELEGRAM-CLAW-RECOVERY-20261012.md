# Telegram and Claw connectivity recovery

On 2026-10-12 two independent network faults prevented the combined Telegram
bot from working. Both were corrected without restarting the Telegram bot,
Gemma model, or Claw bridge. Existing projects remained available.

## Telegram proxy fault

On proxy host `<socks-jump-host>`, a missing newline in `/etc/hosts` joined the
server hostname and the Telegram IPv4 mapping. Consequently,
`api.telegram.org` resolved to the proxy host's own IPv6 address. The full
`<bot-vps> -> 127.0.0.1:11080 -> Dante` hostname path failed with SOCKS
reply 2, while the same path with the Telegram IPv4 supplied directly worked.

At 18:02:20 UTC the malformed entry was split into two lines:

```text
2001:41d0:800:400e::fc5b:30c1 srv133267.hosttoname.com
149.154.167.220 api.telegram.org
```

Backup: `/etc/hosts.bak-claster-20261012T180220Z` on `194`.

Dante retained the old hostname result in its shared DNS cache. After
validating the unchanged configuration with `danted -V`, the cache was
invalidated at 18:06:58 UTC with:

```bash
systemctl kill --kill-who=main --signal=SIGHUP danted.service
```

The main PID remained `801`; this was a configuration/cache reload, not a
service restart. Only the main process was signalled. The full remote-DNS
SOCKS path then returned HTTP 302, and the installed bot's authenticated
`getMe` returned `ok=true` for `SuperbossAI_bot`.

Keep `TELEGRAM_SOCKS_URL=socks5h://127.0.0.1:11080`; direct Telegram HTTPS from
the bot host was not a working fallback. The pinned Telegram IPv4 is a
verified address for this incident, not a guarantee that it will never change.

## Claw reverse tunnel fault

On VM121 (`<vm121-lan>`), `/etc/claw-telegram-forward.env` still contained
`REMOTE_HOST=<previous-bot-vps>`, the retired Telegram VPS address. The bridge and
Gemma tunnel were healthy, but the reverse-forward service repeatedly timed
out and no listener existed on the bot host's `127.0.0.1:19090`.

At 18:03:24 UTC:

- `REMOTE_HOST` was changed to `<bot-vps>`;
- the new host alias was added to `/home/clawrun/.ssh/known_hosts` using the
  independently verified, unchanged ED25519 server key;
- only `claw-telegram-forward.service` was restarted.

Backups on VM121:

- `/etc/claw-telegram-forward.env.bak-claster-20261012T180324Z`
- `/home/clawrun/.ssh/known_hosts.bak-claster-20261012T180324Z`

The existing restricted forwarding identity and strict host-key verification
were preserved. Through the bot host, Claw `/health` returned HTTP 200 and an
authenticated `/v1/status` returned `ok=true`, with the existing project
visible, durable memory enabled, and the 64000-token compaction threshold
unchanged.

## Recovery verification

- Gemma `/health` through the bot host: HTTP 200, `status=ok`.
- Claw `/health` through the bot host: HTTP 200, `status=ok`.
- Telegram remote-DNS SOCKS transport: HTTP 302.
- Installed-bot `getMe`: `ok=true`.
- Telegram webhook absent; pending update count: 0.
- At 18:08:17 UTC the bot remained on its original PID with zero restarts.
  Its journal recorded four completed model responses to chat messages
  between 18:07:24 and 18:07:36 UTC, with no new poll or worker errors in the
  post-recovery window. The `answered chat` entry precedes Telegram delivery;
  no synthetic user-visible Telegram message was sent for this repair.

No bot token, bridge token, project history, session data, model settings, or
application source was changed. Backups are for inspection and targeted
rollback; restoring the old contents would reintroduce the original faults.
