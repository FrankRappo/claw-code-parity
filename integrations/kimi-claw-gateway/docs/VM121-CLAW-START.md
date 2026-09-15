# VM121 launch files

> **Актуально с 2026-08-08:** контур `192.168.87.*:8081` выведен из
> эксплуатации и не используется Claw Code, Telegram bridge или systemd.
> Рабочий endpoint на VM121: `http://127.0.0.1:18080/v1`.

Дополнительный Kimi web gateway для Claw Code:

```text
http://127.0.0.1:18081/v1
```

Папка для запуска VM121:

```text
\\wsl.localhost\Ubuntu-24.04\work\claster\vm121
```

## Запуск из Windows Explorer

Открыть терминал VM121:

```text
VM121-terminal.cmd
```

Открыть Claw Code сразу внутри VM121:

```text
VM121-claw-code.cmd
```

Открыть Claw Code через Kimi:

```text
VM121-claw-kimi.cmd
```

## Запуск из WSL

Терминал VM121:

```bash
/work/claster/vm121/vm121-terminal.sh
```

Проверка маршрута без интерактивного входа:

```bash
/work/claster/vm121/vm121-terminal.sh --check
```

Claw Code на VM121:

```bash
/work/claster/vm121/vm121-claw-code.sh
```

Одноразовый вызов Claw:

```bash
/work/claster/vm121/vm121-claw-code.sh --output-format json prompt 'Say READY'
```

Claw Code через Kimi:

```bash
/work/claster/vm121/vm121-claw-kimi.sh
/work/claster/vm121/vm121-claw-kimi.sh --output-format text prompt 'Reply with exactly READY'
```

## Внутри VM121

```bash
run-claw-gemma2
run-claw-gemma2 --help
run-claw-gemma2 --output-format json prompt 'Say READY'
```

Kimi-вариант:

```bash
run-claw-kimi
run-claw-kimi --output-format text prompt 'Reply with exactly READY'
```

Сервисы Kimi:

```bash
systemctl status kimi-egress-socks.service
systemctl status kimi-api.service
curl -fsS http://127.0.0.1:18081/health
```

Актуальный endpoint Gemma для Claw:

```text
http://127.0.0.1:18080/v1
model: gemma4
physical slot: 163840
automatic compaction threshold: 64000
maximum completion: 32000
```

Адреса `192.168.87.100:8081` и `192.168.87.22:8081` ниже могут встречаться
только в исторических документах за июнь 2026 года; возвращать их в настройки
или использовать для health-check не нужно.

Полная документация рядом:

```text
SECOND-LLM-SRV2-HANDOFF-20260606.md
```


## Prompt / SYSTEM_PROMPT transfer

The documented srv1/Gemma client prompt has been transferred to VM121 and is active through the VM121 workspace `CLAUDE.md`:

```text
/home/ubuntu/second-llm/CLAUDE.md
/home/ubuntu/second-llm/prompts/system-prompt-gemma4-abliterated-current.txt
/home/ubuntu/second-llm/prompts/client-runtime-defaults.env
```

Local source/copy:

```text
/work/claster/vm121/CLAUDE.md
/work/claster/vm121/prompts/system-prompt-gemma4-abliterated-current.txt
/work/claster/vm121/VM121-PROMPT-TRANSFER-20260606.md
```

Verified on VM121 with:

```bash
/home/ubuntu/second-llm/bin/claw system-prompt --cwd /home/ubuntu/second-llm
```

The generated system prompt includes the Gemma 4 31B Abliterated persona lines from the srv1/bot documentation. Reference VM120 storage/orchestrator prompt files were also copied under `prompts/`, but they remain reference-only until VM121 has matching read-only mounts/tools.
