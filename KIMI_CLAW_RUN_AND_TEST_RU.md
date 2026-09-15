# Kimi + Claw на Windows: запуск, продолжение и проверка

## 1. Компоненты и их назначение

| Файл | Назначение |
|------|-----------|
| `install.ps1` | Первичная установка: зависимости, ACL, порты |
| `start-kimi-gateway.ps1` | Запуск gateway (порт 18082) |
| `stop-kimi-gateway.ps1` | Корректная остановка gateway |
| `import-edge-session.ps1` | Импорт web-сессии Edge в `session.dpapi` |
| `protect-session.ps1` | Установка приватных ACL на сессию |
| `set-private-acl.ps1` | Настройка прав доступа к файлам |
| `get-kimi-session-marker.ps1` | Получение маркера текущей сессии |
| `claw-kimi.cmd` | Локальный launcher Claw (пример) |

**Три режима аутентификации:**
- `session.dpapi` — web-логин через браузер (Edge), шифруется DPAPI
- `api-key.txt` — локальный bearer для gateway
- `direct-api-key.txt` — официальный Kimi Coding API (не тестируем без ключа)

---

## 2. Установка и первый запуск

```powershell
# От администратора PowerShell
cd "C:\claw cod\kimi-claw-gateway\windows"
.\install.ps1

# Установка ACL
.\set-private-acl.ps1
```

---

## 3. Импорт и замена web-сессии (без пересборки)

```powershell
# Безопасный вариант по умолчанию: отдельный профиль захвата.
# Обычный открытый Edge закрывать не нужно.
.\import-edge-session.ps1

# Защита
.\protect-session.ps1
```

Режим `-ProfileMode Existing` допустим только после штатного закрытия всех окон Edge: импортёр сам откажется работать при открытом профиле, чтобы не повредить его. Не завершайте пользовательский Edge принудительно ради обычного режима `Dedicated`.

Признак успеха: `session.dpapi` обновлён по дате, размер > 0 байт.

---

## 4. Запуск и остановка gateway

```powershell
# Старт
.\start-kimi-gateway.ps1

# Проверка health (должен ответить JSON)
Invoke-RestMethod -Uri "http://127.0.0.1:18082/health" -TimeoutSec 5

# Стоп
.\stop-kimi-gateway.ps1
```

Ожидаемый ответ health: `{"status":"ok","version":"..."}`

---

## 5. Запуск Claw

```powershell
# Рабочая директория определяет стабильный session marker.
cd "C:\claw cod"

# Запуск через launcher
"C:\claw cod\claw-code-parity — копия (2)\claw-kimi.cmd"
```

**Пример `claw-kimi.local.cmd`** (создай рядом для экспериментов):

```batch
@echo off
set KIMI_GATEWAY_URL=http://127.0.0.1:18082
set KIMI_SESSION_FILE=C:\claw cod\kimi-claw-gateway\windows\session.dpapi
cd /d "C:\claw cod\claw-code-parity — копия (2)"
claw.exe chat --gateway %KIMI_GATEWAY_URL% --session %KIMI_SESSION_FILE%
```

**Переключение gateway/direct без пересборки:** скопируй `claw-kimi.local.cmd.example` в `claw-kimi.local.cmd`; этот локальный файл исключён из Git.

```powershell
Copy-Item .\claw-kimi.local.cmd.example .\claw-kimi.local.cmd

# Новый ключ официального Kimi Coding API — одна строка, без кавычек.
$state = Join-Path $env:LOCALAPPDATA 'KimiClawGateway'
New-Item -ItemType Directory -Force $state | Out-Null
Set-Content -LiteralPath (Join-Path $state 'direct-api-key.txt') `
  -Value 'ВСТАВЬ_НОВЫЙ_API_KEY' -NoNewline
```

Для direct-режима раскомментируй в `claw-kimi.local.cmd` четыре строки `KIMI_PROVIDER_MODE=direct`, `KIMI_DIRECT_BASE_URL`, `KIMI_DIRECT_MODEL`, `KIMI_DIRECT_API_KEY_FILE`. Для возврата к web-gateway укажи `KIMI_PROVIDER_MODE=gateway`. Ни пересборка приложения, ни изменение отслеживаемых файлов не нужны.

---

## 6. Управление сессией внутри чата

```
/session list          # Список сессий
/resume <id>           # Продолжить конкретную сессию
/agent off             # Не требует tool call, права НЕ отбирает
/agent on              # Требует ≥1 настоящий tool call
/agent status          # Текущее состояние
```

**Важно:** `/agent on` требует хотя бы один реальный вызов инструмента. Доступный набор определяется **активным реестром Claw**, не самой командой. `/agent off` не отключает инструменты — она лишь снимает обязательность их использования.

---

## 7. Лимиты и compaction

**Накопительные лимиты одного upstream-чата:**
- 1000 сообщений
- 200000 токенов

**Поведение:**
- Обычный Claw compaction **не обнуляет накопительные счётчики** upstream-чата. Пока оба лимита ниже порога, gateway пишет `continued across Claw compaction` и использует тот же `chat_id`.
- При достижении любого лимита gateway создаёт новую upstream-generation, но сохраняет локальную Claw-сессию и её model/session marker. В новый чат передаётся текущий компактный контекст: summary, сохранённый хвост и новая реплика.
- Поэтому долгий агент не начинает задачу заново. Сбрасывается только окно нового upstream-чата; суточная/аккаунтная квота Kimi этим не сбрасывается.

**Проверка лимитов:**
```powershell
$log = "$env:LOCALAPPDATA\KimiClawGateway\gateway.18082.stderr.log"
Select-String -Path $log -Pattern `
  'continued across Claw compaction|rotating generation=.*reason=cumulative_limit'
```

---

## 8. Проверка продолжения того же чата после compaction

**По логам gateway:**

1. Найди `Persistent session=<12 hex>` до compaction.
2. После compaction ищи тот же ключ и `continued across Claw compaction`.
3. При лимите допустимо `rotating generation=...`: ключ сессии остаётся тем же, меняется только upstream-generation.

```powershell
# Пример grep по логам (если включено логирование в файл)
Select-String -Path "$env:LOCALAPPDATA\KimiClawGateway\gateway.18082.stderr.log" `
  -Pattern 'Persistent session=|continued across Claw compaction|rotating generation='
```

---

## 9. Unit-тесты и проверка синтаксиса

```powershell
# В директории проекта
cd "C:\claw cod\kimi-claw-gateway"

# Python compileall — проверка синтаксиса
python -m compileall windows\

# Если есть pytest:
python -m pytest tests\ -v

# Проверка импортов модулей gateway
python -c "import gateway; print('OK')"
```

---

## 10. DNS: проверка 127.0.0.1:5300 (dnscrypt)

**⚠️ ВАЖНО:** `nslookup` и `dig` по умолчанию идут на порт 53. Порт надо указать явно: для Windows `nslookup` используется `-port=5300`, для `dig` — `-p 5300`. Иначе проверяется не тот listener.

### 10.1 UDP-проверка (корректная)

```powershell
# Windows nslookup: реальный DNS-запрос на UDP 5300
nslookup -port=5300 example.com 127.0.0.1

# dig (если установлен)
dig @127.0.0.1 example.com -p 5300
```

### 10.2 TCP-проверка (корректная)

```powershell
# Проверка только наличия TCP listener (не доказывает резолвинг)
Test-NetConnection -ComputerName 127.0.0.1 -Port 5300

# Реальный DNS-запрос через TCP
nslookup -vc -port=5300 example.com 127.0.0.1
```

### 10.3 DNS-запрос через указанный порт

```powershell
# С помощью Resolve-DnsName НЕЛЬЗЯ указать порт — используй сторонние утилиты

# Если установлен dnscrypt-proxy или аналог:
# Проверь конфиг на 127.0.0.1:5300
Get-Content "C:\Program Files\dnscrypt-proxy\dnscrypt-proxy.toml" | Select-String "listen_addresses"

# Проверка слушающего порта
Get-Process -Id (Get-NetTCPConnection -LocalPort 5300 -ErrorAction SilentlyContinue).OwningProcess -ErrorAction SilentlyContinue
```

**Ожидаемый признак успеха:** `nslookup`/`dig` возвращает DNS-ответ с адресом. Одного состояния `LISTENING` недостаточно: listener может быть запущен, но не иметь рабочего upstream.

---

## 11. Windows HTTPS

```powershell
# Базовая проверка TLS
Invoke-RestMethod -Uri "https://www.google.com" -TimeoutSec 10

# С проверкой сертификата
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$resp = Invoke-WebRequest -Uri "https://www.google.com" -UseBasicParsing
$resp.StatusCode  # Должно быть 200
```

---

## 12. Proxifier SOCKS5 127.0.0.1:8081

### 12.1 Проверка порта

```powershell
Test-NetConnection -ComputerName 127.0.0.1 -Port 8081
```

### 12.2 Проверка через curl (если установлен)

```powershell
curl --socks5 127.0.0.1:8081 https://www.google.com
```

### 12.3 Исключения (не проксировать)

| Адрес | Причина |
|-------|---------|
| `127.0.0.1` | Локальный gateway, DNS |
| `192.168.0.1` | DNS роутера (Wi-Fi адаптер) |
| `172.18.0.2` | DNS sing-tun интерфейса |

**В Proxifier:** Profile → Proxification Rules → Add Direct Rule:
- Target: `127.0.0.1`, `192.168.0.1`, `172.18.0.2`
- Action: Direct

---

## 13. DNS адаптеров

```powershell
# Wi-Fi адаптер — DNS роутера
Get-DnsClientServerAddress -InterfaceAlias "Wi-Fi" | Select-Object -ExpandProperty ServerAddresses
# Ожидается: 192.168.0.1

# sing-tun адаптер
Get-DnsClientServerAddress -InterfaceAlias "*sing*" -ErrorAction SilentlyContinue | Select-Object -ExpandProperty ServerAddresses
# Ожидается: 172.18.0.2
```

---

## 14. WSL: маршрут и HTTPS через tun-vlessssh130

```bash
# Внутри WSL Ubuntu
# Проверка маршрута к 8.8.8.8
ip route get 8.8.8.8
# Ожидается: через tun-vlessssh130

# Проверка интерфейса
ip addr show tun-vlessssh130

# HTTPS через маршрут
curl -v --connect-timeout 10 https://www.google.com

# Проверка DNS в WSL
cat /etc/resolv.conf
```

**Если маршрут не через tun:** проверь `ip rule` и таблицы маршрутизации.

---

## 15. Ожидаемые признаки успеха

| Компонент | Признак |
|-----------|---------|
| Gateway | `http://127.0.0.1:18082/health` → 200 OK |
| Claw | Промпт `>` без ошибок подключения |
| Session | `/session list` показывает активную сессию |
| DNS 5300 | `nslookup -port=5300 example.com 127.0.0.1` возвращает адрес без timeout |
| SOCKS5 | `curl --socks5 127.0.0.1:8081` → HTML страницы |
| WSL route | `ip route get 8.8.8.8` содержит `tun-vlessssh130` |
| Compaction | Лог содержит `continued across Claw compaction` |

---

## 16. Troubleshooting

| Симптом | Причина | Решение |
|---------|---------|---------|
| `Connection refused 18082` | Gateway не запущен | `.\start-kimi-gateway.ps1` |
| `session expired` | `session.dpapi` устарел | `.\import-edge-session.ps1` |
| `agent on` не работает | Нет tool call | Выполни команду, требующую инструмент |
| `assistant stream produced no content` после text-only ответов | Upstream-чат перестал выполнять обязательный tool call | Новый gateway один раз корректирует ответ, после второго подряд text-only ответа автоматически меняет upstream-чат и повторяет текущий контекст |
| После `/resume` создаётся другой Kimi-чат | Старая JSONL-сессия не содержала модель | Один раз запусти controller с `-Model <прежнее-точное-имя>`; дальше модель сохраняется автоматически |
| DNS timeout | Проверяется порт 53 либо dnscrypt listener не имеет рабочего upstream | Используй `nslookup -port=5300 ...`; отдельно проверь listener и его журнал |
| SOCKS5 не проксирует | Правила Proxifier | Проверь исключения 127.0.0.1/192.168.0.1/172.18.0.2 |
| WSL не через tun | Приоритет маршрутов | `ip route add 8.8.8.8 dev tun-vlessssh130` |
| `compacted summary` вместо `continued` | Достигнут лимит | Нормально, чат продолжается с summary |

---

## 17. Быстрый старт (чек-лист)

```powershell
# [ ] 1. Gateway стоп (если был)
.\stop-kimi-gateway.ps1

# [ ] 2. Обновить сессию (при необходимости)
.\import-edge-session.ps1

# [ ] 3. Защита
.\protect-session.ps1

# [ ] 4. Старт gateway
.\start-kimi-gateway.ps1

# [ ] 5. Проверка health
Invoke-RestMethod http://127.0.0.1:18082/health

# [ ] 6. Запуск Claw из постоянной рабочей директории
cd "C:\claw cod"; "C:\claw cod\claw-code-parity — копия (2)\claw-kimi.cmd"

# [ ] 7. Внутри Claw: /agent status, /session list

# [ ] 8. Smoke-тесты DNS/SOCKS/WSL
```
