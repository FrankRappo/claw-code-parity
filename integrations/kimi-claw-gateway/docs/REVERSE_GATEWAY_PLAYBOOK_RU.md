# Практическое руководство по reverse web API и подключению модели к Claw Code

## 1. Назначение документа

Этот документ описывает воспроизводимую методику, по которой был построен и
отлажен `kimi-claw-gateway`, и превращает её в универсальный процесс для другого
web-чата — например GLM.

Цель интеграции:

```text
Claw Code
  -> локальный OpenAI-совместимый API
  -> provider adapter
  -> web API выбранного сервиса
  -> поток текста и tool calls обратно в Claw
```

Результат должен быть не простым чат-прокси, а агентским runtime:

- один Claw-сеанс продолжает один логический диалог;
- модель получает полный актуальный список инструментов Claw;
- модель вызывает реальные инструменты, а не изображает их текстом;
- tool results возвращаются модели для следующего шага;
- длинная работа сжимается и продолжается;
- запросы дозируются, повторяются безопасно и не создают дублирующиеся чаты;
- токены, cookies, HAR и пароли не попадают в Git или логи.

> Интеграция web API неофициальна. Используйте только собственную учётную
> запись, соблюдайте условия сервиса и не обходите ограничения доступа.

## 2. Главный принцип: сначала доказательства, потом код

Нельзя начинать реализацию с догадок о URL, JSON-полях, model ID или формате
стрима. Для каждого элемента протокола должна существовать наблюдаемая опора:

| Что требуется узнать | Надёжный источник |
| --- | --- |
| URL создания чата | Network-запрос web-клиента |
| URL генерации | Fetch/XHR, SSE или WebSocket в DevTools |
| model ID | Сравнение запросов после переключения модели в UI |
| обязательные headers | Полный рабочий браузерный запрос |
| access/refresh token | Storage и запрос обновления сессии |
| формат кадров | Raw response/WS frames из DevTools |
| продолжение диалога | Второй запрос в том же web-чате |
| tool protocol | Запрос web-клиента с включённой агентской функцией |
| лимиты | Реальные ответы 429/resource_exhausted и UI |

Для нового провайдера заведите журнал доказательств:

```markdown
| Поле | Наблюдаемое значение/форма | Откуда получено | Проверено повторно |
| --- | --- | --- | --- |
| chat_create_url | ... | HAR request 12 | да |
| completion_url | ... | WS connection 2 | да |
| model_id | ... | diff basic/model-switch | да |
| refresh_url | ... | Network после refresh | да |
```

Если поле не подтверждено, помечайте его `UNKNOWN`; не подставляйте значение из
памяти или старого стороннего репозитория.

## 3. Разделение архитектуры

Не смешивайте в одном обработчике все уровни. Практически полезны семь границ:

1. **Browser session import** — получение сессии из собственного браузера.
2. **Secret store** — защищённое хранение и атомарное обновление токенов.
3. **Provider transport** — create chat, refresh, completion stream.
4. **Protocol parser** — SSE/WS/Connect frames в нормализованные события.
5. **Conversation manager** — chat ID, delta history, rotation, fingerprints.
6. **Tool bridge** — схемы Claw, имена инструментов, аргументы и результаты.
7. **OpenAI facade** — `/v1/models`, `/v1/chat/completions`, SSE и usage.

Такой разрез позволяет заменить Kimi на GLM, не переписывая весь gateway.

## 4. Безопасная лаборатория reverse engineering

### 4.1. Подготовка

- Используйте отдельный профиль Edge/Chrome для тестовой учётной записи.
- Включите MFA, если сервис его поддерживает.
- Не используйте root/admin-аккаунт на VPS для экспериментов.
- Создайте отдельную директорию вне Git для HAR, cookies и raw frames.
- В DevTools включите `Preserve log` и отключите cache на время захвата.
- Перед публикацией скриншотов скрывайте tokens, cookies, chat IDs и PII.

Рекомендуемая структура приватной лаборатории:

```text
%LOCALAPPDATA%\ProviderReverseLab\
├── captures\
├── fixtures-redacted\
├── session.dpapi
├── api-key.txt
└── logs\
```

### 4.2. Минимальная матрица браузерных действий

Для каждого сценария очищайте Network и выполняйте ровно одно действие:

1. открыть новый пустой чат;
2. отправить `Reply with exactly ONE`;
3. отправить `Reply with exactly TWO` в том же чате;
4. создать новый чат и повторить первый запрос;
5. переключить модель в UI;
6. включить/выключить web search;
7. прикрепить небольшой текстовый файл;
8. активировать агентский/tool-режим, если он есть;
9. дождаться истечения access token и зафиксировать refresh;
10. вручную остановить генерацию.

Сравнивайте запросы попарно. Один изменённый фактор позволяет понять назначение
поля намного надёжнее, чем большой HAR с десятками действий.

### 4.3. Что сохранять из DevTools

- Request URL, method и status.
- Request headers без секретных значений.
- Payload целиком в приватный capture.
- Тип ответа: JSON, SSE, chunked binary или WebSocket.
- Raw frames с временными границами.
- Порядок событий create-chat -> completion -> title/history update.
- Коды ошибок и тело ответа.
- Изменения Local Storage, Session Storage, IndexedDB и cookies.

HAR с токенами нельзя добавлять даже в приватную ветку репозитория. Создавайте
отдельные redacted fixtures, сохраняющие структуру, но не значения секретов.

## 5. Разбор аутентификации

### 5.1. Определите тип сессии

Web-сервис может использовать:

- cookie-only session;
- access token в `Authorization`;
- access + refresh tokens;
- CSRF token;
- device/browser ID;
- комбинацию cookie и bearer token.

Проверяйте минимальность headers удалением одного поля за раз. Не переносите все
браузерные headers навсегда: часть из них шум, fingerprinting или значения,
которые быстро устаревают.

### 5.2. Refresh lifecycle

Зафиксируйте:

- endpoint refresh;
- method;
- где передаётся refresh token;
- меняется ли refresh token после обновления;
- допустимый clock skew;
- коды revoked/expired session;
- нужна ли cookie одновременно с refresh token.

Gateway должен обновлять токен немного раньше `exp`, сохранять новую пару
атомарно и повторять исходный запрос только после успешного refresh.

### 5.3. Хранение на Windows

В текущей Kimi-реализации применяются:

- импорт из Edge через `windows/import-edge-session.ps1`;
- DPAPI-файл `%LOCALAPPDATA%\KimiClawGateway\session.dpapi`;
- атомарная запись через временный файл;
- loopback bearer key для защиты локального `/v1/*`;
- отдельный launcher, который читает ключ только в окружение процесса.

Для GLM создайте отдельное хранилище, например:

```text
%LOCALAPPDATA%\GlmClawGateway\session.dpapi
%LOCALAPPDATA%\GlmClawGateway\api-key.txt
```

Не используйте общий session-файл Kimi и GLM.

## 6. Восстановление web-протокола

### 6.1. Сначала минимальный текстовый запрос

Первый probe не должен содержать tools, файлы и историю:

```text
system: отсутствует или минимален
user: Reply with exactly PROBE_OK
```

Подтвердите:

- успешное создание чата;
- получение полного ответа;
- корректное завершение стрима;
- повторный turn в том же chat ID;
- новый chat ID для нового диалога.

### 6.2. Kimi как эталон исследованного контракта

Текущая интеграция использует:

```text
POST https://www.kimi.com/api/chat
POST https://www.kimi.com/apiv2/kimi.gateway.chat.v1.ChatService/Chat
POST https://auth.kimi.com/api/account.gateway.v1.AuthService/RefreshToken
```

У Kimi генерация идёт через framed Connect transport. Для K3 первый запрос
использует пустой provisional `chatId`, а реальный ID приходит событием стрима.
K2.6 допускает явное создание чата до генерации.

Для GLM эти URL и правила нельзя копировать. Нужно повторить браузерную матрицу и
заполнить новый provider contract.

### 6.3. Дифференциальный анализ payload

Сделайте нормализованный diff:

- отсортируйте JSON keys;
- замените UUID/timestamps/tokens placeholders;
- сравните первый и второй turns;
- сравните две модели;
- сравните search off/on;
- сравните обычный и agent режим.

Классифицируйте поля:

```text
CONSTANT          одинаково во всех запросах
PER_CHAT          меняется при новом диалоге
PER_MESSAGE       меняется на каждом turn
MODEL_DEPENDENT   меняется после выбора модели
FEATURE_DEPENDENT меняется при search/tools/files
SECRET            token/cookie/CSRF/device secret
UNKNOWN           назначение пока не доказано
```

## 7. Парсер потокового протокола

Нельзя считать, что один сетевой chunk равен одному событию. Парсер обязан:

- накапливать partial frame;
- поддерживать несколько frames в одном chunk;
- различать request echo и assistant output;
- отделять thinking от final text;
- распознавать chat-created event;
- корректно завершаться по done/finish event;
- обнаруживать оборванный последний frame;
- ограничивать максимальный размер буфера;
- сохранять диагностический тип неизвестного события без секретного payload.

Нормализованный внутренний поток может выглядеть так:

```python
@dataclass
class ProviderEvent:
    kind: Literal[
        "chat_created",
        "text_delta",
        "thinking_delta",
        "tool_call",
        "usage",
        "error",
        "done",
    ]
    data: dict[str, Any]
```

Тестируйте парсер только на redacted fixtures. Обязательные случаи:

- frame разделён посередине UTF-8 символа;
- несколько JSON-событий в одном chunk;
- пустые heartbeat frames;
- echo пользовательского текста;
- error frame после HTTP 200;
- stream завершился без done;
- неизвестное поле не ломает совместимость.

## 8. Provider adapter

Для второй модели не копируйте весь сервер. Сначала выделите контракт:

```python
class ProviderAdapter(Protocol):
    async def refresh_session(self) -> None: ...
    async def create_chat(self) -> str: ...
    async def stream_completion(
        self,
        chat_id: str,
        prompt: str,
        *,
        system_prompt: str,
        native_tool_names: list[str] | None,
    ) -> AsyncIterator[ProviderEvent]: ...
```

Общие части остаются одинаковыми:

- OpenAI request/response types;
- локальный bearer auth;
- pacing и retry policy;
- conversation fingerprints;
- tool registry validation;
- SSE facade;
- metrics;
- Windows launcher conventions.

Provider-specific части:

- URL и headers;
- refresh;
- create-chat contract;
- payload;
- frame decoder;
- model IDs и feature flags;
- provider error classification.

Не выносите общий слой раньше, чем подтверждены два рабочих адаптера. До этого
легко создать неверную абстракцию, основанную только на Kimi.

## 9. OpenAI-совместимый facade

Минимальный API для Claw:

```text
GET  /health
GET  /ready
GET  /metrics
GET  /v1/models
POST /v1/chat/completions
```

### 9.1. Streaming

Для `stream=true`:

1. вернуть HTTP/SSE и первый role chunk немедленно;
2. затем ждать медленный upstream;
3. передавать text deltas или tool-call deltas;
4. отправить finish reason;
5. отправить usage event;
6. завершить `data: [DONE]`.

Если делать полный provider preflight до открытия SSE, HTTP-клиент Claw может
решить, что запрос завис, повторить его и открыть несколько upstream-чатов.

Ошибку после начала SSE передавайте структурированным событием:

```json
{
  "error": {
    "type": "upstream_error",
    "message": "redacted diagnostic"
  }
}
```

### 9.2. Usage

Web API часто не возвращает tokenizer usage. Тогда используйте консервативную
оценку, но явно отмечайте, что она приблизительная. Нулевые usage fields мешают
Claw вовремя запускать auto-compaction.

## 10. Один длинный диалог

### 10.1. Conversation key

Для каждого независимого запуска Claw нужен уникальный marker модели:

```text
kimi-k2d6-<uuid>
glm-<model>-<uuid>
```

Gateway хэширует marker и хранит:

```json
{
  "chat_id": "provider-chat-id",
  "message_fingerprints": ["..."],
  "estimated_context_tokens": 12345,
  "generation": 1,
  "updated_at": 0
}
```

### 10.2. Delta synchronization

Claw отправляет всю локальную историю. Provider web-chat уже хранит старые
turns. Поэтому gateway сравнивает fingerprints и отправляет upstream только
новый хвост:

- новый user message;
- assistant tool call, если он ещё не известен upstream;
- новый tool result.

Если prefix fingerprints не совпал, безопаснее открыть новый provider chat и
передать нормализованную историю, чем продолжить неправильный диалог.

### 10.3. Rotation и compaction

Это разные операции:

- **rotation** меняет provider chat ID;
- **compaction** сжимает локальную историю Claw;
- **tool-call budget** предотвращает бесконечный цикл.

Для Kimi launcher установлен практический порог auto-compaction 16 000
оценочных input tokens. Claw runtime проверяет порог и между tool iterations,
а не только после окончания пользовательского turn.

Текущий gateway допускает до 32 последовательных tool calls в одном видимом
сегменте. После compaction старые tool messages заменяются summary, поэтому
большая задача может выполнить больше 32 действий суммарно, не раздувая prompt.

Для GLM пороги подбирайте экспериментально:

1. измерьте latency на 25%, 50%, 75% и 90% заявленного context;
2. повторите одинаковую агентскую задачу;
3. найдите точку ухудшения tool adherence;
4. поставьте compaction раньше этой точки;
5. hard limit оставьте выше practical limit.

## 11. Rate limit, concurrency и retries

### 11.1. Pacing

Запросы должны начинаться равномерно:

```text
interval_seconds = 60 / requests_per_minute
```

Для 10 RPM минимальная пауза между стартами — 6 секунд. Используйте FIFO lock,
а не отдельный `sleep` в каждом coroutine: параллельные запросы иначе проснутся
одновременно.

### 11.2. Concurrency

Для одной web-сессии безопасный стартовый режим:

```text
max_concurrent_upstream = 1
```

Увеличивать concurrency можно только после доказательства, что provider
разрешает параллельные generations для одного аккаунта и одного chat ID.

### 11.3. Retry policy

Повторяйте только:

- сетевые ошибки до первого принятого content frame;
- 408;
- 429 с backoff/Retry-After;
- 500/502/503/504;
- provider-specific temporary overload.

Не повторяйте автоматически:

- 400 schema error;
- 401 после неудачного refresh;
- 403 policy/account restriction;
- запрос, который уже мог выполнить необратимое действие;
- stream после того, как часть ответа уже отдана Claw.

Корректирующие retries формата tool call должны продолжать тот же upstream chat,
а не создавать новый чат на каждую ошибку.

## 12. Полный tool bridge Claw

### 12.1. Источник истины

Источник истины — `tools` в текущем OpenAI-запросе Claw. Не используйте:

- список, который модель написала текстом;
- статический список из старой версии Claw;
- словарь глагол -> инструмент;
- только 3-5 самых частых инструментов.

Текущий Windows Claw предоставляет 49 tools. Gateway регистрирует все имена как
provider-native device tools и добавляет компактные схемы в system prompt.

### 12.2. Валидация имени

Правильный порядок:

1. case-insensitive exact match;
2. удаление только известных префиксов (`functions.`, `claw_`, `bridge_`);
3. маленький explicit alias list для реально наблюдавшихся форм;
4. проверка, что итоговое имя существует в registry текущего запроса;
5. неизвестное имя -> corrective retry в том же provider chat.

Не применяйте общий suffix/fuzzy match. Например, `evil_read_file` не должен
автоматически превращаться в `read_file`.

### 12.3. Аргументы

Нормализуйте только подтверждённые совместимости:

```text
file_path -> path       только для зарегистрированного read_file
code      -> command    только для явного python/shell adapter
```

Проверяйте типы после parsing. Строка `"120000"` для integer timeout должна
стать числом, но произвольная строка не должна автоматически выполняться как
shell command.

### 12.4. Tool result loop

Цикл считается рабочим только в полном виде:

```text
user request
  -> assistant tool_call
  -> Claw executes tool
  -> tool result
  -> next model request
  -> next tool_call or verified final answer
```

Один успешно распарсенный tool call не доказывает агентскую работоспособность.

## 13. Надёжность локального execution runtime

Реальная интеграция выявила проблемы не только gateway, но и Claw runtime.

### 13.1. stdin

`bash` и `PowerShell` должны принимать optional `stdin`. Это позволяет модели
передавать данные отдельно от process command line.

Schema:

```json
{
  "command": "...",
  "stdin": "optional input",
  "timeout": 30000,
  "run_in_background": false
}
```

### 13.2. Finite timeout

У foreground command должен быть конечный timeout. В gateway применяется
30 000 мс, если сгенерированный shell call не указал timeout.

### 13.3. Process tree cleanup

При timeout нужно завершать дерево, а не только оболочку:

```text
PowerShell -> cmd -> ssh
sh -> bash -> ssh
```

Иначе `ssh` остаётся сиротой, удерживает handles, порт или ресурсы.

### 13.4. Detached output handles

Если PowerShell запускает detached child через `Start-Process`, потомок может
унаследовать stdout/stderr pipe. Родитель уже завершился, но `wait_with_output`
никогда не получает EOF.

Проверенное решение — capture stdout/stderr foreground wrapper во временные
файлы, а не в наследуемые pipes. Это позволяет wrapper быстро вернуть результат,
не убивая корректно отделённый процесс.

## 14. Runtime context без task-specific helpers

Gateway может сообщать модели только общие факты среды:

- Windows или Linux;
- home directory;
- Desktop/Documents/Downloads;
- связь Windows путей и WSL `/mnt/c`;
- последние user requests;
- последний tool result.

Нельзя добавлять helper под каждую задачу — SOCKS, Git, Docker, браузер и т.д.
Модель должна сама планировать. Gateway отвечает только за:

- доступность реальных tools;
- точные schemas;
- транспорт;
- восстановление после ошибок;
- общие safety invariants;
- проверяемое завершение.

## 15. Windows launcher для нового провайдера

Создайте отдельный launcher, не изменяющий Gemma/Kimi:

```bat
@echo off
setlocal
set "GLM_GATEWAY_ROOT=C:\path\to\glm-claw-gateway"
set "GLM_API_KEY_FILE=%LOCALAPPDATA%\GlmClawGateway\api-key.txt"
set /p "OPENAI_API_KEY="<"%GLM_API_KEY_FILE%"
set "OPENAI_BASE_URL=http://127.0.0.1:18082/v1"
set "CLAUDE_CODE_AUTO_COMPACT_INPUT_TOKENS=16000"
for /f %%I in ('powershell.exe -NoProfile -Command "[guid]::NewGuid().ToString([char]78)"') do set "GLM_CLAW_SESSION=%%I"
claw.exe --model "glm-web-%GLM_CLAW_SESSION%" %*
```

Имена env зависят от adapter routing в конкретной сборке Claw. Не используйте
пример буквально, пока не подтверждено, через какие переменные Claw выбирает
OpenAI-compatible provider.

Для каждого provider используйте отдельный port, state directory и API key:

```text
Kimi -> 127.0.0.1:18081
GLM  -> 127.0.0.1:18082
```

## 16. План переноса на GLM

### Фаза A. Разведка

- [ ] Выбрать официальный web UI GLM и зафиксировать домены.
- [ ] Создать отдельный браузерный профиль.
- [ ] Зафиксировать login/session lifecycle.
- [ ] Снять 10 минимальных сценариев из раздела 4.2.
- [ ] Найти create-chat, completion и refresh.
- [ ] Определить transport: SSE, WebSocket или binary frames.
- [ ] Подтвердить model IDs через UI diff.
- [ ] Проверить продолжение одного чата.
- [ ] Зафиксировать реальные error frames и rate limits.

### Фаза B. Read-only prototype

- [ ] Реализовать `GlmWebClient` только для одного текстового запроса.
- [ ] Добавить redacted fixtures.
- [ ] Реализовать frame parser с partial-frame tests.
- [ ] Добавить refresh без browser automation на каждый запрос.
- [ ] Реализовать `/health`, `/ready`, `/v1/models`.
- [ ] Не включать tools до стабильного multi-turn text loop.

### Фаза C. OpenAI facade

- [ ] Non-streaming completion.
- [ ] SSE completion с немедленным первым chunk.
- [ ] Typed upstream errors.
- [ ] Usage estimate.
- [ ] Local bearer auth.
- [ ] FIFO pacing и single concurrency.

### Фаза D. Persistent conversation

- [ ] Unique Claw marker.
- [ ] Chat ID persistence.
- [ ] Message fingerprints.
- [ ] Delta synchronization.
- [ ] Context rotation.
- [ ] Auto-compaction threshold.

### Фаза E. Tools

- [ ] Передать все tools текущего Claw request.
- [ ] Определить нативный GLM tool/device/function contract.
- [ ] Протестировать один read-only tool.
- [ ] Протестировать write/edit/shell loop.
- [ ] Добавить exact registry validation.
- [ ] Добавлять aliases только после реального наблюдения.
- [ ] Проверить unknown/malicious tool names.

### Фаза F. Windows production profile

- [ ] Импорт сессии из Edge/Chrome.
- [ ] DPAPI at rest.
- [ ] API key для loopback gateway.
- [ ] Отдельный launcher.
- [ ] Startup health validation.
- [ ] Логи без секретов.
- [ ] Restart/stop scripts.
- [ ] Проверка совместимости с существующей Gemma и Kimi.

## 17. Матрица provider mapping

Перед началом кода GLM заполните таблицу:

| Capability | Kimi | GLM |
| --- | --- | --- |
| Web origin | `www.kimi.com` | `UNKNOWN` |
| Auth origin | `auth.kimi.com` | `UNKNOWN` |
| Create chat | `/api/chat` | `UNKNOWN` |
| Completion transport | Connect v2 frames | `UNKNOWN` |
| Refresh | AuthService/RefreshToken | `UNKNOWN` |
| First chat ID | explicit K2.6 / provisional K3 | `UNKNOWN` |
| Model selector | payload `options.model` | `UNKNOWN` |
| System prompt | `options.systemPrompt` | `UNKNOWN` |
| Tools | named device tools | `UNKNOWN` |
| Tool-call syntax | native tags/frames | `UNKNOWN` |
| Search flag | payload option | `UNKNOWN` |
| Usage events | approximate locally | `UNKNOWN` |
| Temporary overload | framed error | `UNKNOWN` |

Ни одно `UNKNOWN` нельзя заполнять значением Kimi без браузерного подтверждения.

## 18. Тестовая пирамида

### 18.1. Offline unit tests

- payload shape;
- frame parsing;
- partial frames;
- request echo filtering;
- token expiry;
- atomic session store;
- DPAPI rejection outside Windows;
- rate pacer;
- persistent conversation delta;
- context rotation;
- tool-name validation;
- argument normalization;
- OpenAI streaming chunks;
- error-before/after stream start.

### 18.2. Contract tests

Используйте redacted captured responses. Тест должен доказать, что parser новой
версии всё ещё понимает известный web contract.

### 18.3. Live smoke

Минимальный порядок:

1. `/health`;
2. authenticated `/v1/models`;
3. text-only `READY`;
4. read_file;
5. write_file -> read_file;
6. PowerShell/Bash;
7. multi-tool loop;
8. длинная задача с compaction;
9. новая Claw session -> новый provider chat;
10. resume -> сохранённый логический контекст.

### 18.4. Hostile tests

- unknown forced tool -> HTTP 400;
- `evil_read_file` не принимается;
- model пишет fake tool result текстом;
- upstream отдаёт empty stream;
- upstream зависает;
- client disconnect;
- две одновременные generations;
- token истекает во время запроса;
- refresh token revoked;
- request содержит 49 больших schemas;
- detached process наследует output handles;
- timeout оставляет child process;
- контекст растёт внутри одного tool loop.

## 19. Наблюдаемость

Логируйте только безопасные метаданные:

```text
request message count
tool count
prompt char estimate
stream true/false
provider status code
frame type/count
generated tool name
argument key names, но не значения
retry count/reason class
chat rotation generation
latency
```

Не логируйте:

- Authorization;
- Cookie;
- refresh/access token;
- полный tool input;
- tool output из credential file;
- raw HAR;
- пароль в process command line.

Полезные metrics:

```text
http_requests_total
auth_failures_total
upstream_retries_total
upstream_transport_errors_total
paced_requests_total
pacing_wait_ms_total
empty_completions_total
tool_calls_total
chat_rotations_total
http_request_latency_ms_average
```

## 20. Диагностическое дерево

### Модель отвечает как чат и не выполняет задачу

1. Проверить, что Claw отправил non-empty `tools`.
2. Проверить число tools и schemas в system prompt.
3. Проверить provider native tool registration.
4. Проверить strict action/informational classification.
5. Проверить, что SSE tool delta соответствует OpenAI формату.
6. Проверить, что Claw выполнил tool и вернул result.

### Модель придумывает имя инструмента

1. Записать только имя и argument keys.
2. Проверить, было ли точное имя в registry.
3. Выполнить corrective retry в том же chat.
4. Добавлять explicit alias только для устойчиво наблюдаемой формы.
5. Не включать fuzzy suffix matching.

### Повторяется один и тот же запрос

1. Проверить, открывается ли SSE сразу.
2. Проверить HTTP client timeout Claw.
3. Проверить duplicate request fingerprints.
4. Проверить concurrency semaphore.
5. Проверить, не создаёт ли correction новый chat.

### Длинная работа ухудшается

1. Сравнить characters, estimated tokens и provider usage.
2. Проверить auto-compaction до и между tool iterations.
3. Снизить practical threshold, не hard context.
4. Проверить размер newest/older tool results.
5. Проверить, что summary сохраняет unresolved objective.

### Shell tool зависает

1. Проверить finite timeout.
2. Проверить дерево процессов.
3. Проверить inherited stdout/stderr handles.
4. Проверить foreground/background semantics.
5. Проверить stdin и интерактивную аутентификацию.

## 21. Критерии готовности GLM-интеграции

Интеграция не считается готовой, пока не выполнены все пункты:

- [ ] новый Claw-сеанс создаёт новый логический GLM chat;
- [ ] turns одного сеанса продолжают тот же диалог;
- [ ] text streaming корректен;
- [ ] local API требует bearer key;
- [ ] сессия обновляется без ручного login на каждый запрос;
- [ ] все реальные tools Claw зарегистрированы;
- [ ] informational вопрос не вызывает shell;
- [ ] action request вызывает реальный tool;
- [ ] минимум один read/write/edit/shell сценарий прошёл end-to-end;
- [ ] неизвестные tool names не исполняются;
- [ ] длинная задача проходит auto-compaction;
- [ ] timeout не оставляет процессы;
- [ ] rate pacing доказан измерением стартов;
- [ ] Windows и WSL пути обрабатываются корректно;
- [ ] токены/cookies отсутствуют в Git diff;
- [ ] unit, Windows, static и live tests зелёные;
- [ ] ограничения конкретной модели документированы честно.

## 22. Карта текущего Kimi-проекта

| Файл | Назначение |
| --- | --- |
| `src/kimi_api_server.py` | transport, session, OpenAI facade, tools, pacing |
| `tests/test_kimi_api_server.py` | offline и contract regressions |
| `windows/import-edge-session.ps1` | импорт авторизованной Edge session |
| `windows/protect-session.ps1` | DPAPI migration |
| `windows/set-private-acl.ps1` | приватные ACL gateway state |
| `windows/start-kimi-gateway.ps1` | проверенный Windows profile |
| `windows/claw-kimi.cmd` | изолированный Claw launcher |
| `README.md` | эксплуатация и актуальный Kimi contract |

Целевая Windows-копия Claw содержит дополнительные generic runtime changes:

| Файл | Изменение |
| --- | --- |
| `rust/crates/runtime/src/bash.rs` | stdin и process-tree timeout |
| `rust/crates/runtime/src/conversation.rs` | mid-loop compaction |
| `rust/crates/tools/src/lib.rs` | PowerShell stdin/output/process handling |
| `claw-kimi.cmd` | provider marker и practical compaction threshold |

## 23. Рабочий порядок при поломке web API

Если provider изменил протокол:

1. Не менять parser наугад.
2. Сохранить status, frame type и безопасные метаданные ошибки.
3. Повторить минимальный браузерный probe.
4. Снять новый redacted fixture.
5. Сравнить старый и новый request/response contracts.
6. Сначала добавить failing regression test.
7. Исправить минимальный provider-specific слой.
8. Прогнать offline suite.
9. Прогнать один text live smoke.
10. Только затем запускать agent/tool matrix.
11. Обновить documentation с датой и ограничениями.

Такой процесс позволяет использовать опыт Kimi для GLM, не превращая gateway в
набор task-specific костылей и не полагаясь на нестабильные догадки.
