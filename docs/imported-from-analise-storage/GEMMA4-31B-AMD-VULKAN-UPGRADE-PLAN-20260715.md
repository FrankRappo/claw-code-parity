# Gemma 4 31B: перенос ускоряющих патчей на AMD/Vulkan

**Статус:** актуализированный план; high-resolution Vision 1120 внедрён в production

**Рабочая копия:** `/work/claster/gemma431b_upgrade/README.md`

**Версионируемая копия:** этот файл в `analise_storage`

**Дата первичной фиксации:** 2026-07-15

**Последнее обновление:** 2026-07-20

**Основание обновления:** публикации Unsloth и Google Gemma, официальный commit
Google с новым chat template, production A/B Vision 280/1120 и повторная
проверка открытых AMD/Vulkan-кандидатов для llama.cpp, live ASPM/MCLK probe и
end-to-end проверка Claw Cod на VM120.

**Цель:** ускорить текущую Gemma 4 31B Abliterated Q4_K_M на двух AMD GPU
без смены основной модели и без измеримой потери качества.

Этот каталог предназначен для будущей отдельной ветки работ. Production нельзя
модифицировать непосредственно: все новые ядра сначала собираются в параллельный
runtime, проходят correctness-gates и только затем допускаются к A/B-тесту.

## 1. Что именно опубликовано в сети

### 1.1. Fable 5 / Xenova: около 255 токенов/с

- Пост: <https://x.com/xenovacom/status/2067289897111638484>
- Демонстрация и исходники:
  <https://huggingface.co/spaces/webml-community/gemma-4-webgpu-kernels>
- Дерево файлов:
  <https://huggingface.co/spaces/webml-community/gemma-4-webgpu-kernels/tree/main>
- Основной JS/WGSL bundle:
  <https://huggingface.co/spaces/webml-community/gemma-4-webgpu-kernels/blob/main/gemma-4-e2b.js>
- Описание и часть вариантов ядер:
  <https://huggingface.co/spaces/webml-community/gemma-4-webgpu-kernels/blob/main/index.html>

Это эксперимент для Gemma 4 **E2B**, WebGPU/WGSL и Apple M4 Max. Метрика —
decode-only после первого токена. Код не является готовым патчем для нашей 31B,
AMD Vulkan или распределённого llama.cpp RPC. В Space не указана ясная лицензия
исходного кода, поэтому его нельзя просто копировать в проект. Пока лицензия не
прояснена, использовать только опубликованные идеи и результаты, а реализацию
вести clean-room способом.

### 1.2. Fast Gemma Challenge

- Пост Google Gemma: <https://x.com/googlegemma/status/2075611948985835877>
- Challenge: <https://huggingface.co/gemma-challenge>
- Lossless submission, около 315 TPS:
  <https://huggingface.co/buckets/gemma-challenge/gemma-main-bucket/resolve/results/20260610-153213-507_blake-fable5-1.md>
- Более быстрый, но quality-regressing submission, около 492 TPS:
  <https://huggingface.co/buckets/gemma-challenge/gemma-main-bucket/resolve/results/20260616-001236-372_byteshark.md>

Challenge использовал Gemma 4 E4B, одну NVIDIA A10G, vLLM, CUDA и Triton.
Применимы принципы профилирования, fusion и MTP; CUDA Graph/Triton-код напрямую
на AMD/Vulkan не переносится. Нельзя переносить lossful-оптимизации: уменьшение
sliding window, vocabulary/lm_head pruning и приближённый centroid top-k.

### 1.3. Уже применимые upstream-изменения llama.cpp

- Gemma 4 MTP: <https://github.com/ggml-org/llama.cpp/pull/23398>
- Описание MTP Google: <https://ai.google.dev/gemma/docs/mtp/mtp>
- Speculative decoding:
  <https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md>
- Multimodal/Gemma 4 vision:
  <https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md>
- RPC и предупреждения по безопасности:
  <https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md>

MTP — наиболее прямой путь к ускорению 31B без изменения результатов target
model: draft предлагает токены, target их проверяет. Но ускорение не гарантировано
на Vulkan/RPC и зависит от acceptance rate.

### 1.4. Конкретные downloadable patches на 2026-07-15

Поддержка Gemma 4 MTP уже включена в `b10012`; эти merged patches повторно
накладывать нельзя:

- generic MTP, merged: <https://github.com/ggml-org/llama.cpp/pull/22673.patch>
- Gemma 4 MTP, merged: <https://github.com/ggml-org/llama.cpp/pull/23398.patch>
- Gemma 4 assistant, merged: <https://github.com/ggml-org/llama.cpp/pull/24282.patch>

Открытые Vulkan-кандидаты:

1. AMD speculative-decode MMVQ fix/performance:
   <https://github.com/ggml-org/llama.cpp/pull/25666> и
   <https://github.com/ggml-org/llama.cpp/pull/25666.patch>. Патч чисто
   накладывается на `b10012`; upstream измерил примерно +12.9% на gfx1151/RADV,
   но discrete GFX1200 и RPC не проверялись. Это первый кандидат для отдельного
   single-GPU эксперимента, не для немедленного production rollout.
2. MTP shared-KV diagnostic:
   <https://github.com/ggml-org/llama.cpp/pull/25232> и
   <https://github.com/ggml-org/llama.cpp/pull/25232.patch>. Он применим к
   `b10012`, но не ускоряет и не реализует cross-backend KV sharing — только
   заменяет аварийное завершение понятной диагностикой.
3. Q4_K/Q5_K/Q6_K Vulkan A-transpose:
   <https://github.com/ggml-org/llama.cpp/pull/22970>. Заявлен прирост prompt
   processing около 5–8%, decode почти не меняется. Текущий patch конфликтует с
   `b10012`, поэтому потребуется ручной port, а не `git apply`.
4. Vulkan command-buffer graph reuse:
   <https://github.com/ggml-org/llama.cpp/pull/24720>. На `b10012` конфликтует,
   а автор не получил практического ускорения; низкий приоритет.

Известный блокер MTP в нашей топологии:
<https://github.com/ggml-org/llama.cpp/issues/24492>. Cross-backend shared KV для
Gemma 4 31B остаётся проблемным. Поэтому MTP сначала проверяется на одном GPU;
двуххостовый MTP нельзя считать production-ready только потому, что draft GGUF
успешно загрузился.

### 1.5. Обновление Unsloth/Google от июля 2026 года

Источники:

- публикация Unsloth:
  <https://x.com/UnslothAI/status/2078118183085731843>;
- официальный commit Google:
  <https://huggingface.co/google/gemma-4-31B-it/commit/68abe48010cbe15293462fa11e901a60639a44e5>;
- изменённый файл:
  <https://huggingface.co/google/gemma-4-31B-it/blob/68abe48010cbe15293462fa11e901a60639a44e5/chat_template.jinja>.

Это **не новая ревизия весов Gemma 4 31B**. В указанном официальном commit
изменён `chat_template.jinja`: обработка `null`, проверка входных данных,
балансировка turn tags, сохранение reasoning/thinking, tool calls, tool responses
и continuation turns. Поэтому исправление применимо и к нашей abliterated-модели
без повторного квантования весов.

Для нашей сборки правильный путь — сохранить канонический template отдельным
versioned-файлом и передавать его через поддерживаемый `b10012` параметр
`--chat-template-file`. Перекачивать чужой Unsloth GGUF только ради шаблона не
нужно: это заменило бы нашу модель и затруднило бы проверку происхождения весов.

Заявленные Unsloth улучшения нельзя считать единым AMD-патчем:

- chat/tool/reasoning fixes аппаратно независимы и переносятся;
- Flash Attention 4 относится к NVIDIA Hopper и напрямую на Vulkan/RDNA4 не
  переносится;
- NVFP4 относится к NVIDIA Blackwell и не является форматом наших AMD GPU;
- цифры `25–70%` для prefill и до `31%` для TTFT должны рассматриваться как
  результаты другого software/hardware path, а не как прогноз для нашей RPC
  конфигурации;
- изменение vision token budget — отдельный quality/latency trade-off, а не
  бесплатное ускорение и не должно смешиваться с обновлением шаблона.

Обновление шаблона — **P0 correctness/reliability change**, но не обещание роста
decode TPS. Оно должно пройти отдельный A/B на одинаковых весах и runtime.

### 1.6. Что из опубликованного реально делать на RX 9060 XT

| Кандидат | Переносимость | Решение для нашей системы |
|---|---|---|
| Официальный `chat_template.jinja` | прямая, GPU-независимая | P0: внедрить отдельным файлом, проверить chat/tool/vision и оставить мгновенный rollback |
| PR `#25666`, AMD speculative MMVQ | условная | тестировать только после рабочего single-GPU MTP; затем отдельно проверять GFX1200 и RPC |
| PR `#22970`, K-quant A-transpose | условная | портировать только если exact staging driver/runtime действительно экспонирует cooperative matrix |
| PR `#25232` | диагностическая | использовать для понятной ошибки MTP, не учитывать как ускорение |
| PR `#24720`, graph reuse | технически возможна | низкий приоритет: upstream не показал практического выигрыша |
| Hopper FA4 | прямого порта нет | использовать только алгоритмические идеи; нужна новая GLSL/SPIR-V реализация и профилирование |
| Blackwell NVFP4 | прямого порта нет | не переносить; исследовать собственные INT4/Q4_K paths RDNA4 |
| Fable5 WebGPU/WGSL kernels | только clean-room идеи | не копировать без ясной лицензии; переписывать layout, wave64, tiles и dispatch |
| KTransformers CPU+GPU | архитектурно не подходит текущей dense 31B | не менять runtime и не включать RAM cache; использовать только идеи profiling/dispatch |
| Vision budget `560/1120` | прямая настройка, без смены весов | внедрён диапазон `280–1120`, batch limits `1280`, `cache-ram=0`; A/B и rollback задокументированы |
| Собственные Q4_K MMVQ/DMMV kernels | да, разработкой | главный кандидат для decode после профилирования top bottlenecks |

Итого: быстрый и безопасный результат — новый шаблон; ближайшие performance
кандидаты — capability-gated `#22970`, MTP + `#25666` на одном GPU и собственный
autotuning/Q4_K path. NVIDIA-патчи не накладываются на AMD как готовый diff.

### 1.7. KTransformers и публикация о 2.8T Kimi

Источники:

- публикация: <https://x.com/alex_verem/status/2078202294253039715>;
- официальный KTransformers:
  <https://github.com/kvcache-ai/ktransformers>;
- официальный Kimi-K2 tutorial:
  <https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/Kimi-K2.md>;
- beta ROCm tutorial:
  <https://github.com/kvcache-ai/ktransformers/blob/main/doc/en/ROCm.md>;
- текущий KT-Kernel support/status:
  <https://github.com/kvcache-ai/ktransformers/blob/main/kt-kernel/README.md>;
- текущий model registry:
  <https://github.com/kvcache-ai/ktransformers/blob/main/kt-kernel/python/cli/utils/model_registry.py>;
- AMD/ROCm CI placeholder:
  <https://github.com/kvcache-ai/ktransformers/blob/main/kt-kernel/test/per_commit/test_amd_placeholder.py>;
- host-memory prompt cache в pinned llama-server:
  <https://github.com/ggml-org/llama.cpp/blob/c71854292f7c367cc3b35939f88121d81945472f/tools/server/README.md>;
- известное ограничение cache reuse у Gemma 4:
  <https://github.com/ggml-org/llama.cpp/issues/21468>.

Публикация не описывает патч для Gemma или llama.cpp. KTransformers решает другую
задачу: запуск очень больших **Mixture-of-Experts** моделей, когда все эксперты не
помещаются в VRAM. Холодные experts размещаются в системной RAM/CPU, горячие — на
GPU, а выполнение перекрывается и планируется между устройствами.

Для текущей Gemma 4 31B это не ускоритель по следующим причинам:

1. Наша 31B — dense-модель без набора routed experts; основной механизм
   KTransformers hot/cold expert placement к ней неприменим.
2. Текущий `kt-kernel` registry ориентирован на MoE-семейства DeepSeek, Kimi и
   MiniMax; готового Gemma 4 backend/optimize rule в официальном репозитории нет.
3. Основной GPU path и готовые performance wheels ориентированы на CUDA. В
   текущей документации `AMD BLIS` означает прежде всего ускорение **AMD CPU**,
   а не поддержку нашей RX 9060 XT.
4. ROCm path помечен beta, проверялся на Radeon 7900 XTX с DeepSeek-R1; Marlin
   там не поддерживается, а Q8 linear прямо отмечен как suboptimal. Это не
   доказательство работы или выигрыша на RDNA4/GFX1200.
5. В текущем дереве `kt-kernel` AMD/ROCm CI test остаётся placeholder, поэтому
   нельзя считать gfx1200 production-qualified только по наличию build flags.
6. Официальный пример Kimi-K2 Q4_K_M указывает примерно 600 GB DRAM и около
   10 TPS на consumer GPU. В нашей доступной топологии RAM на порядок меньше, а
   2.8T-вариант потребует ещё больше памяти; две 16 GiB карты это не компенсируют.
7. Наша Gemma уже помещается в совокупную VRAM двух GPU. Перенос dense layers
   обратно в CPU RAM добавит PCIe/RPC transfers и, вероятнее всего, снизит TPS.

Полезны только идеи, которые можно проверять независимо внутри llama.cpp:

- аппаратно-зависимый dispatch и operator injection через feature flags;
- CPU/GPU overlap — только если profiling докажет реальную независимую работу;
- NUMA-aware thread placement для CPU-side preprocessing;
- многоуровневый prefix/prompt cache для повторяющихся Telegram/system prompts;
- раздельное измерение hot/cold paths и сохранение воспроизводимых profiles.

Из применимых идей оставляются только profiling, hardware-aware dispatch и
проверка возможности CPU/GPU overlap. Переносить KTransformers runtime или его
MoE scheduler не требуется.

**Решение от 2026-07-20:** host-memory prompt cache не включать. В production и
staging сохранять `--cache-ram 0`. Возможное уменьшение TTFT повторных запросов не
компенсирует занятую RAM и дополнительный риск OOM; decode TPS эта функция почти
не повышает. К вопросу можно возвращаться только после увеличения RAM и отдельного
пересмотра memory budget.

Решение: KTransformers не включать в production и не считать performance
кандидатом для Gemma 4 31B. Оставить как отдельный future research track для
MoE-моделей только после появления официальной поддержки нужной модели,
доказанной ROCm/gfx1200 совместимости и сервера с сотнями гигабайт RAM.

### 1.8. High-resolution Vision: 560/1120 image tokens

Источники:

- публикация Google Gemma про sharp OCR и `max_soft_tokens=1120`:
  <https://x.com/googlegemma/status/2077449157254738227>;
- официальная документация Google по image token budgets:
  <https://ai.google.dev/gemma/docs/capabilities/vision/image>;
- официальный model card Gemma 4 31B:
  <https://huggingface.co/google/gemma-4-31B-it>;
- Google proposal сменить default с 280 на 1120:
  <https://huggingface.co/google/gemma-4-31B-it/discussions/131>;
- текущий официальный `config.json`:
  <https://huggingface.co/google/gemma-4-31B-it/blob/main/config.json>;
- image-token параметры pinned llama-server:
  <https://github.com/ggml-org/llama.cpp/blob/c71854292f7c367cc3b35939f88121d81945472f/tools/server/README.md>;
- известный crash-report для fixed 1120:
  <https://github.com/ggml-org/llama.cpp/issues/21550>.

Gemma 4 официально поддерживает visual token budgets `70`, `140`, `280`, `560`
и `1120`. Высокие значения предназначены для OCR, документов, рукописного и
мелкого текста. Это не новые веса и не GPU-патч: image encoder сохраняет больше
визуальных деталей ценой дополнительного compute, context tokens и latency.

На 2026-07-20 proposal `#131`, меняющий default `280 → 1120`, помечен как ready
to merge, но ещё не слит. В `main` Google `config.json` и
`processor_config.json` всё ещё указано `280`. Поэтому нельзя считать, что
перекачивание текущих весов автоматически активирует high-resolution mode.

Наша pinned llama.cpp поддерживает `--image-min-tokens` и
`--image-max-tokens`, а также ограничение image batch
`--mtmd-batch-max-tokens`. Поэтому режим внедрён с существующими target GGUF и
`mmproj`, без повторного квантования.

Предыдущий baseline использовал `--batch-size 512 --ubatch-size 512`. Для 1120
этого недостаточно: Gemma 4 использует non-causal image attention, и полный
image-token chunk должен помещаться в batch. Production launcher теперь задаёт
диапазон `280–1120`, а `batch-size`, `ubatch-size` и
`mtmd-batch-max-tokens` равны `1280`. Он отклоняет несогласованные значения до
запуска сервера.

Фактическая последовательность внедрения 2026-07-20:

1. зафиксирован baseline `280/512/512` и rollback launcher;
2. проверен candidate `280–1120/1280/1280/1280` с health timeout и
   автоматическим rollback;
3. выполнен одинаковый OCR и RAM A/B, после замера baseline был восстановлен;
4. владелец принял измеренные `+501.9 MiB` working RAM и рост dense latency;
5. тот же candidate повторно установлен как production default и проверен через
   Telegram path.

Fixed `1120/1120` не используется: диапазон сохраняет адаптивность и не повторяет
конфигурацию известного crash-report на другой backend/hardware комбинации.

Это не включает `cache-ram` и не создаёт постоянный RAM cache. Однако большие
batch/ubatch могут увеличить временные рабочие буферы и peak VRAM/RAM, поэтому
решение `--cache-ram 0` сохраняется, а memory telemetry остаётся обязательной.

Для Telegram максимальное качество исходника достигается, когда скриншот
отправлен как PNG/JPEG **документ**, а не как photo: Telegram photo может быть
пережат до того, как изображение попадёт в модель. Текущий бот уже принимает оба
варианта и не выполняет собственного resize.

Измеренный результат одного dense PNG fixture: prompt `414 → 1247` tokens,
latency `20.33 → 30.54 s`, мелкая дата исправилась с ошибочного `07-29` на
ожидаемое `07-20`. Cgroup во время OCR вырос с `375.6` до `877.5 MiB`, swap не
изменился, минимальный `MemAvailable` составил `35.77 GiB`, OOM/GPU error/restart
не было. Полный протокол, fixture SHA-256, operations и rollback:
`/work/claster/analise_storage/GEMMA4-31B-HIGH-RES-VISION-1120-20260720.md`.

### 1.9. Дополнительные ускорения на тех же весах и том же железе

После production rollout Vision 1120 повторно проверены настройки хостов и
актуальные upstream-кандидаты. Это отдельный performance track: ни один пункт
ниже не требует замены Gemma 4 31B или включения host-memory prompt cache.

Источники:

- практическое исследование RDNA4/RADV, включая ASPM, Mesa, `rm_kq` и batch:
  <https://github.com/ggml-org/llama.cpp/discussions/21043>;
- официальная документация RPC и `--tensor-split`:
  <https://github.com/ggml-org/llama.cpp/blob/master/tools/rpc/README.md>;
- официальная документация speculative/ngram decoding:
  <https://github.com/ggml-org/llama.cpp/blob/master/docs/speculative.md>;
- официальный Radeon compatibility matrix ROCm 7.2:
  <https://rocm.docs.amd.com/projects/radeon-ryzen/en/docs-7.2/docs/compatibility/compatibilityrad/native_linux/native_linux_compatibility.html>;
- обсуждение HIP/ROCm в llama.cpp:
  <https://github.com/ggml-org/llama.cpp/discussions/15021>;
- текущие официальные releases llama.cpp:
  <https://github.com/ggml-org/llama.cpp/releases>.

#### Фактический live snapshot 2026-07-20

На `srv1` зафиксированы kernel `6.17.2-1-pve`, Mesa/RADV `25.0.7-2`, GPU
`0000:03:00.0`, link `32 GT/s x16`, `ASPM L1 Enabled`,
`power_dpm_force_performance_level=high`. ASPM policy оставалась
`[default] performance powersave powersupersave`, профиль GPU —
`BOOTUP_DEFAULT`. Максимальный DPM state памяти равен `1258 MHz`, но во время
непрерывной генерации 256 токенов MCLK держалась около `1124 MHz`.

Контрольный запрос: 35 prompt tokens, 256 completion tokens, `22.468 s`, то есть
примерно `11.39 ток/с`. Это не доказывает причину ограничения MCLK, но даёт
воспроизводимую точку для A/B. Внешний результат из discussion `#21043` получен
на другой RDNA4-карте (`R9700/gfx1201`), поэтому его `+10.8%` decode от ASPM
`performance` является основанием для теста, а не прогнозом для RX 9060 XT.

#### Приоритет кандидатов

| Приоритет | Кандидат | Что может улучшить | Ограничения и решение |
|---|---|---|---|
| P0 | ASPM `performance` + проверка MCLK на обоих GPU | single-request decode без смены математики | отдельный A/B с power/temperature/MCLK/TPS; при отсутствии стабильного выигрыша вернуть `default` |
| P0 | Асимметричный layer split `1.1/0.9`, затем `1.2/0.8` | убрать часть нагрузки с более слабого CPU/RPC host `srv2` | сравнивать с `1/1`, следить за VRAM обеих карт; exact greedy output должен совпасть |
| P1 | Отдельная HIP/ROCm 7.2 staging-сборка | потенциально prefill, vision encoder и отдельные kernels | не заменять production Vulkan; RX 9060 XT официально есть в matrix, но выигрыш llama.cpp/RPC не доказан |
| P1 | `ngram-mod` для повторяющегося кода/текста | decode без отдельной draft-модели | около 16 MiB state, не prompt cache; обычный чат может не ускориться, default оставить выключенным |
| P1 | `parallel=2` при реальной очереди Claw/Telegram | суммарную пропускную способность двух одновременных запросов | не ускоряет один запрос; при total context 32K каждый slot получит примерно 16K, поэтому не включать для длинных agent-сессий без отдельного теста |
| P2 | `rm_kq=1`, `ubatch=2048`, explicit flash-attn | небольшой prefill/decode эффект | внешний RADV-эффект около `1%` для `rm_kq` и около `3%` prefill для `ubatch`; дополнительный memory pressure, низкий приоритет |
| P2 | staged upgrade `b10012 ->` новая release | исправления и новые kernels | на 2026-07-20 release `b10069`; только pinned candidate с теми же model/template/RPC binaries и полным regression suite |

ROCm candidate собирать отдельно, ориентировочно с
`-DGGML_HIP=ON -DGGML_RPC=ON -DGGML_HIP_ROCWMMA_FATTN=ON`. Наличие флага и
официальной поддержки GPU не доказывает, что HIP быстрее RADV именно на dense
Q4_K_M decode. Сначала single-GPU benchmark, затем одинаковые версии server и
RPC worker на двух хостах.

`ngram-mod` не нарушает принятое решение `--cache-ram 0`: это небольшой
спекулятивный индекс текущей генерации, а не сохранение model/prompt cache в RAM.
Однако он остаётся opt-in, потому что владелец явно выбрал минимальное
потребление RAM и польза зависит от повторяемости agent-задачи.

Реалистичный ближайший потолок от системных настроек — ориентировочно **+5–20%**
к текущим ~`11.6 ток/с`, то есть примерно `12.2–13.9 ток/с`. Эффекты не следует
складывать арифметически: ASPM, split и backend могут устранять один и тот же
bottleneck. Более высокий диапазон `15–20 ток/с` остаётся целью kernel/MTP
ветки, а не обещанием от одних параметров запуска.

Порог **+50% single-request decode** означает примерно `17.4 ток/с`. Он возможен
как stretch goal только при доказанном крупном выигрыше от MTP, собственного
Q4_K kernel или другого backend вместе с системной настройкой; ASPM и tensor
split сами по себе такой результат не обещают. `parallel=2` может приблизить
рост aggregate throughput к 50% и выше при двух одновременных запросах, но не
ускоряет один ответ и сокращает доступный context каждого slot.

#### Context + parallel capacity probe

Официальный нативный text context Gemma 4 31B равен 256K. Для двух полностью
заполненных slots текущему llama-server нужен общий `ctx-size=524288`. Этот
maximum profile проверен 2026-07-20:

```text
CTX_SIZE=524288, PARALLEL=2
health=OK, total_slots=2, context_per_slot=262144
srv1 VRAM free after startup=75.5 MiB
```

Два slots по 256K технически создаются, но VRAM reserve недостаточен для
production и Vision 1120. Candidate отклонён до нагрузки, backup восстановлен,
production снова `32K/p1`, health OK, `NRestarts=0`. После возобновления тестов
проверять по убыванию 192K, 160K и 128K на slot с single/dual TPS, long prompt,
Vision и Claw tool gates. Полный протокол:
`/work/claster/analise_storage/GEMMA4-CLAW-CONTEXT-PARALLEL-PROBE-20260720.md`.

Не использовать как ускорение текущей системы:

- AMDVLK вместо RADV: в опубликованном RDNA4 A/B dense prefill заметно хуже;
- `split-mode row` через RPC без отдельного доказательства корректности и пользы;
- RPC local model cache: он ускоряет загрузку, но не inference;
- RDMA без замены существующего Realtek 2.5GbE оборудования;
- CPU/RAM offload dense layers: модель уже помещается в суммарную VRAM и такой
  offload, вероятнее всего, добавит передачи вместо ускорения.

#### Порядок безопасного A/B

1. Сохранить baseline command line, clocks, power, temperatures, per-GPU VRAM,
   prompt TPS и decode TPS минимум для трёх прогонов.
2. Отдельно проверить ASPM policy, не меняя split/runtime; затем cold reboot и
   повторить, чтобы исключить случайное DPM state.
3. На выигравшей policy проверить `1/1`, `1.1/0.9`, `1.2/0.8`; остановиться при
   OOM, GPU reset или VRAM reserve меньше принятого порога.
4. HIP/ROCm, ngram и новая llama.cpp release тестировать независимыми candidates,
   не смешивая изменения в одном прогоне.
5. Для каждого победителя требовать exact output/correctness gates, Telegram и
   Claw tool-call smoke, затем 6–8-часовой RPC burn-in.
6. Production сохраняет `--cache-ram 0`; rollback каждого этапа — возврат одного
   параметра или systemd unit к сохранённому baseline.

Текущая совместимость установленного Claw Cod, два пройденных tool-call smoke и
ограничение image transport зафиксированы в
`/work/claster/analise_storage/CLAW-COD-GEMMA4-COMPATIBILITY-20260720.md`.

## 2. Текущая исходная точка

Baseline зафиксирован 2026-07-15; capability snapshot дополнительно проверен
2026-07-20:

- target: `gemma-4-31b-abliterated-Q4_K_M.gguf`;
- runtime: официальный llama.cpp `b10012`, commit
  `c71854292f7c367cc3b35939f88121d81945472f`;
- GPU: две AMD Radeon RX 9060 XT 16 GiB, RDNA4/GFX1200;
- `srv1`: локальный `Vulkan0`;
- `srv2`: `RPC0` через приватный адрес `10.30.0.2:50053`;
- layer split `1,1`, context 32K, `parallel=1`, Q4 KV;
- vision: `mmproj-gemma-4-31B-it-Q8_0.gguf`;
- high-resolution vision: range `280–1120`, все batch limits `1280`,
  `--cache-ram 0`;
- оба GPU занимают примерно по 10–11 GiB VRAM;
- 10-case text benchmark: median decode около **11.57 ток/с**;
- screenshot-smoke корректно прочитал `SOKOL-4827` и `19 375 РУБ.`.

На проверенном production host используется Mesa `25.0.7`. Текущий runtime
сообщал `fp16=1`, `bf16=0`, `fp4=0`, `warp size=64`, `shared memory=65536`,
`int dot=1`, `matrix cores=none`; `VK_KHR_cooperative_matrix` и
`VK_NV_cooperative_matrix2` в текущем capability probe не экспонировались.
Следовательно, ускоряющая ветка PR `#22970` в текущем окружении, вероятнее всего,
не будет активирована.

При этом старый журнал
`analise_storage/logs/two-gpu-after-psu-vm100-rpc-service-20260528-195909.log`
содержит `matrix cores: KHR_coopmat`. Это важное расхождение: поддержку нельзя
определять только по названию GPU или старому логу. Gate должен проверять точную
комбинацию kernel/Mesa/ICD/llama.cpp binary, которая будет участвовать в A/B.

Mesa 25.0 отключала `VK_KHR_cooperative_matrix` на GFX12; актуальные возможности
RADV и последующие исправления необходимо проверять на staging, а не предполагать:

- <https://docs.mesa3d.org/relnotes/25.0.0.html>;
- <https://docs.mesa3d.org/drivers/radv.html>;
- <https://docs.mesa3d.org/relnotes.html>.

RDNA4 аппаратно поддерживает FP8/INT4 и sparsity, но наличие аппаратной функции
не гарантирует доступность нужного Vulkan extension в установленном Mesa/ICD:
<https://www.amd.com/en/technologies/rdna.html>.

Production-источники запуска, systemd units, Telegram vision bot и benchmark
находятся в:

```text
/work/claster/analise_storage/llm_ops/
```

Сторонний draft GGUF, созданный старым конвертером, имеет architecture
`gemma4_mtp`; `b10012` ожидает `gemma4_assistant`. Поэтому он не загружается,
MTP в production отключён. В будущем официальный
`google/gemma-4-31B-it-assistant` нужно конвертировать конвертером из точно того
же pinned llama.cpp commit и только затем бенчмаркировать.

## 3. Почему это не обычный port

| Fable 5 | Наша система |
|---|---|
| Gemma 4 E2B | Gemma 4 31B Abliterated Q4_K_M |
| Apple M4 Max | AMD GFX1200/RDNA, две карты |
| WebGPU/WGSL | GGML Vulkan/GLSL → SPIR-V |
| один процесс/один GPU | две машины через llama.cpp RPC |
| mobile wNa8o8/QAT | GGUF Q4_K_M target |
| decode-only demo | text, long context, screenshots, bot |

Переписывать нужно не синтаксис WGSL, а алгоритм ядра, layout данных, размеры
tile/workgroup, работу wave64, shared memory, quant unpacking, dispatch и fusion
границы. Кроме того, kernel speedup может быть скрыт сетевой задержкой RPC.

## 4. Безопасная структура будущего проекта

Создать отдельный Git-репозиторий или worktree, не изменяя production runtime:

```text
gemma431b_upgrade/
  README.md
  upstream/                 # pinned clean llama.cpp source
  patches/                  # небольшие независимые commits
  scripts/                  # build, deploy-to-staging, rollback
  benchmarks/
    corpus/
    baseline/
    candidates/
  correctness/
  reports/
```

Рекомендуемая база:

```bash
git clone https://github.com/ggml-org/llama.cpp.git upstream/llama.cpp
git -C upstream/llama.cpp checkout c71854292f7c367cc3b35939f88121d81945472f
git -C upstream/llama.cpp switch -c gemma431b-amd-vulkan

cmake -S upstream/llama.cpp -B upstream/llama.cpp/build-amd-vulkan \
  -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DGGML_VULKAN=ON \
  -DGGML_RPC=ON \
  -DLLAMA_BUILD_SERVER=ON
cmake --build upstream/llama.cpp/build-amd-vulkan -j
```

Server и RPC worker всегда должны быть собраны из одного commit. Перед запуском
на двух хостах зафиксировать SHA-256 бинарников и shared libraries.

## 5. Карта мест llama.cpp для оптимизации

Для pinned `b10012` основные файлы:

```text
src/models/gemma4.cpp
ggml/src/ggml-vulkan/ggml-vulkan.cpp
ggml/src/ggml-vulkan/vulkan-shaders/vulkan-shaders-gen.cpp
ggml/src/ggml-vulkan/vulkan-shaders/mul_mat_vec_q4_k.comp
ggml/src/ggml-vulkan/vulkan-shaders/mul_mat_vec_base.glsl
ggml/src/ggml-vulkan/vulkan-shaders/dot_product_funcs.glsl
ggml/src/ggml-vulkan/vulkan-shaders/mul_mm*.comp
ggml/src/ggml-vulkan/vulkan-shaders/mul_mmq*.comp
ggml/src/ggml-vulkan/vulkan-shaders/flash_attn*.comp
ggml/src/ggml-vulkan/vulkan-shaders/rms_norm*.comp
ggml/src/ggml-vulkan/vulkan-shaders/rope*.comp
ggml/src/ggml-vulkan/vulkan-shaders/argmax.comp
```

Приоритет определяется профилированием, но для Q4_K_M decode первым кандидатом
обычно будет quantized matrix-vector path. Per-layer embeddings Gemma 4 находятся
в `src/models/gemma4.cpp`; их fusion потребует не только нового shader, но и
изменения построения/исполнения GGML graph.

## 6. Пошаговый план переписывания

### Этап 0. Зафиксировать baseline

1. Снять отдельные результаты для:
   - только `srv1` GPU;
   - только `srv2` GPU;
   - двух GPU через RPC.
2. Измерять отдельно prefill и decode.
3. Записать model/runtime hashes, Vulkan driver, Mesa version, clocks, VRAM,
   контекст, KV type, sampler и RPC link state.
4. Использовать минимум 20 повторяемых text cases, OCR screenshot suite,
   длинный контекст и declarant regression corpus.
5. Выполнить warm-up до записи результатов.
6. Сохранить capability snapshot для каждого хоста и каждого candidate runtime:

   ```bash
   vulkaninfo --summary
   vulkaninfo | grep -E 'VK_(KHR|NV)_cooperative_matrix|subgroupSize|driverInfo'
   ```

7. Сохранить строку `ggml_vulkan` из startup log. PR `#22970` не тестировать как
   performance candidate, если сборка не сообщает активный cooperative-matrix
   path.

Существующий воспроизводимый text harness:

```text
/work/claster/analise_storage/llm_ops/benchmarks/benchmark_openai.py
```

### Этап 0A. Обновить chat template без замены весов

1. Сохранить официальный `chat_template.jinja` из commit `68abe480...` в
   staging-конфигурации и записать SHA-256.
2. Передать файл в `llama-server` через `--chat-template-file`; не встраивать его
   вручную в GGUF на первом A/B.
3. Зафиксировать до/после rendered prompt и token IDs для одинаковых сообщений.
4. Проверить минимум: обычный chat, `null`, system/developer message, один и
   несколько tool calls, tool response, continuation, reasoning on/off, история
   разговора и multimodal request.
5. Запустить Telegram bot smoke suite и убедиться, что ответы не содержат сырых
   `<|turn>`, `<|channel>` и `<tool_call>` tokens.
6. Измерить prefill/TTFT отдельно, но принимать обновление прежде всего по
   correctness. Rollback — убрать один аргумент/вернуть предыдущий template file.

### Этап 0B. RAM prompt cache: закрытый кандидат

1. В production и staging сохранять `--cache-ram 0`.
2. Не выделять `512–1024 MiB` под host-memory prompt cache при текущем memory
   budget.
3. Не устанавливать KTransformers и не добавлять отдельный caching layer.
4. Повторно открывать эксперимент только после увеличения RAM, нового baseline
   RSS/available memory и отдельного решения о допустимом memory budget.
5. Это решение не запрещает обычное повторное использование KV внутри активного
   server slot, если оно не создаёт отдельный host-memory cache.

### Этап 0C. High-resolution OCR A/B — production rollout выполнен

Выполнено 2026-07-20:

```text
--image-min-tokens 280
--image-max-tokens 1120
--batch-size 1280
--ubatch-size 1280
--mtmd-batch-max-tokens 1280
--cache-ram 0
```

Зафиксированы correctness, latency, prompt tokens, cgroup RAM, system
`MemAvailable`, swap, health, restarts и журналы OOM/GPU errors. Candidate
исправил один мелкий числовой OCR field, не сломал крупные поля, не увеличил
swap и оставил `15.77 GiB` сверх принятого 20 GiB RAM reserve. После
контролируемого возврата к 280 владелец одобрил production rollout; повторный
end-to-end Telegram smoke прошёл.

Оставшаяся работа — расширить frozen OCR corpus и выполнить длительный mixed
text/image burn-in. Rollback — установить `min=max=280`, вернуть все три batch
limits к `512` и перезапустить только primary unit. Полный протокол находится в
`GEMMA4-31B-HIGH-RES-VISION-1120-20260720.md`.

### Этап 1. Найти реальный bottleneck

1. Запустить `llama-bench` для prompt/decode и JSONL-вывода.
2. Сравнить один GPU и RPC: если combined почти не быстрее локального режима,
   сначала измерить сеть и количество межузловых transfer.
3. Получить GPU timestamp/profiling данные для Vulkan dispatch.
4. Ранжировать операции по доле decode time.
5. Не начинать fusion до доказательства, что выбранные kernels действительно
   находятся в top-3 bottlenecks.

### Этап 1A. Driver/cooperative-matrix gate

1. Не обновлять Mesa глобально на production ради эксперимента.
2. Подготовить отдельный staging host, image или изолированный userspace stack с
   более новой стабильной Mesa; на обоих RPC-хостах использовать совместимые
   runtime/ICD и одинаковый llama.cpp commit.
3. Повторить capability snapshot и backend correctness tests.
4. Только при фактически доступном cooperative-matrix path вручную перенести PR
   `#22970` на pinned `b10012` и измерить Q4_K prefill.
5. Если extension отсутствует, закрыть этот кандидат как `not applicable` для
   данной комбинации драйвера, а не пытаться принудительно включить shader.
6. Любые GPU reset/device-lost или расхождения token output означают немедленный
   возврат к production Mesa/ICD.

### Этап 2. Autotuning без изменения математики

Это самый дешёвый и безопасный этап:

1. Добавить варианты workgroup/tile размеров для GFX1200.
2. Проверить wave64, 64 KiB shared memory и integer-dot capabilities через
   Vulkan device properties, не hardcode по имени карты.
3. Генерировать набор shader variants на build-time.
4. При первом staging-запуске измерять варианты на фиксированных Gemma 4 shapes.
5. Сохранять выбранный вариант в hardware-keyed cache.
6. Всегда иметь generic fallback.

Каждый variant — отдельный маленький commit; не смешивать matvec, attention и
runtime-dispatch в одном изменении.

### Этап 3. Q4_K matrix-vector decode kernels

1. Начать с `mul_mat_vec_q4_k.comp` и связанных GLSL helpers.
2. Переразложить quant unpacking и dot products под wave64.
3. Минимизировать divergent branches и повторное чтение scale/min metadata.
4. Проверить coalesced reads для фактических 31B tensor shapes.
5. Подбирать register pressure против occupancy, а не максимизировать tile вслепую.
6. Сравнивать с generic shader и автоматически откатываться для неподходящих
   shapes/devices.

### Этап 4. Gemma-specific fusion

Выполнять только после успешного Этапа 3:

1. QKV projection + последующие простые transformations;
2. Q/K norm + RoPE;
3. RMSNorm + соседние scale/add;
4. gate/up + activation;
5. per-layer embedding projection/norm/add;
6. attention split-K/reduction для реальных context sizes.

Fusion меняет GGML execution boundaries и повышает риск ошибки. Для каждого
fused op сохранять unfused fallback и отдельный feature flag.

### Этап 5. MTP

1. Скачать официальный `google/gemma-4-31B-it-assistant`.
2. Конвертировать current pinned `convert_hf_to_gguf.py`, получить architecture
   `gemma4_assistant`, затем сделать Q8 GGUF.
3. Сначала проверить target и draft на одном локальном GPU. Для split target
   `Vulkan0,RPC0` shared KV может потребовать размещения draft на устройстве
   последних target layers (`RPC0`), но текущий upstream всё ещё имеет
   cross-backend blocker; не подбирать device вслепую на production.
4. Начать с `--spec-draft-n-max 1`, затем отдельно тестировать 2 и 4.
5. Отдельно применить и проверить AMD-кандидат PR `#25666`; не смешивать его с
   baseline MTP в одном изменении.
6. Не объединять MTP и новые kernels в одном A/B: сначала доказать эффект каждого.
7. Помнить: llama-server отключает speculative decoding для multimodal request;
   MTP ускоряет text, но не обработку самого скриншота.
8. До закрытия issue `#24492` или локального доказанного исправления не включать
   MTP для двуххостового production RPC.

## 7. Correctness-gates: обязательное условие «без потери качества»

Кандидат не допускается к performance A/B, пока не пройдены:

1. upstream unit tests и `test-backend-ops` для Vulkan;
2. сравнение output tensors с reference implementation в допустимой для dtype
   абсолютной/относительной погрешности;
3. exact token equality на deterministic corpus (`temperature=0`, фиксированный
   seed и неизменный sampler);
4. отсутствие регрессии perplexity сверх заранее измеренного baseline noise;
5. точное извлечение контрольных полей из screenshot suite;
6. declarant regression suite;
7. short/32K/long-context проверки;
8. отсутствие NaN/Inf, повторяющегося текста и деградации на других shapes;
9. одинаковый target output с MTP и без него;
10. чистый 6–8-часовой RPC burn-in.
11. exact OCR comparison для budgets 280/560/1120 без пропуска мелких полей,
    подмены цифр и деградации обычных изображений.

Плавающие reductions могут немного изменить logits даже при корректном kernel.
Поэтому нужно одновременно проверять tensor/logit tolerance, exact greedy tokens
и perplexity, а не полагаться только на один показатель.

## 8. Performance-gates

До запуска для кандидата выбирается одна целевая метрика:

- chat template: только correctness/reliability, обязательного speedup нет;
- high-resolution vision: измеримое улучшение exact OCR accuracy при сохранении
  Telegram latency SLO и отсутствии crash/OOM; speedup не ожидается;
- prefill shader, включая PR `#22970`: prompt processing минимум `+5%`;
- decode kernel/MTP: median decode TPS минимум `+5%`;
- нецелевая p95 latency и prompt/decode performance: не хуже baseline более чем
  на `5%`;
- zero correctness mismatches;
- zero OOM, GPU reset, device-lost и malformed RPC response;
- VRAM укладывается в обе 16 GiB карты с рабочим запасом;
- результат воспроизводится после холодного restart.

Сначала измеряется kernel-only/local-GPU эффект, затем end-to-end RPC. Если shader
быстрее локально, но combined endpoint не ускорился, bottleneck находится в RPC
или partitioning — такой патч не выдаётся за production speedup.

## 9. Что категорически не делать

- не заменять 31B на E2B/E4B ради красивого TPS;
- не уменьшать context/sliding window в «lossless» ветке;
- не обрезать vocabulary или lm_head;
- не менять quantization target без отдельной quality-валидации;
- не копировать Fable 5 Space code до выяснения его лицензии;
- не применять shader только потому, что он быстрее на Apple/NVIDIA;
- не заменять llama.cpp на KTransformers для dense Gemma: его основное ускорение
  рассчитано на CPU+GPU offload MoE experts, которых в нашей 31B нет;
- не путать KTransformers `AMD BLIS` для CPU с оптимизированным GPU backend для
  RX 9060 XT/GFX1200;
- не смешивать server и RPC runtime разных commits;
- не публиковать RPC port вне приватной point-to-point сети;
- не тестировать экспериментальную сборку вместо production без готового rollback.

## 10. Оценка трудоёмкости

| Работа | Оценка | Ожидаемый эффект |
|---|---:|---:|
| Chat template integration и regression suite | 0.5–1 день | корректность chat/tool/reasoning; TPS не обещается |
| Vision 1120 OCR rollout | выполнено 2026-07-20 | исправлен один мелкий OCR field; +50% dense latency, +501.9 MiB working RAM |
| Driver/capability staging gate | 1–3 дня | решение, применим ли cooperative-matrix path |
| Baseline, profiling, MTP conversion | 2–4 дня | решение, что оптимизировать |
| Ручной port PR `#22970`, если gate открыт | 2–4 дня | ориентир upstream +5–8% prefill, decode около нуля |
| Single-GPU MTP + PR `#25666` | 2–5 дней после рабочего draft | ориентир upstream около +12.9%, GFX1200 не гарантирован |
| Vulkan parameter autotuning | 3–7 дней | ориентировочно 5–20% |
| Q4_K decode kernels | 1–3 недели | ориентировочно 10–40% локально |
| Gemma-specific fusion set | 2–5 недель | сильно зависит от bottleneck |
| Hardening и RPC burn-in | 1–2 недели | подтверждение production-ready |

Полная ветка уровня Fable 5: **4–8+ недель** даже при помощи ИИ-агентов. Код можно
генерировать параллельно, но hardware benchmark и correctness-gates остаются
последовательными. Для нашей 31B реалистичная цель первой волны — перейти от
текущих ~11.6 к 15–20 ток/с; 255 ток/с не является реалистичной целью.

## 11. Работа через оркестратор

Рекомендуемые независимые роли:

1. **researcher** — лицензии, upstream PR/issues, алгоритмы без копирования кода;
2. **profiler** — baseline и ranking bottlenecks;
3. **kernel executor** — один shader family на ветку;
4. **correctness verifier** — не пишет kernels, только пытается выявить ошибку;
5. **benchmark verifier** — слепое baseline/candidate сравнение;
6. **integrator** — собирает только прошедшие commits и владеет rollback.

ИИ-агент не может сам объявить свой kernel корректным: автор и verifier должны
быть разными ролями. Все отчёты и сырые результаты сохраняются до принятия
решения.

## 12. Rollback и stop conditions

Для staging хранить:

- production runtime directory и его SHA-256;
- production systemd units/env;
- предыдущий RPC worker;
- отдельный candidate runtime directory;
- одну команду возврата `ENABLE_EXPERIMENT=0` и restart;
- локальный srv2 fallback service.

Немедленно остановить эксперимент при:

- несовпадении greedy tokens;
- ухудшении perplexity вне baseline noise;
- неправильном OCR/vision результате;
- NaN/Inf или повторяющемся/повреждённом выводе;
- OOM, GPU reset/device lost;
- RPC corruption/link-down;
- недостижении заранее выбранного candidate-specific performance gate после трёх
  воспроизводимых прогонов; для chat-template correctness update speed gate не
  применяется.

## 13. С чего начать в будущей сессии

1. Прочитать этот документ и текущий runbook в `analise_storage`.
2. Убедиться, что production health 200 и MTP выключен.
3. Выполнить короткий ASPM/MCLK A/B на обоих GPU с power/temperature guard и
   немедленным возвратом к `default`, если выигрыш не воспроизводится.
4. Проверить layer split `1/1`, `1.1/0.9`, `1.2/0.8` на одинаковом corpus;
   сохранить вариант только при exact correctness и безопасном VRAM reserve.
5. Создать отдельный Git repo/worktree и pinned source `b10012`.
6. Скопировать benchmark harness, но не production credentials/env.
7. Зафиксировать SHA-256 официального template `68abe480...` и выполнить Этап
   0A без замены GGUF-весов.
8. Проверить, что production и staging сохраняют `--cache-ram 0`; KTransformers
   и отдельный RAM caching layer не устанавливать.
9. Сохранять production Vision `280–1120`, `batch=1280`, `cache-ram=0`; расширить
   OCR corpus и выполнить длительный mixed text/image burn-in.
10. Повторить baseline и capability snapshot на текущем runtime.
11. В изолированном staging проверить более новую Mesa и cooperative-matrix gate;
   production driver пока не менять.
12. Если gate открыт, отдельно портировать и проверить PR `#22970`; если закрыт —
   зафиксировать `not applicable` и перейти дальше.
13. Отдельно сравнить pinned Vulkan runtime с HIP/ROCm 7.2 candidate; production
    backend не заменять до двуххостового burn-in.
14. Исправить официальный MTP conversion и измерить его отдельно на одном GPU.
15. Только на рабочем single-GPU MTP отдельно проверить PR `#25666`.
16. Провести one-GPU profiling и выбрать один top decode bottleneck для
    autotuning/Q4_K shader experiment с feature flag и fallback.
17. Запустить correctness-gates до измерения скорости.
18. Повторить выигравший кандидат на двуххостовом RPC и выполнить burn-in.
19. Зафиксировать решение в commit с model/runtime/template hashes и сырыми
    результатами.

До выполнения этих пунктов перенос WebGPU/CUDA-патчей следует считать
исследовательским проектом, а не готовым обновлением production LLM.

## 14. Будущий отдельный infrastructure repository

Отдельный GitHub-репозиторий для Gemma infrastructure пока не создан. План
безопасного выделения production-safe кода, документации, тестов и примеров
конфигурации зафиксирован в
[`GEMMA4-INFRA-SEPARATE-REPO-TODO-20260715.md`](GEMMA4-INFRA-SEPARATE-REPO-TODO-20260715.md).

До выполнения описанных там secret-scan, reproducibility и source-of-truth
gates рабочей версией остаются файлы приватного `FrankRappo/analise_storage`.

## 15. Журнал обновлений

### 2026-07-20

- разобрана публикация Unsloth и отделены изменения шаблона от NVIDIA-only
  FA4/NVFP4 оптимизаций;
- подтверждено, что официальный Google commit меняет template, а не веса;
- добавлен P0-план безопасного подключения template к нашей abliterated GGUF;
- добавлена матрица переносимости опубликованных патчей на RX 9060 XT/GFX1200;
- добавлен обязательный driver/cooperative-matrix capability gate для PR `#22970`;
- отражено расхождение между текущим `matrix cores=none` и историческим
  `KHR_coopmat`, чтобы не принять старый лог за текущую capability;
- PR `#25666` поставлен после single-GPU MTP, двуххостовый MTP оставлен закрытым
  до устранения cross-backend проблемы;
- проверен KTransformers: для текущей dense Gemma 4 он отклонён как runtime
  accelerator;
- по решению владельца host-memory prompt cache также закрыт: `--cache-ram 0`
  сохраняется, чтобы не занимать RAM и не повышать риск OOM;
- добавлен high-resolution vision track: официальный budget 560/1120 можно
  проверить без смены весов, но только с согласованными batch limits, OCR corpus,
  memory telemetry и поэтапным rollback-safe A/B;
- выполнен production A/B `280` против диапазона `280–1120`, зафиксированы
  OCR/latency/RAM/swap результаты, после одобрения владельца профиль 1120 включён
  постоянно без RAM cache;
- измерен live ASPM/MCLK baseline: при доступном state `1258 MHz` sustained MCLK
  оставалась около `1124 MHz`; зафиксирован P0 A/B без изменения модели;
- добавлены кандидаты asymmetric tensor split, отдельный HIP/ROCm 7.2 runtime,
  ngram speculative decoding и concurrency profile с явными ограничениями;
- зафиксировано, что ближайший реалистичный системный выигрыш `+5–20%`, эффекты
  не складываются, а AMDVLK/RDMA/RPC load cache не являются быстрым решением;
- проверена совместимость установленного Claw Cod на VM120 с текущим endpoint:
  text, streaming tool call и цепочка `bash -> write_file -> read_file` прошли;
  внешний Tesseract/OCRmyPDF доступен через Bash и отдельный OCR smoke прошёл;
  только direct high-resolution image transport в текущем Claw отсутствует;
- maximum context/concurrency probe создал два slots по 256K, но оставил только
  `75.5 MiB` VRAM на srv1; профиль отклонён и выполнен rollback к healthy 32K/p1;
- зафиксирован следующий безопасный ряд A/B: 192K, 160K, 128K на каждый из двух
  slots с обязательными Vision, long-prompt и aggregate throughput gates;
- обновлён практический порядок работ, оценки и rollback/correctness требования.
