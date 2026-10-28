# Telegram Gemma 4 vision bot — deployed 2026-07-15

## Result

The active Telegram bot now accepts text, Telegram photos, and PNG/JPEG
screenshots. It was deployed and verified against the live two-GPU Gemma 4
backend.

Architecture:

```text
Telegram API
  -> jump host: tg-gemma-bot.service
  -> jump host: http://127.0.0.1:18080
  -> llm-srv1-forward.service
  -> srv1: http://127.0.0.1:8080
  -> Gemma 4 31B on srv1 Vulkan0 + srv2 RPC0
```

The bot remains on the jump host because the historic direct `srv1 ->
api.telegram.org:443` path was unreliable. Do not enable the obsolete srv1 bot
unit while this deployment is active.

## Active files and services

Jump host:

```text
/opt/tg-gemma-bot/telegram_gemma_bot.py
/opt/tg-gemma-bot/tg-gemma-bot.env       # secret; never print or commit
/etc/systemd/system/tg-gemma-bot.service

tg-gemma-bot.service: active + enabled
llm-srv1-forward.service: active + enabled
LLM_BASE_URL: http://127.0.0.1:18080
```

Tracked deployment source and tests:

```text
/work/claster/analise_storage/llm_ops/telegram_bot/
```

Deployed Python SHA-256:

```text
9ec0f8fe5b27cab3c63c28bb224a9aaac04baa2d7856d961e277b5561eb18847
```

Rollback copy created before deployment:

```text
/opt/tg-gemma-bot/telegram_gemma_bot.py.bak-20260714T222546Z
```

## Image behavior

- chooses the largest Telegram `photo` variant;
- accepts PNG or JPEG documents/screenshots;
- uses the caption as the image question;
- without a caption asks the model to describe the image and extract important text;
- rejects unsupported MIME types before download;
- enforces declared and streaming size limits;
- default maximum is 10 MiB (`MAX_IMAGE_BYTES=10485760`);
- validates PNG/JPEG magic bytes and declared/actual MIME equality;
- authenticates the Telegram user before downloading the image;
- sends `image_url` before the text block as required by llama-server multimodal input;
- never saves Telegram images to disk;
- never saves base64 in chat history; history contains only a short image placeholder.

Existing behavior was retained:

- allowlist by Telegram ID or username;
- `/start`, `/help`, `/reset`, `/status`, `/bench`, `/tokens`, `/whoami`;
- per-chat history and token limits;
- per-chat locking and two-worker concurrency;
- `temperature`, `top_p`, `top_k`, and `enable_thinking` from the existing env;
- Telegram command menu and persistent reset keyboard.

## Deployment procedure

1. Verify the multimodal backend directly before touching the bot:

   ```bash
   curl -fsS http://127.0.0.1:8080/health
   ```

2. Run local no-network tests:

   ```bash
   cd /work/claster/analise_storage
   python3 -m unittest discover -s llm_ops/telegram_bot/tests -v
   python3 -m py_compile llm_ops/telegram_bot/telegram_gemma_bot.py
   ```

3. Back up `/opt/tg-gemma-bot/telegram_gemma_bot.py`, install the tracked
   source, compile it, then restart `tg-gemma-bot.service`.

4. Do not copy or display `tg-gemma-bot.env`. Add only the non-secret default if
   absent:

   ```text
   MAX_IMAGE_BYTES=10485760
   ```

5. Roll back automatically if compile, restart, or active-state verification
   fails.

## Verification evidence

Automated tests:

```text
15/15 unittest cases: PASS
py_compile: PASS
git diff --check: PASS
```

Live service evidence immediately after deployment:

```text
tg-gemma-bot.service: active/enabled, NRestarts=0
llm-srv1-forward.service: active
http://127.0.0.1:18080/health -> {"status":"ok"}
Telegram getMe: SuperbossAI_bot
Telegram command menu: installed
```

End-to-end deployed-code smoke through the jump tunnel used a generated PNG and
returned:

```text
КОД ПРОВЕРКИ: SOKOL-4827
СУММА: 19 375 РУБ.
completion_tokens: 34
prompt_tokens: 528
elapsed: 7.77 s
history contains base64: false
```

This proves the deployed bot payload, jump tunnel, mmproj, two-GPU backend, and
history sanitization together. After deployment, the owner sent a real inbound
Telegram screenshot and confirmed on 2026-07-15 that the bot works. This closes
the remaining Telegram `getFile`/attachment-path verification gap.

### High-resolution backend verification — 2026-07-20

The bot code did not require a new image transport path. The backend was changed
to adaptive `280–1120` visual tokens with batch limits `1280`, while
`--cache-ram 0` remained disabled. An end-to-end dense PNG request through the
deployed bot payload and jump tunnel returned:

```text
prompt_tokens: 1247
elapsed: 30.78 s
SOKOL-4827: correct
19 375 РУБ.: correct
small date 07-20: correct
history contains base64: false
```

At the same time `tg-gemma-bot.service`, `llm-srv1-forward.service` and the
tunnel health endpoint were active. The backend A/B, RAM measurements, source
links and rollback are documented in
[`GEMMA4-31B-HIGH-RES-VISION-1120-20260720.md`](GEMMA4-31B-HIGH-RES-VISION-1120-20260720.md).

Remaining bounded risk: the dependency-free bot limits encoded bytes and checks
the file signature, but it does not decode image headers to cap pixel dimensions
before llama-server sees the image. The current Telegram allowlist limits this
exposure. Add a dependency-free PNG/JPEG dimension preflight or an isolated
decoder limit before opening image ingestion to untrusted users.

## Operations

Safe checks on the jump host:

```bash
systemctl is-active tg-gemma-bot.service llm-srv1-forward.service
systemctl is-enabled tg-gemma-bot.service llm-srv1-forward.service
curl -fsS http://127.0.0.1:18080/health
journalctl -u tg-gemma-bot.service -n 50 --no-pager
```

Never use a command that prints the bot environment. To list configuration
coverage, print env key names or value lengths only.

## Rollback

```bash
systemctl stop tg-gemma-bot.service
cp -a /opt/tg-gemma-bot/telegram_gemma_bot.py.bak-20260714T222546Z \
  /opt/tg-gemma-bot/telegram_gemma_bot.py
python3 -m py_compile /opt/tg-gemma-bot/telegram_gemma_bot.py
systemctl restart tg-gemma-bot.service
systemctl is-active tg-gemma-bot.service
```

Rollback only the bot does not require stopping Gemma or the RPC worker.
