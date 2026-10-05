# План: одно десктопное приложение из WhisperSync и Podcast Reels Forge

> Черновик от 2026-10-05. Рабочее имя приложения — **Studio**, имя пакета — `studio`. И то и другое заглушки: настоящее название пока не выбрано (см. [раздел 2](#2-открытые-решения)), и при выборе имени их заменяют одной автозаменой.
>
> Исходные проекты: [WhisperSync](https://github.com/Bormotoon/WhisperSync) (0.1.0, локально `/home/borm/VibeCoding/WhisperSync`) и Podcast Reels Forge (1.4.x, этот репозиторий).

## Оглавление

1. [Цель](#1-цель)
2. [Открытые решения](#2-открытые-решения)
3. [Исходное состояние](#3-исходное-состояние)
4. [Целевая архитектура](#4-целевая-архитектура)
5. [Этапы обработки](#5-этапы-обработки)
6. [Карта переноса: что, откуда, куда, как](#6-карта-переноса-что-откуда-куда-как)
7. [Фазы работ](#7-фазы-работ)
8. [Тестирование и приёмка](#8-тестирование-и-приёмка)
9. [Риски](#9-риски)
10. [Первые шаги](#10-первые-шаги)
11. [Приложения: форматы](#11-приложения-форматы)

---

## 1. Цель

### Что делает пользователь

1. Указывает папку, где лежат файлы с камер и рекордеров (можно вперемешку, можно по подпапкам). Вместо папки можно дать ссылку на YouTube.
2. Выбирает режим синхронизации:
   - **Авто** — по умолчанию;
   - **Звук камеры** — звук с камер нормальный, рекордер не нужен;
   - **Простая** — достаточно сдвига;
   - **Сложная** — нужна коррекция дрейфа.
3. Включает или выключает остальное: черновой монтаж, спикеров, рилсы, тексты. Выбирает, для кого проект: Final Cut, Premiere или оба.
4. Видит план: что найдено, какие этапы будут выполнены, какие пропущены и почему. Здесь же можно поправить ошибки распознавания (не та камера, не тот рекордер).
5. Нажимает «Пуск» и получает папку с результатом:
   - проект для Final Cut (`.fcpxml`) и/или Premiere (`.xml`);
   - синхронизированный звук;
   - транскрипт;
   - по желанию — черновой мастер, рилсы, вычитанный текст и статья.

### Принципы

- **Исходники неприкосновенны.** Программа ничего не пишет в файлы камер и рекордеров. Проекты для монтажных программ ссылаются на оригиналы и на отрендеренный звук.
- **Любой этап можно не выполнять.** Пропуск этапа не ломает остальные: каждый этап объявляет, что ему нужно на входе, и пропускается с понятной причиной, если входа нет.
- **Программа решает сама, пользователь может переопределить.** Планировщик выбирает этапы и режимы по содержимому папки и настройкам. Любое решение можно поменять вручную, и ручная правка сохраняется в проекте.
- **Ничего не пересчитывается зря.** У каждого этапа есть отпечаток входов (как в Forge). Повторный запуск выполняет только то, что изменилось.
- **Всё локально.** Никаких облачных API: Whisper, pyannote и llama.cpp работают на машине пользователя.
- **Ядро не знает про интерфейс.** Вся логика без Qt — как сейчас у обоих проектов. Графический интерфейс и командная строка — две оболочки над одним ядром.

### Чего не делаем (по крайней мере в первой версии)

- Собственный таймлайн-редактор. Тонкий монтаж — в Final Cut или Premiere, а приложение готовит черновик.
- Цветокоррекцию, сведение звука, графику.
- Публикацию в соцсети.
- Облачные модели (провайдеры OpenAI/Anthropic/Gemini в `llm/providers.py` Forge остаются в коде, но в интерфейсе не показываются).

---

## 2. Открытые решения

План ниже исходит из рекомендованного варианта. Последний столбец — что изменится, если выбрать иначе.

| # | Вопрос | Рекомендация | Если выбрать иначе |
|---|---|---|---|
| 1 | Где строить | **Новый репозиторий.** Историю обоих проектов импортировать через `git subtree` (раздел 7, фаза 0). Основа интерфейса и CI — из WhisperSync. | «Выращивать из WhisperSync» — фаза 0 короче (импортируется только Forge), но имя `whispersync` останется в путях и истории, а переименование пакета всё равно понадобится. |
| 2 | Лицензия | Решать тебе. Юридически можно любую: WhisperSync целиком твой, а MIT разрешает включать код Forge. | PolyForm Noncommercial закрывает коммерческое использование. MIT открывает и код синхронизации. На код и план это не влияет, меняются только `LICENSE` и `pyproject.toml`. |
| 3 | Mac | **Синхронизация, транскрипт, черновой монтаж и экспорт — везде. Рилсы и всё на torch — только с NVIDIA.** | Полная поддержка Mac потребует Whisper через MLX и рендер без NVENC (VideoToolbox) — это отдельная фаза после 7. |
| 4 | Старые репозитории | **Заморозка функций сейчас, только исправления ошибок.** Архивировать WhisperSync после фазы 2, Forge — после фазы 5, когда новый догонит их по возможностям. | Если развивать параллельно, каждую фичу придётся переносить дважды. |
| 5 | Название | — | Автозамена `studio` → новое имя. |
| 6 | Редактор субтитров | **Сначала открывать в системном браузере**, как сейчас. Встраивание через QWebEngineView — позже. | Сразу встраивать — +PyQt6-WebEngine (около 100 МБ) в базовую установку. |
| 7 | Версия Python | **3.12** для основного окружения. CI — 3.10–3.13. | 3.13+ — нужно сверить совместимость с torch и pyannote. |

---

## 3. Исходное состояние

| | WhisperSync | Podcast Reels Forge |
|---|---|---|
| Назначение | синхронизация звука рекордера с видео, дрейф часов, FCPXML | рилсы, вычитка и статья из длинного эпизода |
| Код / тесты, строк | 13 559 / 7 089 | 23 999 / 11 043 (+4 846 строк веб-интерфейса) |
| Тестов | 378 | 560 |
| Интерфейс | PyQt6 (тёмная тема, таймлайн, симулятор, справка) | CLI + статическая страница в браузере (сборка `config.yaml`, редактор субтитров) |
| Платформы | Windows, macOS, Linux; есть работа без GPU | фактически Linux + NVIDIA 16 ГБ |
| Тяжёлые зависимости | faster-whisper (без torch); `.sep-venv` на Python 3.12 под audio-separator | faster-whisper, torch, opencv, pyannote (опц.), llama-server, yt-dlp (опц.) |
| Настройки | dataclass `WhisperSyncConfig` + строгая проверка, JSON | `config.yaml` + `config.local.yaml`, словари |
| Лицензия | PolyForm Noncommercial 1.0.0 | MIT |
| Оркестрация | монолит `engine/pipeline.py` (2773 строки), один процесс + пул рендера | `pipeline.py` (1868) запускает стадии отдельными процессами `python -m …` |

### Что уже общее

- **Настройки Whisper одинаковые:** их перенесли из Forge в WhisperSync. Это лестница температур, `repetition_penalty`, `no_repeat_ngram_size`, режимы `fast`/`quality`, OOM-лестница.
- **Формат транскрипта один:** WhisperSync уже пишет JSON «в формате Forge» (`engine/transcript_export.py` → `segments[].words[]` + `sentences`). На этом формате и строится общий контракт.

### Что дублируется и сольётся в одно

| Что | WhisperSync | Forge |
|---|---|---|
| Обёртка Whisper | `engine/transcriber.py` (класс `WhisperEngine`, кэш по SHA-256) | `stages/transcribe_stage.py`, `utils/subtitle_sync.load_model` |
| Проверка окружения | `engine/system_check.py` | `preflight.py`, `pipeline._has_cuda/_gpu_vram_gb` |
| Блокировка от двойного запуска | `engine/workspace.output_lock` | `utils/run_lock.py` |
| Запуск ffmpeg и пробы | `engine/media.py`, `engine/proc.py` | `utils/ffmpeg.py`, `utils/media_qa.py`, `analysis/audio_features.py` |
| Поиск пауз | `pipeline._silence_spans`, `pause_spans_local`, `recorder_word_gaps` | `analysis/audio_features.parse_silencedetect`, `utils/clip_intervals.speech_edges` |
| Логи | `logging_setup.py` | `utils/logging_utils.py` |

---

## 4. Целевая архитектура

### 4.1. Слои

```
┌──────────────────────────────┐   ┌──────────────────────────┐
│ gui/  (PyQt6)                │   │ cli/  (headless, batch)  │
└──────────────┬───────────────┘   └────────────┬─────────────┘
               │  события JSON-lines, команды    │
┌──────────────▼─────────────────────────────────▼─────────────┐
│ stages/planner.py   — что запускать и почему                  │
│ stages/runner.py    — процессы-исполнители, порядок по GPU,   │
│                       отпечатки, отмена, отчёт                │
└──────────────┬───────────────────────────────────────────────┘
               │ Stage API
┌──────────────▼───────────────────────────────────────────────┐
│ stages/{scan,transcribe,sync,timeline,speakers,roughcut,      │
│         text,export,program,reels,fetch}                      │
└──────┬─────────────────────┬─────────────────────┬───────────┘
┌──────▼──────┐   ┌──────────▼─────────┐   ┌──────▼──────────┐
│ core/       │   │ llm/  (llama.cpp)  │   │ vision/ (torch) │
│ модель      │   └────────────────────┘   └─────────────────┘
│ проекта,    │
│ медиа, время│   modules/ — менеджер опциональных окружений
└─────────────┘
```

Правила зависимостей:

- `core` ни от чего не зависит.
- `stages` зависят от `core`, а также от `llm` и `vision` (те — только внутри исполнителя).
- `gui` и `cli` зависят от `stages` (только от планировщика и исполнителя) и от `core`.
- В `core` и `stages` нет импортов Qt, torch и pyannote на уровне модуля. Тяжёлые импорты — внутри функций, которые выполняются в процессе-исполнителе.

### 4.2. Дерево пакетов

```
studio/
  __init__.py
  app.py                     # точка входа: GUI по умолчанию, --cli → cli
  core/
    project.py               # Project, Asset, Camera, Recorder, Placement, … + (де)сериализация project.json
    timeline.py              # Timeline, Track, Clip, KeepRange, Marker; преобразования времени
    transcript.py            # Word/Segment/Transcript + JSON-формат + SRT
    transcript_index.py      # пословный/пофразовый индекс (из Forge analysis/)
    transcript_align.py      # перенос таймингов на исправленный текст (из Forge utils/word_alignment)
    media.py                 # probe, извлечение звука (WhisperSync engine/media.py)
    ffmpeg.py                # выбор ffmpeg с NVENC/libass, аргументы кодека (Forge utils/ffmpeg.py)
    media_qa.py              # проверка готового ролика (Forge)
    audio_spans.py           # паузы/тишина/энергия: общее для sync, roughcut, reels
    proc.py                  # запуск долгих процессов (WhisperSync)
    workspace.py             # атомарная публикация, блокировка папки, временные каталоги
    fingerprint.py           # отпечатки входов (Forge)
    naming.py, sources.py    # естественный порядок имён, стабильные id источников (WhisperSync)
    settings/                # модель настроек по разделам + загрузка YAML + проверка
    capabilities.py          # CUDA, VRAM, NVENC, llama-server, модули, ffmpeg
    env.py                   # .env с токенами (HF, YouTube)
    logging.py
    report.py                # отчёт о прогоне (Forge run_report)
  stages/
    base.py                  # Stage, Decision, Requirement, StageContext, StageOutput
    planner.py
    runner.py
    worker.py                # точка входа процесса-исполнителя: python -m studio.stages.worker
    events.py                # протокол событий JSON-lines
    scan/       transcribe/   sync/        timeline/   speakers/
    roughcut/   text/         export/      program/    reels/      fetch/
  llm/
    providers.py, schemas.py, json_utils.py
    server.py                # запуск/остановка llama-server (Forge utils/llama_cpp_service.py)
    session.py               # одна сессия сервера на группу LLM-этапов (Forge pipeline._LlamaSession)
  vision/
    face_crop.py, face_track.py, active_speaker.py
  modules/
    manager.py               # установка/проверка опциональных окружений
    recipes/                 # что ставить для separation, enhance, vision, diarization, llm, youtube
  gui/
    main_window.py
    screens/                 # start, material, plan, run, result, settings, help
    widgets/                 # drop_zone, log_view, timeline_preview, sync_simulator, …
    bridge.py                # QProcess ↔ runner, события → сигналы
    theme.qss
  cli/
    main.py                  # studio scan|plan|run|export|doctor|verify|batch|modules
    batch.py                 # прогон без присмотра (Forge autonomy)
  resources/
    prompts/{ru,en}/  fonts/  models/  subtitle_editor/  help/
tests/  (зеркалит studio/)
contrib/
  hooks/host_memory/         # освобождение памяти виртуалок (Forge host_memory) как хук
tools/
  evaluate_prompts.py  make_fixtures.py  migrate_settings.py
packaging/
  pyinstaller/  ci/
docs/
```

### 4.3. Время: три шкалы

Это главное, на чём легко ошибиться. Все этапы обязаны явно знать, в какой шкале у них числа.

| Шкала | Что это | Кто в ней живёт |
|---|---|---|
| **Время файла** (asset time) | секунды от начала конкретного файла | транскрипты источников, кэш Whisper, якоря синхронизации |
| **Время таймлайна** (timeline time) | общая шкала съёмки; ноль — начало самого раннего материала, опорная шкала — часы главной камеры (как `timebase_source: camera` в WhisperSync) | размещение файлов, транскрипт таймлайна, спикеры, решения монтажа, экспорт |
| **Время монтажа** (edited time) | шкала после вырезания лишнего | черновой мастер, транскрипт мастера, рилсы |

Преобразования:

- **файл → таймлайн:** `Placement` — `t_tl = offset + k · (t_file − in)` для каждого файла. Это ровно `AlignmentMap` WhisperSync (`t_camera = offset + k · t_recorder`), обобщённый на камеры. У камер `k = 1`: их часы и есть шкала.
- **таймлайн → монтаж:** `EditList` — упорядоченный список оставленных диапазонов. Время монтажа = сумма длин предыдущих диапазонов + смещение внутри текущего. Обратное преобразование однозначно для любой точки внутри оставленного диапазона.
- Модуль `core/timeline.py` даёт функции `file_to_timeline`, `timeline_to_file`, `timeline_to_edited`, `edited_to_timeline` и `map_words(words, …)`. Все этапы пересчитывают время только через них. Тесты на круговое преобразование обязательны.

### 4.4. Модель проекта

Набросок. Это dataclass'ы в `core/project.py`, сериализация в `project.json` с полем `version` для миграций.

```python
@dataclass
class Asset:                      # один файл из папки
    id: str                       # стабильный slug (WhisperSync sources.assign_source_ids)
    path: Path                    # относительно корня папки, если внутри неё
    kind: Literal["video", "audio", "other"]
    role: Literal["camera", "recorder", "ignore", "unknown"]
    device: str | None            # «DJI Osmo Pocket 3», «ZOOM H6» — из метаданных или имени
    group_id: str | None          # к какой камере/рекордеру относится
    chapter_of: str | None        # продолжение записи (главы GoPro, разбиение по 4 ГБ)
    info: MediaInfo               # результат probe (WhisperSync media.MediaInfo)
    created_at: datetime | None
    timecode: str | None
    manual: dict[str, Any]        # ручные правки из интерфейса, переживают пересканирование

@dataclass
class Camera:   id: str; name: str; asset_ids: list[str]; speaker: str | None; is_wide: bool | None
@dataclass
class Recorder: id: str; name: str; asset_ids: list[str]; tracks: list[RecorderTrack]
@dataclass
class RecorderTrack: index: int; channels: list[int]; speaker: str | None   # дорожка многодорожечного рекордера

@dataclass
class Placement:                  # где файл лежит на таймлайне
    asset_id: str
    offset_s: float; in_s: float; duration_s: float; k: float = 1.0
    provenance: Literal["filename", "metadata", "timecode", "acoustic", "text", "repair", "manual"]
    evidence: dict[str, float]    # inliers, residual_ms, coverage — как AlignmentMap.evidence

@dataclass
class SyncedAudio:                # отрендеренный голос под конкретный файл камеры или весь таймлайн
    path: Path; for_asset: str | None; source_ref: tuple[str, float, float]

@dataclass
class SpeakerTurn: start: float; end: float; speaker: str; source: Literal["mics", "pyannote", "manual"]

@dataclass
class KeepRange:                  # кусок, который остаётся в монтаже (время таймлайна)
    start: float; end: float; camera_id: str | None

@dataclass
class Cut:                        # что убрано и почему — для маркеров и проверки
    start: float; end: float
    reason: Literal["pause", "retake", "head", "tail", "filler", "llm", "manual"]
    confidence: float; note: str; accepted: bool = True

@dataclass
class EditList: keep: list[KeepRange]; cuts: list[Cut]; markers: list[Marker]; mode: Literal["cut", "markers"]

@dataclass
class Project:
    version: int
    source_dir: Path; work_dir: Path
    assets: list[Asset]; cameras: list[Camera]; recorders: list[Recorder]
    placements: list[Placement]; synced_audio: list[SyncedAudio]
    transcripts: dict[str, Path]          # asset_id | "timeline" | "edited" → JSON
    speakers: list[SpeakerTurn]; speaker_names: dict[str, str]
    edit: EditList | None
    outputs: dict[str, list[Path]]        # export, program, reels, text
    stage_states: dict[str, StageState]   # отпечатки (Forge fingerprint.StageState)
```

`SyncPlan` и `MediaClip` из WhisperSync перестают быть внешним форматом. В новой модели их заменяют `Placement`, `SyncedAudio`, `KeepRange` и `Timeline` (раздел 6). Внутри этапа `sync` их можно оставить на время переноса.

### 4.5. Папка проекта

По умолчанию — `<папка с материалом>/_studio/`: пути получаются относительными, и проект переносится вместе с материалом. Если папка только для чтения, рабочая папка задаётся отдельно.

```
_studio/
  project.json              # модель проекта — единственный источник правды
  settings.yaml             # настройки ЭТОГО проекта (только отличия от общих)
  stages/
    scan/report.json
    transcribe/<asset_id>.json, .srt
    sync/voice/<asset_id>_voice.wav, ambience/…, segments/…, verify.json, self_check.json
    timeline/transcript.json
    speakers/diarization.json   # формат Forge: [{start, end, speaker}]
    roughcut/edit.json
    text/proofread.json, article.md
    reels/moments.json, reels_summary.md
  program/program.mp4, program.transcript.json
  export/<имя>.fcpxml, <имя>.xml, transcripts/, <имя>_master.wav
  reels/reel_01.mp4 …
  logs/run-<дата>.log, report.json
```

Кэш Whisper — не в проекте, а в общем пользовательском каталоге кэша (`platformdirs`, как `cache_dir` в WhisperSync). Тогда тот же материал в другом проекте не распознаётся заново.

### 4.6. Этап: интерфейс, планировщик, исполнитель

```python
class Stage(Protocol):
    id: str                                  # "sync"
    title: str                               # «Синхронизация» (для интерфейса)
    after: tuple[str, ...]                   # от каких этапов зависит
    gpu: GpuUse                              # NONE | WHISPER | TORCH | LLM | NVENC

    def requirements(self, s: Settings) -> list[Requirement]: ...
        # Requirement(kind="cuda"|"vram_gb"|"module"|"llama"|"nvenc"|"ffmpeg_libass",
        #             value=..., optional=bool, why="…")
    def decide(self, p: Project, s: Settings, caps: Capabilities) -> Decision: ...
        # Decision.run(choices={...}) | Decision.skip(reason) | Decision.blocked(reason, fix)
    def fingerprint(self, p: Project, s: Settings) -> str: ...
    def run(self, ctx: StageContext) -> StageOutput: ...
        # ctx: project (копия), settings, workdir, progress(v, msg), warn(msg),
        #      cancelled() -> bool, emit_timeline(snapshot), log
```

**Планировщик** (`planner.py`):

1. Обходит этапы в порядке зависимостей и вызывает `decide()`.
2. Если этап пропущен или заблокирован, зависимые этапы получают `skip` с причиной «нет входа X». Исключение — этапы, у которых этот вход необязателен (например, `export` без `roughcut` экспортирует таймлайн без вырезок).
3. Учитывает ручные переопределения (`--only`, `--skip`, галочки в интерфейсе) и показывает их в плане как «выключено пользователем».
4. Возвращает `Plan`: список `PlannedStage(stage, decision, choices, will_reuse: bool)`, где `will_reuse` значит, что отпечаток совпал и результат уже есть.

**Исполнитель** (`runner.py`):

- Каждый этап с `decision = run` и изменившимся отпечатком запускается **отдельным процессом**: `python -m studio.stages.worker <stage_id> <project.json> <settings.yaml>`. Если этапу нужен модуль, используется интерпретатор модуля. Зачем отдельный процесс:
  - видеопамять гарантированно освобождается после этапа;
  - падение этапа не роняет интерфейс;
  - отмена работает всегда (так уже устроен Forge, `pipeline.run_module`);
  - этапы на torch можно запускать интерпретатором окружения `vision`.
- Исполнитель читает из stdout процесса события JSON-lines (раздел 11.2) и пересылает их в интерфейс или в CLI.
- После успеха этап отдаёт `StageOutput`: изменения в модели проекта и список файлов. Исполнитель вливает изменения в `project.json` атомарно (`core/workspace.published`) и записывает отпечаток.
- **Ошибки.** Упавший этап помечается `failed`, зависимые — `skip(«упал X»)`. Независимые этапы продолжают работу: упала статья — экспорт всё равно делается. Это поведение Forge «упавший эпизод не роняет очередь», перенесённое на этапы.
- **Отмена:** флаг-файл в `workdir` (кооперативная отмена), затем `terminate` через N секунд. Модель проекта не меняется, частичные файлы удаляются (`RunWorkspace` из WhisperSync).

### 4.7. GPU и память

- Этапы с GPU идут строго по одному.
- Порядок выбран так, чтобы тяжёлое не грузилось дважды. Он совпадает с нынешним порядком Forge «Whisper → pyannote → llama → NVENC»:
  ```
  scan → transcribe → sync → timeline → speakers → [proofread → roughcut → article → reels-select] → export → program → reels-render
                                                    └──────────── одна сессия llama-server ─────────────┘
  ```
- **llama-server** поднимается один раз на группу LLM-этапов и гасится после неё (`llm/session.py`, из Forge `_LlamaSession`). Модель, кэш ответов и автонастройка контекста — как в Forge (`_autotune_llama_cpp_conf`, `CachingProvider`).
- **Хуки.** В `settings.local.yaml` задаются `hooks.before_run`, `hooks.after_run`, `hooks.before_gpu`, `hooks.after_gpu` — команды оболочки. Через них подключается пауза ПедОбраза (`local/pedobraz_pause.py`) и освобождение памяти виртуалок (`contrib/hooks/host_memory`). В продукт эти вещи не встраиваются.
- По правилу «тяжёлое — только на GPU»: если CUDA нет, этапы с `gpu ≠ NONE` по умолчанию получают `blocked(«нет CUDA»)`. Работа на процессоре включается отдельной настройкой `compute.allow_cpu` (для Mac и машин без NVIDIA).

### 4.8. Настройки

- **Модель:** dataclass'ы по разделам (`ScanSettings`, `SyncSettings`, `TranscribeSettings`, `SpeakerSettings`, `RoughcutSettings`, `ExportSettings`, `ProgramSettings`, `TextSettings`, `ReelsSettings`, `LlmSettings`, `HooksSettings`, `BatchSettings`, `FetchSettings`).
- **Проверка** — подход WhisperSync (`config.py`): типы, перечисления, диапазоны, конечные числа. Проверяется один раз после слияния всех слоёв. Ошибка — одна строка и код возврата 2, а не странный результат через час.
- **Формат файла** — YAML, как у Forge (человекочитаемый, с комментариями).
- **Слои** (каждый следующий перекрывает предыдущий):
  1. умолчания в коде;
  2. общий `settings.yaml` пользователя (каталог настроек `platformdirs`);
  3. `settings.local.yaml` рядом с ним — машинные пути, порты, хуки (идея `config.local.yaml` Forge);
  4. `_studio/settings.yaml` проекта;
  5. `--set раздел.ключ=значение` из CLI или правки в интерфейсе.
- **Пресеты** — готовые наборы: «Подкаст: 2 человека, 2 камеры, петлички», «Соло на камеру», «Интервью с рекордером», «Готовое видео с YouTube».
- **Миграция:** `tools/migrate_settings.py` переводит старый `config.yaml` Forge и JSON-конфиг WhisperSync в новые разделы (таблица соответствия — раздел 6.3).

Разделы (сокращённо):

```yaml
scan:       {ignore: [...], grouping: auto|folders|names, device_patterns: {...}}
sync:       {mode: auto|camera|simple|complex, strategy: 1|2|3, auto: {max_drift_ms: 20},
             camera_audio_source: auto|<camera>, boundary_flex: true, seam_snap_max_s: 0.4,
             crossfade_ms: 10, pause_duck: {...}, ambience: {...}, voice_enhance: off,
             self_check: off|warn|repair, verify: false, camera_av_offset_ms: {...},
             voice_segment_minutes: 0, master_wav: false}
transcribe: {model: large-v3, language: ru, device: auto, compute_type: auto, mode: fast,
             batch_size: 16, initial_prompt: "", glossary: [...], keep_fillers: true}
speakers:   {method: auto|mics|pyannote|off, num_speakers: null, names: {...}}
roughcut:   {enabled: true, mode: cut|markers, pauses: {...}, retakes: {...}, head_tail: {...},
             fillers: {...}, multicam: {...}, llm: {enabled: false, ...}}
export:     {targets: [fcpxml, xmeml], fcpxml_version: "1.9", name: "", relative_paths: true}
program:    {enabled: auto, encoder: nvenc, quality: high}
text:       {proofread: {...}, article: {...}}
reels:      {...}   # нынешние processing/subtitles/video/exports/audio/prompts из Forge
llm:        {server: {...}, model: gemma4, cache: true}
hooks:      {before_run: "", after_run: "", before_gpu: "", after_gpu: ""}
batch:      {...}   # нынешний autonomy из Forge
fetch:      {...}   # нынешний youtube из Forge
compute:    {allow_cpu: false}
```

### 4.9. Опциональные модули

Базовая установка: PyQt6, faster-whisper/ctranslate2, numpy, PyYAML, Pillow, fonttools, platformdirs. Всё тяжёлое — в отдельные окружения, которые ставит `modules/manager.py`. Принцип уже работает в WhisperSync (`.sep-venv` и `setup_sep_venv.sh`).

| Модуль | Что внутри | Нужен этапам | Откуда рецепт |
|---|---|---|---|
| `separation` | audio-separator (Python 3.12) | sync → эмбиенс, enhance → denoise/dereverb | `setup_sep_venv.sh`, `requirements-sep.txt` |
| `enhance` | Resemble Enhance и др. | sync → voice_enhance | `engine/enhance.py` (`resemble_package_dir`) |
| `vision` | torch, opencv, модели YuNet/Light-ASD | reels-render (кадр за говорящим), roughcut → авто-привязка камер | `requirements.txt` Forge |
| `diarization` | pyannote.audio + torch, токен HF | speakers → pyannote | extras `diarization` Forge |
| `llm` | бинарник llama-server (CUDA/Metal) + GGUF gemma4 | text, roughcut-LLM, reels-select | `llama_cpp_service.py`, `config.yaml → llama_cpp` |
| `youtube` | yt-dlp | fetch | extras `youtube` Forge |

Код этапа, запущенный в окружении модуля, — это тот же пакет `studio`: менеджер кладёт путь к нему в `PYTHONPATH` интерпретатора модуля. Поэтому `core` должен работать на всех версиях Python, которые используют модули (3.12 — общий знаменатель).

В фазах 1–5 разработка идёт в одном окружении, как сейчас (`whisper-env` с torch). Менеджер модулей появляется в фазе 6 вместе с упаковкой.

---

## 5. Этапы обработки

Для каждого этапа: когда запускается, вход, выход, откуда берётся код, что пишется заново, на что обратить внимание.

### 5.0. `fetch` — альтернативный вход: YouTube

- **Когда:** пользователь дал ссылку, а не папку.
- **Выход:** скачанный файл и `.info.json` в `_studio/`. Проект из одного ассета `camera` со звуком. В метаданных отмечается, что материал уже смонтирован, поэтому планировщик сам пропускает `sync` и `roughcut`.
- **Код:** Forge `stages/fetch_stage.py`, `sources/youtube.py`, `sources/episode_metadata.py`, `scripts/fetch_youtube.py` — **как есть**. Меняются только точка входа (`run(ctx)` вместо CLI) и путь выгрузки.
- **Заметки:** перечисление канала или плейлиста — это режим `batch` (несколько проектов), а не один проект.

### 5.1. `scan` — разбор папки (новое)

- **Когда:** всегда.
- **Вход:** путь к папке и ручные правки из прошлого скана.
- **Выход:** `assets`, `cameras`, `recorders`, предупреждения, сводка для экрана плана.
- **Как:**
  1. Рекурсивный обход. Пропускаются скрытые файлы, сама `_studio/` и служебные файлы камер: `.LRF`/`.LRV` (прокси), `.THM`, `.SRT`-телеметрия DJI. Список настраивается (`scan.ignore`).
  2. Параллельный `probe` (WhisperSync `core/media.probe`): есть ли видео (не обложка), аудио, каналы, длительность, fps, `creation_time`, таймкод, теги производителя и модели (`com.apple.quicktime.make/model`, `encoder`, `handler_name`).
  3. **Роль.** Есть видеопоток → `camera`. Только звук → `recorder`. Не медиа → `ignore`. Короткий звук, который не удалось разместить на этапе sync (джингл, музыка), потом предлагается пометить как `ignore`.
  4. **Группировка по устройствам, по приоритету:**
     - подпапки (нынешнее соглашение WhisperSync «каждая камера в своей подпапке»);
     - теги производителя и модели;
     - шаблоны имён.
     Шаблоны — таблица в настройках. Примеры ниже **проверить на реальных файлах**:

     | Устройство | Пример имени | Заметки |
     |---|---|---|
     | DJI (Osmo, Action, Pocket) | `DJI_0838.MP4`, `DJI_20251004120000_0001_D.MP4` | рядом `.LRF`-прокси |
     | GoPro | `GX010024.MP4`, `GX020024.MP4` | вторая и третья цифры — номер главы одной записи |
     | Sony | `C0001.MP4` + `C0001M01.XML` | XML-спутник: модель и таймкод |
     | Canon | `MVI_1234.MP4` | |
     | iPhone | `IMG_1234.MOV` | тег `make = Apple` |
     | Zoom (H-серия) | `ZOOM0001.WAV`, `ZOOM0001_Tr1.WAV`, `ZOOM0001_LR.WAV` | дорожки одного дубля — один рекордер, общие часы |
     | DJI Mic, Rode, Tascam | `DJI_*.WAV`, `TASCAM_*.wav` и т. п. | достаточно «только звук» |

  5. **Главы и разбиение.** Файлы одной камеры, у которых `created_at + duration ≈ created_at` следующего или номера идут подряд в одной записи (главы GoPro), склеиваются в непрерывную последовательность без зазора. Здесь используется `naming.py` WhisperSync (естественный порядок, поиск последовательных серий).
  6. **Дорожки рекордера.** Многоканальный WAV или набор `_Tr1`, `_Tr2` одного дубля превращается в `RecorderTrack` со своим `speaker = None`. Имена спикеров даются позже.
  7. **Предупреждения:** «в видео нет звука — по звуку не синхронизировать, нужен таймкод или ручной сдвиг», «файл не опознан», «две камеры с одинаковыми именами файлов».
- **Откуда:** новый код. Используются `scan_cameras`, `scan_video_clips`, `preliminary_offsets`, `sequence_order_warnings` из `engine/pipeline.py` WhisperSync (строки 141–322) и `find_input_queue`, `_ensure_audio_companions` из Forge `pipeline.py`.
- **В интерфейсе:** экран «Материал» — дерево «устройство → файлы» с миниатюрами. Файл можно перетащить в другую группу, поменять роль, назвать камеру или дорожку. Правки пишутся в `Asset.manual` и переживают повторный скан.

### 5.2. `transcribe` — распознавание источников

- **Когда:** почти всегда. **Что именно** распознавать, решает `decide()`:
  - звук рекордера (каждый дубль; у многодорожечного — сведение или лучшая дорожка, см. 5.5) — всегда, если рекордер есть;
  - звук камер — только если синхронизации нужны текстовые якоря (режимы `complex` или `auto`, когда акустика не справилась) или если рекордера нет.
- **Выход:** `stages/transcribe/<asset_id>.json` и `.srt` во **времени файла**.
- **Откуда:**
  - основа — `WhisperEngine` из WhisperSync `engine/transcriber.py`: кэш по SHA-256 с учётом фактического устройства, OOM-лестница, батчи, выгрузка модели;
  - из Forge `stages/transcribe_stage.py`: разбиение `.srt` по предложениям (`_segment_to_srt_cues`, `_wrap_on_words`, `_build_sentence_groups`), `confidence`/`avg_logprob` сегментов, сессия модели на всю очередь (`whisper_model_session`);
  - сериализацию объединить в `core/transcript.py` на основе `engine/transcript_export.py` WhisperSync, в котором уже формат Forge.
- **Новое:**
  - `transcribe.glossary` — имена и термины из настроек (у Forge глоссарий сейчас есть только у вычитки). Передаётся в `hotwords` или `initial_prompt`.
  - `transcribe.keep_fillers`. Whisper склонен выбрасывать «ээ» и «мм», а черновому монтажу они нужны. Пробуем `initial_prompt` с примерами («Ээ, ну, мм, как бы…»). Эффект измеряем на реальной записи (риск в разделе 9).
- **GPU:** WHISPER. Модель загружается один раз на весь этап, все файлы — в одном процессе.

### 5.3. `sync` — размещение и синхронизация

Синхронизация решает **две разные задачи**, и их стоит развести:

1. **Размещение** — где каждый файл лежит на общей шкале. Нужно всегда, когда источников больше одного, в том числе если камер две, а рекордера нет.
2. **Подгонка** — заставить звук рекордера идти вровень с картинкой на всей длине (дрейф часов). Нужна только при режиме, отличном от «звук камеры».

#### Режимы

| Режим | Размещение | Подгонка | Рендер звука |
|---|---|---|---|
| `camera` («звук камеры») | камеры относительно друг друга: акустика, при неудаче текст | нет | нет; в экспорте звук камеры (`sync.camera_audio_source`) |
| `simple` | акустический сдвиг (GCC-PHAT), при неудаче текстовые якоря | один коэффициент на клип (стратегия 1 «Global Linear») | **нет, если `k ≈ 1`**: экспорт ссылается на оригинальный файл рекордера со сдвигом; иначе передискретизация целиком |
| `complex` | текстовые якоря + RANSAC (как сейчас) | стратегия 3 «Hybrid» (2 — в расширенных настройках) | да, кусками (как сейчас) |
| `auto` | как `simple`, плюс замер дрейфа | выбирается по замеру | по выбранному |

**Правило для `auto`:**

1. Для каждой пары «клип камеры ↔ рекордер» мерим сдвиг акустически в нескольких окнах по длине клипа (`acoustic_coarse_align` / `gcc_phat` из `engine/acoustic.py`) и строим прямую.
2. `|k − 1| · длительность < sync.auto.max_drift_ms` (по умолчанию 20 мс — половина кадра при 25 к/с) → `simple` без рендера.
3. Дрейф линейный (остаток мал, локальные `k` стабильны) → стратегия 1.
4. Иначе → `complex`. Решение принимает уже существующий `matcher.recommend_strategy`.
5. Если акустика не дала уверенного результата (камера далеко, эхо) → докидываем распознавание этого клипа камеры и идём по текстовым якорям. Так `transcribe` получает задание «распознать ещё клип X». Технически это повторный вызов этапа `transcribe` с расширенным списком, который планировщик делает автоматически.

#### Что входит в этап, откуда и как

Монолит `_run_pipeline_locked` (`engine/pipeline.py`, 1500–2773) режется по шагам, которые там уже идут подряд:

| Шаг | Строки в `pipeline.py` WhisperSync | Куда | Как |
|---|---|---|---|
| Сканирование камер | ~1540–1617 | `stages/scan` | заменяется новым сканером |
| Транскрибация рекордеров и камер | ~1623–1729 | `stages/transcribe` | выносится в отдельный этап |
| Выравнивание (якоря, RANSAC, акустический запасной путь, проверка карты) | ~1730–1818, `_try_acoustic_fallback`, `_anchor_count`, `_map_evidence` | `stages/sync/placement.py` | функции как есть, обвязка заново |
| План стратегии, куски, привязка швов к тишине, Boundary Flex | ~1874–2097; функции 324–935 | `stages/sync/pieces.py` | как есть |
| Дубли → auditions/маркеры | внутри планирования, `_retake_groups_for_clip`, `_retake_groups_in_segment` | `stages/roughcut` | **переезжают в черновой монтаж** |
| Рендер кусков, пул процессов, сборка | ~2112–2296; функции 936–1160 | `stages/sync/render.py` | как есть |
| Улучшение голоса | ~2297–2346 | `stages/sync/enhance_step.py` | как есть, модуль `enhance` |
| Самопроверка и ремонт | ~2347–2425, `_repair_span` | `stages/sync/self_check_step.py` | как есть |
| Нарезка голоса на N-минутные сегменты | ~2426–2501, `_quiet_cut_points` | `stages/sync/segments.py` | как есть |
| Эмбиенс (отделение голоса камеры) | ~2517–2663 | `stages/sync/ambience_step.py` | как есть, модуль `separation` |
| Мастер-WAV | ~2664–2690 | `stages/export` | переезжает в экспорт |
| FCPXML | ~2691–2746 | `stages/export` | переезжает в экспорт |

- **Выход:** `placements`, `synced_audio`, отчёты `verify.json` и `self_check.json`, предупреждения (несопоставленные клипы, большой остаток, совет по стратегии).
- **Прогресс таймлайна** (`PipelineProgress`, `make_timeline_entries`) превращается в событие `timeline` протокола (раздел 11.2). Виджет `timeline_preview.py` получает его как сейчас.
- **GPU:** NONE для выравнивания и рендера (процессор, пул). WHISPER — для самопроверки. TORCH (в окружении модуля) — для эмбиенса и улучшения голоса.

### 5.4. `timeline` — транскрипт таймлайна (новое, лёгкое)

- **Когда:** всегда после `sync` или сразу после `transcribe`, если синхронизировать нечего.
- **Как:** берём транскрипт «главного» звука (рекордер; без него — выбранная камера) и пересчитываем слова через `Placement` во время таймлайна (`core/timeline.map_words`). Если рекордеров несколько и включён `recorder_mode: best`, на каждый отрезок берётся лучший, как сейчас при выборе рекордера на клип.
- **Выход:** `stages/timeline/transcript.json` — **без повторного Whisper**.

### 5.5. `speakers` — кто говорит

- **Когда:** `speakers.method ≠ off`. При `auto`:
  - у рекордера ≥ 2 дорожек со спикерами (или ≥ 2 рекордеров в режиме `all`) → `mics`;
  - иначе, если установлен модуль `diarization` и людей явно больше одного → `pyannote`;
  - иначе пропуск.
- **`mics` (новое):**
  1. Огибающая громкости (RMS, шаг 50 мс) каждой синхронизированной дорожки на шкале таймлайна.
  2. Для каждого слова транскрипта спикер — дорожка с наибольшей средней энергией, если она громче второй на `≥ 6 дБ`. Иначе — `overlap`.
  3. Сглаживание: реплики короче порога сливаются с соседними. Идея — `merge_short_turns` из Forge `face_track.py`.
  4. Выход в формате Forge `diarization.json` (`[{start, end, speaker}]`) во времени таймлайна. Модуль не нужен, GPU не нужен.
  5. Звук из чужих петличек (bleed) при этом не мешает: слова берутся из одного транскрипта, а дорожки служат только для сравнения громкости.
- **`pyannote`:** Forge `scripts/diarize.py` → `stages/speakers/pyannote.py`. `main(argv)` превращается в функцию, запускается в окружении `diarization`, параметр `num_speakers` сохраняется.
- **Реплики:** Forge `analysis/speaker_turns.py` → `stages/speakers/turns.py`, **как есть**. Им пользуются `text`, `reels` и `roughcut`.
- **Имена:** интерфейс («Дорожка 1 = Иван») или LLM по содержанию разговора (Forge `article_stage.resolve_speaker_names`).

### 5.6. `roughcut` — черновой монтаж (новое)

- **Когда:** `roughcut.enabled` и материал не помечен как уже смонтированный.
- **Вход:** транскрипт таймлайна (**не** вычитанный: нужны слова как сказаны), спикеры, звук для оценки энергии, камеры и их привязка к спикерам.
- **Выход:** `EditList` (`stages/roughcut/edit.json`).

**Детекторы** (каждый даёт кандидатов `Cut` с причиной и уверенностью):

| Детектор | Как работает | По умолчанию | Откуда код |
|---|---|---|---|
| Паузы | промежуток между словами ≥ `pauses.min_s` (1.0 с) **и** энергия ниже порога (дыхание и смех не режем); пауза сокращается до `pauses.keep_s` (0.4 с, поровну по краям) | вырезать | `recorder_word_gaps`, `_silence_spans`, `pause_spans_local` (WhisperSync) + `parse_silencedetect` (Forge) → `core/audio_spans.py` |
| Дубли | группы повторов одной фразы; всё, кроме выбранного дубля (по умолчанию последнего), вырезается от начала первой попытки до начала выбранной | вырезать | WhisperSync `engine/retakes.py` (`detect_retakes`, `refine_retakes`) — как есть |
| Начало и конец | всё до первого слова − `head_tail.pad_s` и после последнего + `pad_s` | вырезать | новое |
| Слова-паразиты | словарь по языку («ну», «как бы», «типа», «короче», «вот», «эээ») | **только маркеры** | новое |
| Нераспознанные звуки | энергия есть, а слов нет дольше 0.3 с — вероятно «эээ», которое Whisper выкинул | **только маркеры** | новое, на `core/audio_spans.py` |
| LLM (фаза 6) | лучший дубль по подаче, оговорки, «где на самом деле начинается разговор», паразиты по смыслу | выключено | `llm/` Forge, по образцу `cleanup/judge` в `analyze_stage.py` |

**Точность резов:**

- рез ставится на границу слова и прижимается к тишине (`_snap_to_word_gap`, `seam_snap_max_s` из WhisperSync);
- затем округляется до кадра;
- на звуке при программном рендере (`program`) — кроссфейд 10 мс (`crossfade_ms`).

**Мультикамера** (фаза 4):

- **привязка камера ↔ спикер:**
  - вручную на экране «Материал»;
  - автоматически (v2): кадры каждой камеры во время реплик каждого спикера → YuNet + Light-ASD (`vision/`) → чья камера показывает говорящего крупнее; камера, где почти всё время ≥ 2 лиц, — общий план (`face_crop.decide_layout`);
- **правила переключения (v1, детерминированные):**
  - реплика ≥ `multicam.min_shot_s` (2.5 с) → камера спикера;
  - короткая реплика — остаёмся на текущем плане;
  - частые перебросы (≥ 3 смены за 6 с) или наложения → общий план;
  - план дольше `multicam.max_shot_s` (20 с, опция) → вставка общего;
  - рез на `начало реплики − 0.2 с`, по кадру.

**Режимы применения:**

- `cut` (по умолчанию) — оставленные диапазоны идут встык. В местах резов маркеры с причиной («пауза 3.2 с», «дубль 2 из 3»).
- `markers` — ничего не режется, всё размечено маркерами. Этот путь по умолчанию сейчас у дублей WhisperSync.
- `disable` (оценить в фазе 3 на реальном импорте) — вырезанное остаётся на таймлайне отключёнными клипами: `enabled="0"` в FCPXML, `<enabled>FALSE</enabled>` в XML Premiere.

**Просмотр решений в интерфейсе** (фаза 3): список резов с причиной и кнопкой «прослушать» (QtMultimedia, диапазон ±2 с). Галочка «принять» меняет `Cut.accepted`. Пересчитываются только `export` и `program` (их отпечаток зависит от `edit.json`), сам `roughcut` заново не идёт.

### 5.7. `export` — проекты для монтажных программ

- **Когда:** всегда, если есть хоть одна камера.
- **Вход:** `Timeline` (размещение, синхронизированный звук, дорожки) + `EditList` (если есть) + маркеры.
- **Как:**
  1. `stages/export/sequence.py` (новое) — **нейтральная последовательность**: дорожки → клипы `(asset, src_in, src_out, rec_in, rec_out, enabled, lane, role)` + маркеры + мультикам-группы. Всё время уже в кадрах последовательности. Строится из таймлайна и `EditList`; без монтажа — один `KeepRange` на весь таймлайн.
  2. `fcpxml.py` — из WhisperSync `engine/export.py`. **Адаптировать:** вход — `Sequence` вместо `SyncPlan`. Сохраняются: рациональные времена и привязка к кадрам (`to_rational`, `_frame_rational`, `fps_to_frame_duration`), относительные пути (`_media_src`), форматы по `(fps, w, h)`, роли, политика «звук камеры выключен, если есть замена» (`source_audio_enabled`), проверки `check_fcpxml`/`validate_fcpxml`/`fcpxml_intervals`. **Новое:** мультикамера через `<media><multicam>` + `<mc-clip>` (раздел 11.4), маркеры резов.
  3. `xmeml.py` (новое) — XML Final Cut 7 (`<xmeml version="4">`) для Premiere; DaVinci Resolve его тоже читает. Premiere **не импортирует** FCPXML. Подводные камни:
     - время в кадрах, `<rate><timebase>/<ntsc>` (23.976 и 29.97 → `ntsc TRUE` с базой 24/30);
     - абсолютные `pathurl` вида `file://localhost/…` с процентным кодированием; на Windows — `file://localhost/C%3a/…`;
     - связь видео и звука (`<link>`), `sourcetrack` для каждого канала звука;
     - мультикамеру в v1 делаем «стопкой»: каждая камера на своей видеодорожке, неактивные куски `enabled = FALSE`;
     - подсэмпловая точность синхронизации в этом формате невозможна, поэтому синхронизация «зашита» в отрендеренный WAV, а позиции в кадрах.
  4. Транскрипты (`json`/`srt`) таймлайна и монтажа, мастер-WAV (`timestretch.mix_clips_on_timeline`), отчёт.
- **Проверка:**
  - автоматическая — круговое преобразование: разбор написанного XML обратно в интервалы, они должны совпадать с `Sequence` (`fcpxml_intervals` уже есть, для xmeml пишется аналог);
  - ручная — чек-лист импорта (раздел 8.4).

### 5.8. `program` — черновой мастер (новое)

- **Когда:** нужны рилсы (они режутся из мастера) или пользователь включил «отрендерить черновик».
- **Как:** ffmpeg по `EditList`. Для каждого `KeepRange` берётся видео выбранной камеры (вход файла через `Placement`) и синхронизированный голос (или рекордер со сдвигом). Куски склеиваются с кроссфейдом звука 10 мс, NVDEC → NVENC с высоким битрейтом, выбор ffmpeg — `core/ffmpeg.py` (Forge). Рядом пишется `program.transcript.json` — транскрипт таймлайна, пересчитанный во время монтажа, без Whisper.
- **Зачем:**
  - рилсы и тексты работают на одном файле — так Forge устроен сейчас, и его анализ и нарезка переносятся без переделки;
  - пользователь может посмотреть черновик без монтажной программы.
- **GPU:** NVENC.

### 5.9. `text` — вычитка и статья

- **proofread:** Forge `stages/proofread_stage.py` → `stages/text/proofread.py`, **как есть** (guardrail `is_correction_safe`, батчи, глоссарий). Вход — транскрипт таймлайна. Тайминги переносятся на исправленный текст через `core/transcript_align.py` (Forge `utils/word_alignment.py`). Результат идёт в `reels` и `article`, а `roughcut` его **не** использует.
- **article:** Forge `stages/article_stage.py` → `stages/text/article.py`, **как есть** (проверки `check_faithfulness`, разделы, имена спикеров). Вход — вычитанный транскрипт **монтажа**, чтобы дубли не попали в статью дважды.
- **term_check:** Forge `analysis/term_check.py` → `stages/text/term_check.py`.
- **Промпты** — `resources/prompts/{ru,en}/` без изменений.
- **GPU:** LLM (общая сессия).

### 5.10. `reels` — рилсы

Два подэтапа, потому что между ними должен успеть выключиться llama-server.

- **`reels-select`** (LLM). Forge `stages/analyze_stage.py` + весь `analysis/` → `stages/reels/select.py` и `stages/reels/analysis/`, **как есть**. Вход:
  - вычитанный транскрипт мастера;
  - `diarization.json` (из `speakers`, пересчитанный во время монтажа);
  - метаданные эпизода, если вход из YouTube.

  Обёртку `scripts/analyze.py` заменяет `run(ctx)`. Выход — `moments.json`, `reels_summary.md`.
- **`reels-render`** (TORCH + NVENC + WHISPER). Forge `scripts/video_processor.py` → `stages/reels/render.py`. Вход — `program.mp4`. Вместе с ним переезжают:
  - `utils/burned_subtitles.py`, `subtitle_layout.py`, `subtitle_presets.py`, `subtitle_sync.py` → `stages/reels/subtitles/`;
  - `utils/clip_intervals.py`, `reel_markdown.py` → `stages/reels/`;
  - `utils/face_*`, `active_speaker.py` → `vision/`;
  - перераспознавание каждого клипа для субтитров (`subtitle_sync`) — через общий `WhisperEngine`.
- **Позже (фаза 7):** рилсы прямо из исходников. Для вертикального кадра берётся ракурс камеры спикера, а не кроп мастера.

---

## 6. Карта переноса: что, откуда, куда, как

Как переносится файл:

- **как есть** — `git mv` + правка импортов; логика не меняется;
- **адаптировать** — меняется интерфейс: вход/выход, настройки вместо словарей, `run(ctx)` вместо `main(argv)`;
- **разрезать** — файл распадается на несколько модулей;
- **заменить** — пишется заново, старый код служит образцом;
- **не переносить** — остаётся в истории или в `contrib/`.

Все переносы делаются через `git mv`, чтобы `git log --follow` вёл историю.

### 6.1. WhisperSync

| Откуда (`whispersync/…`) | Куда (`studio/…`) | Как | Заметки |
|---|---|---|---|
| `models.py` | `core/transcript.py` (Word, Segment, Transcript); `stages/sync/models.py` (Anchor, AlignmentMap); `stages/roughcut/models.py` (Take, RetakeGroup); `core/timeline.py` (MediaClip, SyncPlan → Clip, Timeline) | разрезать | `SyncResult` заменяется `StageOutput` |
| `config.py` | `core/settings/` (механизм проверки) + `SyncSettings`, `TranscribeSettings` | разрезать | поля без изменений, только разложить по разделам; `load_config` → загрузчик YAML-слоёв |
| `engine/acoustic.py` | `stages/sync/acoustic.py` | как есть | |
| `engine/matcher.py` | `stages/sync/matcher.py` | как есть | `WhisperSyncConfig` → `SyncSettings` |
| `engine/timestretch.py` | `stages/sync/timestretch.py` | как есть | `mix_clips_on_timeline` используется и в `export` |
| `engine/strategies.py` | `stages/sync/strategies.py` | как есть | + соответствие режимов (`simple` → 1, `complex` → 3) |
| `engine/pipeline.py` | `scan`, `transcribe`, `sync/*`, `roughcut`, `export` | разрезать | по таблице в 5.3. `run_pipeline` и `_run_pipeline_locked` заменяются `stages/sync/stage.py` (обвязка заново, функции как есть) |
| `engine/transcriber.py` | `stages/transcribe/engine.py` | адаптировать | основа единого движка; + функции из Forge `transcribe_stage.py` |
| `engine/transcript_export.py` | `core/transcript.py` | как есть | единый (де)сериализатор формата |
| `engine/media.py` | `core/media.py` | как есть | |
| `engine/proc.py` | `core/proc.py` | как есть | |
| `engine/naming.py` | `core/naming.py` | как есть | используется сканером |
| `engine/sources.py` | `core/sources.py` | как есть | id ассетов |
| `engine/workspace.py` | `core/workspace.py` | как есть | + семантика Forge `run_lock` для `batch` |
| `engine/retakes.py` | `stages/roughcut/retakes.py` | как есть | |
| `engine/self_check.py` | `stages/sync/self_check.py` | как есть | |
| `engine/verify.py` | `stages/sync/verify.py` | как есть | |
| `engine/separation.py` | `stages/sync/ambience.py` + `modules/manager.py` | разрезать | поиск `.sep-venv` (`sep_venv_candidates`, `separator_python`) обобщается в менеджер модулей |
| `engine/enhance.py` | `stages/sync/enhance.py` + `modules/` | разрезать | то же |
| `engine/system_check.py` | `core/capabilities.py` + `cli doctor` | адаптировать | слить с Forge `preflight.py` |
| `logging_setup.py` | `core/logging.py` | адаптировать | слить с Forge `utils/logging_utils.py` |
| `cli.py` | `cli/main.py` | заменить | флаги → `--set`; старые флаги — псевдонимы на одну версию |
| `app.py` | `app.py` | адаптировать | |
| `gui/main_window.py` | `gui/main_window.py` + `gui/screens/*` | разрезать | переиспользовать: drag&drop, восстановление состояния, `_install_quiet_message_handler`, `load_stylesheet`, закрытие с отменой |
| `gui/worker.py` | `gui/bridge.py` | заменить | QThread с конвейером в процессе → QProcess + события; веса прогресса (`overall_progress`) — как есть |
| `gui/widgets/drop_zone.py`, `log_view.py`, `timeline_preview.py`, `strategy_diagram.py`, `sync_simulator.py`, `help_page.py` | `gui/widgets/` | как есть | `timeline_preview` дорисовывает резы и смены камер (фаза 3–4) |
| `gui/widgets/settings_dialog.py` | `gui/screens/settings.py` | адаптировать | становится экраном всех настроек |
| `gui/theme.qss` | `gui/theme.qss` | как есть | |
| `tools/verify_sync.py` | `cli verify` | адаптировать | |
| `whispersync.spec` | `packaging/pyinstaller/studio.spec` | адаптировать | |
| `setup_venv.sh`, `setup_sep_venv.sh`, `requirements-sep.txt` | `modules/recipes/` | адаптировать | |
| `tests/*` (378 тестов) | `tests/core`, `tests/sync`, `tests/roughcut`, `tests/export`, `tests/gui` | как есть | правка импортов; тесты GUI-воркера переписываются под `bridge` |
| `README*.md` (разделы «Sync Strategies», «Retake», «Self-Check», «Voice Enhancement», «Troubleshooting») | `docs/sync.md`, `docs/troubleshooting.md` | адаптировать | |
| `PROJECT_ANALYSIS.md`, `PROJECT_AUDIT_2026-09-08.md`, `TODO_project.md`, `CHANGELOG.md` | `docs/history/whispersync/` | как есть | архив |
| `docs/images/*` | `docs/images/` | как есть | скриншоты обновить после фазы 1 |

### 6.2. Podcast Reels Forge

| Откуда (`podcast_reels_forge/…`) | Куда (`studio/…`) | Как | Заметки |
|---|---|---|---|
| `pipeline.py` | разные места, см. ниже | разрезать | |
| ↳ `PipelineIO`, `pick_input_file`, `find_input_queue`, `_ensure_audio_companions`, `_ensure_mp3_companion` | `stages/scan`, `core/media.py` | адаптировать | ввод одного файла — частный случай скана |
| ↳ `_LlamaSession`, `_kill_llama_server`, `_autotune_llama_cpp_conf`, `_merge_llama_cpp_conf`, `_get_model_overrides`, `_prompt_variant_for_model` | `llm/session.py` | как есть | |
| ↳ `_has_cuda`, `_gpu_vram_gb`, `_cpu_count`, `_autotune_video_threads` | `core/capabilities.py` | как есть | |
| ↳ `_PipelineRun`, `EpisodeState`, `stage_*` | `stages/runner.py`, `Project`, `run()` этапов | заменить | логика каждого `stage_*` уходит в `run()` своего этапа |
| ↳ `run_module` | `stages/runner.py` | адаптировать | + чтение событий JSON-lines |
| ↳ `PIPELINE_STAGES`, `resolve_stages` | `stages/planner.py` | заменить | `--only`/`--skip` сохраняются |
| ↳ `_youtube_conf`, `_run_youtube_fetch` | `stages/fetch` | адаптировать | |
| `config.py`, `utils/config_loader.py` | `core/settings/` | заменить | словари → dataclass'ы с проверкой; оверлей `*.local.yaml` сохраняется |
| `autonomy.py` | `cli/batch.py` | адаптировать | лог-файл, отчёт, уведомления |
| `run_report.py` | `core/report.py` | как есть | |
| `preflight.py` | `core/capabilities.py` | адаптировать | слить с `system_check` |
| `cli.py`, `__main__.py`, корневые `start_forge.py`, `transcribe_input_audio.py`, `rerender_videos.py` | `cli/main.py` | заменить | перезапуск в venv из `start_forge.py` → лаунчер; `rerender` → `studio run --only reels-render` (отпечатки делают остальное) |
| `stages/transcribe_stage.py` | `stages/transcribe/` | разрезать | см. 5.2; CLI и `parse_args` — не переносить |
| `stages/proofread_stage.py` | `stages/text/proofread.py` | адаптировать | `main(argv)` → `run(ctx)` |
| `stages/article_stage.py` | `stages/text/article.py` | адаптировать | то же |
| `stages/analyze_stage.py` | `stages/reels/select.py` | адаптировать | то же; асинхронный код как есть |
| `stages/fetch_stage.py`, `scripts/fetch_youtube.py`, `sources/*` | `stages/fetch/` | адаптировать | |
| `stages/video_stage.py` | `stages/reels/` | как есть | |
| `analysis/speaker_turns.py` | `stages/speakers/turns.py` | как есть | общий для text/reels/roughcut |
| `analysis/transcript_index.py` | `core/transcript_index.py` | как есть | нужен и roughcut |
| `analysis/audio_features.py` | `core/audio_spans.py` (разбор silencedetect/volumedetect) + `stages/reels/analysis/` (аннотация кандидатов) | разрезать | |
| `analysis/term_check.py` | `stages/text/term_check.py` | как есть | |
| остальные `analysis/*` (chunking, contracts, decisions, metadata, ranking, scoring, serializers, validation, candidate_extraction) | `stages/reels/analysis/` | как есть | |
| `llm/providers.py`, `llm/schemas.py` | `llm/` | как есть | |
| `utils/llama_cpp_service.py` | `llm/server.py` | как есть | |
| `utils/json_utils.py` | `llm/json_utils.py` | как есть | |
| `scripts/diarize.py` | `stages/speakers/pyannote.py` | адаптировать | запуск в окружении `diarization` |
| `scripts/video_processor.py` | `stages/reels/render.py` | адаптировать | вход — `program.mp4`; `ffmpeg_cut`, превью, экспорт webm/gif/аудио — как есть |
| `scripts/rerender_videos.py` | — | заменить | повторный запуск этапа |
| `scripts/evaluate_prompts.py` | `tools/evaluate_prompts.py` | как есть | инструмент разработчика |
| `scripts/host_memory.py`, `utils/host_memory.py` | `contrib/hooks/host_memory/` | адаптировать | хук `before_gpu`/`after_gpu`, а не часть ядра |
| `scripts/transcribe.py`, `scripts/analyze.py` | — | не переносить | тонкие обёртки |
| `utils/face_track.py`, `face_crop.py`, `active_speaker.py` | `vision/` | как есть | используются и `reels`, и (позже) `roughcut` |
| `utils/burned_subtitles.py`, `subtitle_layout.py`, `subtitle_presets.py`, `subtitle_sync.py` | `stages/reels/subtitles/` | как есть / адаптировать | `subtitle_sync.load_model` → общий `WhisperEngine` |
| `utils/word_alignment.py` | `core/transcript_align.py` | как есть | |
| `utils/clip_intervals.py` | `stages/reels/clip_intervals.py` | как есть | |
| `utils/reel_markdown.py` | `stages/reels/markdown.py` | как есть | |
| `utils/media_qa.py` | `core/media_qa.py` | как есть | |
| `utils/ffmpeg.py` | `core/ffmpeg.py` | как есть | |
| `utils/fingerprint.py` | `core/fingerprint.py` | как есть | `StageState` — основа отпечатков исполнителя |
| `utils/env.py` | `core/env.py` | как есть | |
| `utils/run_lock.py` | `core/workspace.py` | заменить | на `output_lock` WhisperSync (pid, перехват устаревшей блокировки) |
| `utils/logging_utils.py` | `core/logging.py` | заменить | |
| `prompts/{ru,en}/*` | `resources/prompts/{ru,en}/` | как есть | |
| `assets/fonts/*` (+ licenses), `assets/models/*` | `resources/fonts/`, `resources/models/` | как есть | модели — кандидаты на скачивание при первом запуске, а не в репозитории |
| `assets/subtitles/style-editor.html`, `gui/assets/editor.js`, `editor.css`, `subtitle-presets.js`, `gui/subtitles.html` | `resources/subtitle_editor/` | адаптировать | открывается из приложения в браузере; читает и пишет раздел `reels.subtitles` |
| `gui/*.html`, `gui/assets/app.js`, `app.css` (сборщик `config.yaml`) | — | не переносить | заменяется экраном настроек в Qt |
| `tests/*` (560 тестов) | `tests/text`, `tests/reels`, `tests/speakers`, `tests/fetch`, `tests/core`, `tests/llm` | как есть | правка импортов; `test_pipeline.py`, `test_pipeline_efficiency.py`, `test_unattended_runs.py`, `test_stage_decisions.py` переписываются под планировщик и исполнитель |
| `docs/USER_GUIDE.md`, `CONFIGURATION.md`, `AUTONOMOUS.md`, `PROMPTS.md`, `DEVELOPMENT.md` | `docs/` | адаптировать | конфигурация — под новые разделы, AUTONOMOUS → `batch` |
| `docs/COMPETITOR_REVIEW.md`, `ANALYSIS_REPORT.md`, `AUTONOMY_REVIEW.md`, `CHANGELOG.md` | `docs/history/forge/` | как есть | архив |
| `local/*` (не в git) | остаётся на хосте | адаптировать | `forge-local.sh`, `forge-night.sh` вызывают `studio run`/`studio batch`; `pedobraz_pause.py` подключается хуком |

### 6.3. Соответствие настроек (для `migrate_settings.py`)

| Было | Стало |
|---|---|
| WhisperSync: `model`, `device`, `compute_type`, `language`, `vad_filter`, `beam_size`, `batch_size`, `best_of`, `patience`, `condition_on_previous_text`, `repetition_penalty`, `no_repeat_ngram_size`, `transcribe_mode`, `quality_beam_size`, `initial_prompt`, `use_cache`, `cache_dir`, `cache_max_age_days` | `transcribe.*` |
| WhisperSync: `default_strategy`, `recorder_mode`, `audio_source_camera`, `timebase_source`, `camera_av_offset_ms*`, `crossfade_*`, `output_audio_format`, `stretch_method`, `seam_snap_max_s`, `render_workers`, `min_anchors`, `anchor_*`, `phrase_gap_threshold`, `match_window_margin`, `seed_*`, `acoustic_*`, `gcc_eps`, `alignment_*`, `boundary_flex`, `flex_*`, `pause_duck_*`, `ambience_*`, `voice_enhance`, `self_check_*`, `voice_segment_minutes`, `reuse_source_dir` | `sync.*` |
| WhisperSync: `detect_retakes`, `retake_*` | `roughcut.retakes.*` |
| WhisperSync: `fcpxml_version`, `save_transcripts`, `render_master_wav`, `output_dir` | `export.*` |
| WhisperSync: `video_exts`, `audio_exts`, `probe_timeout_s` | `scan.*` |
| Forge: `transcription` | `transcribe.*` |
| Forge: `diarization` | `speakers.*` |
| Forge: `proofread`, `article` | `text.*` |
| Forge: `processing`, `subtitles`, `video`, `audio`, `exports`, `prompts` | `reels.*` |
| Forge: `llama_cpp` | `llm.*` |
| Forge: `autonomy`, `cli` | `batch.*` |
| Forge: `youtube` | `fetch.*` |
| Forge: `cache` | `transcribe.cache` + `llm.cache` |
| Forge: `host_memory` | `contrib/hooks/host_memory` + `hooks.*` |
| Forge: `paths` | рабочая папка проекта + `batch.*` |

### 6.4. Что пишется с нуля

| Компонент | Объём | Фаза |
|---|---|---|
| `core/project.py`, `core/timeline.py` (модель, три шкалы времени, круговые тесты) | средний | 1 |
| `stages/base.py`, `planner.py`, `runner.py`, `worker.py`, `events.py` | средний | 1 |
| `stages/scan` (классификация, группировка, главы, дорожки рекордера) | средний | 1–2 |
| `gui/screens/*`, `gui/bridge.py` | крупный | 1–3 |
| `cli/main.py` | малый | 1 |
| Режимы `camera`/`simple`/`auto`, экспорт без рендера при `k ≈ 1` | средний | 2 |
| `stages/timeline` | малый | 2 |
| `export/sequence.py`, `export/xmeml.py` | средний | 2 |
| `stages/roughcut` (детекторы, `EditList`, просмотр решений) | крупный | 3 |
| `stages/program` | малый–средний | 3 |
| `speakers/mics.py`, мультикамера (правила, привязка, `mc-clip`) | средний | 4 |
| `modules/manager.py`, установщики, мастер первого запуска | средний | 6 |
| LLM-детекторы чернового монтажа | средний | 6 |

---

## 7. Фазы работ

После каждой фазы есть работающее приложение. Фаза закрыта, когда выполнены все критерии готовности.

### Фаза 0. Подготовка

**Задачи:**

- [ ] Принять решения из раздела 2.
- [ ] Заморозить функции в обоих репозиториях: только исправления ошибок до их архивации.
- [ ] Создать репозиторий и импортировать оба проекта с историей:
  ```bash
  git init studio && cd studio && git commit --allow-empty -m "chore: start"
  git subtree add --prefix=legacy/whispersync /home/borm/VibeCoding/WhisperSync main
  git subtree add --prefix=legacy/forge "/srv/storage/docs/Podcast Reels Forge" main
  ```
- [ ] CI: оба набора тестов запускаются в своих каталогах без изменений; ruff, black и mypy в режиме WhisperSync для нового кода; для `legacy/` — их текущие правила.
- [ ] Скрипт `tools/make_fixtures.py`: синтетический проект из двух «камер» и рекордера. Речь — локальный TTS (espeak-ng или Piper), дрейф — `asetrate` на 0.05–0.1 %, сдвиг, глава GoPro, пауза, повтор фразы (для дублей). Небольшой, детерминированный, без чужих записей.
- [ ] Выбрать реальную запись ПедОбраза для приёмки (не в репозиторий): 2 камеры + рекордер, 20–40 минут.

**Готово, когда:** 938 тестов зелёные в новом репозитории, фикстуры генерируются одной командой.

### Фаза 1. Ядро и каркас: «отдал папку — получил проект для Final Cut»

**Задачи:**

- [ ] `core/`: перенос модулей WhisperSync «как есть» (`media`, `proc`, `workspace`, `naming`, `sources`, `transcript`), Forge `fingerprint`, `ffmpeg`, `env`, `report`; новые `project.py`, `timeline.py`, `settings/` (механизм и разделы `scan`, `transcribe`, `sync`, `export`), `capabilities.py`.
- [ ] `stages/base.py`, `planner.py`, `runner.py`, `worker.py`, `events.py` — с процессами-исполнителями с самого начала.
- [ ] `scan` v1: подпапки, шаблоны имён, теги, главы, роли.
- [ ] `transcribe`: `WhisperEngine` из WhisperSync, выбор файлов по `decide()`.
- [ ] `sync`: режим `complex` (текущее поведение WhisperSync); шаги из таблицы 5.3; эмбиенс, улучшение голоса и самопроверка — как опции.
- [ ] `export`: FCPXML через старый вход (`SyncPlan`); на `Sequence` переходим в фазе 2.
- [ ] `gui`: экраны «Старт» (выбор или перетаскивание папки), «Материал» (только просмотр), «План», «Работа» (прогресс, таймлайн, лог, отмена), «Результат»; `bridge.py` на QProcess.
- [ ] `cli`: `scan`, `plan`, `run`, `doctor`.
- [ ] Перенос тестов WhisperSync в новую структуру, удаление `legacy/whispersync` после переноса.

**Готово, когда:**

- на фикстурах и на реальной записи интервалы FCPXML (`fcpxml_intervals`) совпадают с выдачей старого WhisperSync на тех же входах при тех же настройках;
- все перенесённые тесты WhisperSync зелёные;
- запуск из интерфейса и из `studio run <папка>` даёт одинаковый результат;
- повторный запуск без изменений ничего не пересчитывает (все этапы `reuse`).

### Фаза 2. Режимы синхронизации, общий транскрипт, Premiere

**Задачи:**

- [ ] Режимы `camera`, `simple`, `auto` (правило из 5.3), экспорт без рендера при `k ≈ 1`.
- [ ] Размещение камер относительно друг друга без рекордера.
- [ ] Этап `timeline` (транскрипт таймлайна без повторного Whisper).
- [ ] Из Forge `transcribe_stage.py`: разбиение `.srt` по предложениям, уверенность сегментов, глоссарий → `hotwords`/`initial_prompt`.
- [ ] `export/sequence.py`; FCPXML переводится на `Sequence`; `xmeml.py` + круговые тесты.
- [ ] Экран «Материал» становится редактируемым (перетаскивание между группами, роли, имена), правки переживают повторный скан.
- [ ] Многодорожечные рекордеры в скане (`RecorderTrack`).

**Готово, когда:**

- один и тот же проект открывается в Final Cut и Premiere по чек-листу 8.4, синхронизация совпадает на глаз и на слух в начале, середине и конце;
- `auto` выбирает `simple` на короткой записи с общими часами и `complex` на длинной с дрейфом (на фикстурах — автоматически, на реальной записи — вручную);
- WhisperSync можно архивировать: всё, что он умел, есть в новом приложении.

### Фаза 3. Черновой монтаж v1

**Задачи:**

- [ ] `core/audio_spans.py` (общие паузы и энергия).
- [ ] `roughcut`: паузы, дубли, начало и конец — режут; паразиты и нераспознанные звуки — маркеры.
- [ ] `EditList`, режимы `cut` и `markers`; экспорт резов и маркеров в оба формата; пробный `disable` на реальном импорте.
- [ ] `program`: черновой мастер, транскрипт мастера.
- [ ] Экран «Решения монтажа»: список резов, прослушивание, принять или отклонить, переэкспорт без пересчёта.
- [ ] `timeline_preview` показывает резы.
- [ ] Проверка `keep_fillers` (влияние `initial_prompt` на «ээ»/«мм») на реальной записи.

**Готово, когда:**

- ни один рез не попадает внутрь слова. Проверка автоматическая: мастер перераспознаётся, и слова сравниваются с транскриптом монтажа (идея и код самопроверки `self_check.diagnose_words`);
- на реальной записи час материала сокращается без «рваных» мест — оценка на слух по чек-листу;
- пороги пауз вынесены в настройки и пресеты.

### Фаза 4. Спикеры и мультикамера

**Задачи:**

- [ ] `speakers`: `mics` (новое), `pyannote` (перенос), `turns` (перенос), имена спикеров вручную.
- [ ] Привязка камера ↔ спикер и «общий план» на экране «Материал».
- [ ] Правила переключения камер (5.6), `KeepRange.camera_id`.
- [ ] FCPXML: мультикам-клип (`mc-clip`), переключение ракурсов в Final Cut работает. xmeml: «стопка» дорожек.
- [ ] `program` учитывает выбранную камеру.

**Готово, когда:** запись «2 камеры + 2 петлички» даёт в Final Cut мультикам с разумными переключениями, и ракурс на любом куске меняется штатно (клавишами или в инспекторе).

### Фаза 5. Тексты и рилсы

**Задачи:**

- [ ] `llm/` (провайдеры, схемы, сервер, сессия) — перенос.
- [ ] `text`: вычитка, статья, `term_check` — перенос, `run(ctx)`.
- [ ] `reels-select`: `analyze_stage` + `analysis/` — перенос; вход — транскрипт мастера и `diarization.json` в шкале монтажа.
- [ ] `reels-render`: `video_processor` + субтитры + `vision/` — перенос; вход — `program.mp4`.
- [ ] `fetch` (YouTube) — перенос; проект из ссылки.
- [ ] `cli batch` (из `autonomy.py`): очередь проектов или канал, отчёты, уведомления, коды возврата для планировщика; хуки `before_*`/`after_*`; перевод `local/forge-local.sh` и `forge-night.sh` на новый CLI.
- [ ] Редактор субтитров из приложения (браузер).
- [ ] Перенос тестов Forge, удаление `legacy/forge`.

**Готово, когда:**

- все 560 перенесённых тестов зелёные;
- на одном и том же `program.mp4` и с тем же кэшем LLM `moments.json` совпадает с выдачей старого Forge, а рилсы по содержанию те же;
- ночной прогон канала (`batch`) отработал хотя бы одну ночь без вмешательства;
- Forge можно архивировать.

### Фаза 6. LLM в монтаже, модули, установщики

**Задачи:**

- [ ] LLM-детекторы чернового монтажа: лучший дубль по подаче, оговорки, начало разговора, паразиты по смыслу. По образцу `scout → cleanup → judge`: JSON-грамматика, сверка с транскриптом, кэш ответов.
- [ ] `modules/manager.py` + рецепты; экран «Модули» в настройках; блокировки этапов со ссылкой «установить модуль».
- [ ] Скачивание моделей (Whisper, YuNet, Light-ASD, GGUF) при первом использовании с прогрессом.
- [ ] Установщики PyInstaller под Linux, Windows и macOS (базовая часть); мастер первого запуска (ffmpeg, CUDA, модули).
- [ ] Автонастройка Whisper по видеопамяти (как `_autotune_llama_cpp_conf`).

**Готово, когда:** на чистой машине установщик → первый запуск → проект из фикстур проходит без ручной установки чего-либо, кроме драйвера NVIDIA.

### Фаза 7. Дальше (по приоритету)

- Рилсы из исходников: ракурс камеры спикера, а не кроп мастера.
- Авто-привязка камер к спикерам (`vision/`).
- Экспорт OTIO (DaVinci Resolve).
- Mac: Whisper через MLX, рендер через VideoToolbox.
- Встраивание редактора субтитров (QWebEngineView).
- Пробелы из обзора конкурентов: нормализация громкости −14 LUFS, обложки, крючок-заголовок, аудиограмма для аудио-эпизодов, тепловая карта «most replayed» с YouTube.

---

## 8. Тестирование и приёмка

### 8.1. Модульные тесты

- 378 тестов WhisperSync и 560 тестов Forge переносятся с правкой импортов. Тесты оркестраторов (Forge `test_pipeline*.py`, `test_unattended_runs.py`, `test_stage_decisions.py`; WhisperSync `test_pipeline.py`, `test_worker_progress.py`, `test_gui_cancel.py`) переписываются под планировщик, исполнитель и мост.
- Новые обязательные тесты:
  - круговые преобразования трёх шкал времени (`core/timeline`);
  - решения планировщика: таблица «содержимое папки + настройки → план» на десятке сценариев (одна камера без рекордера, две камеры + рекордер, только звук, YouTube, нет CUDA, нет модуля);
  - круговые тесты FCPXML и xmeml (`Sequence` → XML → интервалы);
  - детекторы чернового монтажа на синтетических транскриптах;
  - `speakers.mics` на синтетических дорожках с перекрёстной слышимостью.

### 8.2. Сквозные тесты на фикстурах

`tools/make_fixtures.py` → `studio run <фикстура> --json` в CI (без GPU: `compute.allow_cpu=true`, модель Whisper `tiny`). Проверяется:

- найденные устройства;
- оценка дрейфа в пределах допуска;
- интервалы в XML;
- резы пауз и дублей на ожидаемых местах.

### 8.3. Регрессия против старых версий

- **Фаза 1–2:** старый WhisperSync и новое приложение на одних и тех же входах дают одинаковые интервалы FCPXML и одинаковые WAV (сравнение по хэшу или с допуском по сэмплам).
- **Фаза 5:** старый Forge и новое приложение на одном `program.mp4` с общим кэшем LLM дают одинаковый `moments.json`.

### 8.4. Ручная приёмка импорта (чек-лист)

Для Final Cut Pro (версия ___) и Premiere Pro (версия ___), на реальной записи:

- [ ] файл открывается без ошибок и предупреждений, все медиа найдены (нет «offline»);
- [ ] частота кадров и размер последовательности верные;
- [ ] синхронизация губ в начале, середине и конце каждого клипа;
- [ ] звук камеры выключен там, где есть синхронизированный голос, и есть там, где его нет;
- [ ] резы не внутри слов, маркеры видны и подписаны;
- [ ] роли (Final Cut) или имена дорожек (Premiere) понятны;
- [ ] мультикам: ракурс переключается штатно (Final Cut), стопка дорожек корректна (Premiere);
- [ ] после переноса папки с материалом проект находит медиа (относительные пути в FCPXML, повторное связывание в Premiere).

Для проверки нужны Mac с Final Cut и машина с Premiere. Без них фазы 2–4 нельзя считать закрытыми.

---

## 9. Риски

| Риск | Чем грозит | Что делаем |
|---|---|---|
| Объём работ растёт без конца | приложение никогда не «готово» | каждая фаза выпускаемая; заморозка функций в старых репозиториях; фаза 7 — только после 1–6 |
| Нюансы XML для Premiere (NTSC, каналы звука, связи) | проект открывается криво | круговые тесты + ручной чек-лист на каждой фазе; начать с простых случаев (25/50 к/с, стерео) |
| Whisper выбрасывает «ээ»/«мм» | паразиты не находятся | `initial_prompt` с примерами, детектор «энергия без слов», по умолчанию только маркеры |
| Сканер путает устройства | неверная синхронизация | группировка по подпапкам в приоритете; экран «Материал» с ручной правкой до запуска; предупреждения |
| Нехватка видеопамяти (Whisper + llama + torch) | падения по OOM | этапы с GPU строго по одному в отдельных процессах; одна сессия llama на группу; хуки освобождения памяти |
| Размер установки (torch, CUDA) | неподъёмный установщик | модули, которые ставятся при первом включении (фаза 6) |
| Ночные прогоны Forge перестанут работать | регресс в привычном процессе | старый Forge работает до закрытия фазы 5; `batch` проверяется ночным прогоном |
| Расхождение шкал времени | сдвиги на секунды в экспорте и рилсах | единый модуль преобразований, круговые тесты, запрет «ручной арифметики» времени в этапах (проверка на ревью) |
| Нет доступа к Final Cut или Premiere для проверки | фазы не закрываются | заранее решить, на каких машинах и версиях проверяем |
| Лицензия | юридическая неясность при публикации | решить до первого публичного коммита (раздел 2) |

---

## 10. Первые шаги

1. Ответить на вопросы раздела 2 (минимум 1, 2, 5 — без них не создать репозиторий).
2. Фаза 0: репозиторий, `git subtree`, CI, фикстуры.
3. Фаза 1 начинается с `core/project.py` + `core/timeline.py` + `stages/base.py`: это фундамент, который потом дорого менять. Их стоит отдельно обсудить до того, как переносить остальное.

---

## 11. Приложения: форматы

### 11.1. `project.json` (сокращённо)

```json
{
  "version": 1,
  "source_dir": ".",
  "assets": [
    {"id": "dji-0838", "path": "camA/DJI_0838.MP4", "kind": "video", "role": "camera",
     "device": "DJI Osmo Pocket 3", "group_id": "cam-a", "chapter_of": null,
     "created_at": "2026-09-30T18:02:11", "manual": {}},
    {"id": "zoom0001-tr1", "path": "ZOOM0001/ZOOM0001_Tr1.WAV", "kind": "audio",
     "role": "recorder", "device": "ZOOM H6", "group_id": "rec-1", "manual": {}}
  ],
  "cameras":   [{"id": "cam-a", "name": "Камера A", "asset_ids": ["dji-0838"], "speaker": "S1", "is_wide": false}],
  "recorders": [{"id": "rec-1", "name": "ZOOM H6", "asset_ids": ["zoom0001-tr1", "zoom0001-tr2"],
                 "tracks": [{"index": 1, "channels": [0], "speaker": "S1"}, {"index": 2, "channels": [0], "speaker": "S2"}]}],
  "placements": [
    {"asset_id": "dji-0838", "offset_s": 0.0, "in_s": 0.0, "duration_s": 1532.4, "k": 1.0, "provenance": "metadata"},
    {"asset_id": "zoom0001-tr1", "offset_s": -12.731, "in_s": 0.0, "duration_s": 1601.0, "k": 1.000062,
     "provenance": "acoustic", "evidence": {"inliers": 41, "residual_ms": 6.2, "coverage": 0.93}}
  ],
  "transcripts": {"zoom0001-tr1": "stages/transcribe/zoom0001-tr1.json", "timeline": "stages/timeline/transcript.json"},
  "speaker_names": {"S1": "Иван", "S2": "Мария"},
  "edit": "stages/roughcut/edit.json",
  "stage_states": {"sync": {"fingerprint": "3f9c…", "status": "ok", "finished_at": "2026-10-05T21:14:03"}}
}
```

### 11.2. События исполнителя (JSON-lines в stdout процесса этапа)

```json
{"t": "start",    "stage": "sync"}
{"t": "progress", "stage": "sync", "value": 0.42, "message": "Клип DJI_0839: рендер голоса"}
{"t": "timeline", "stage": "sync", "clips": [{"asset_id": "dji-0839", "offset_s": 1532.4, "status": "working", "speed": 0.0011}]}
{"t": "warning",  "stage": "sync", "text": "GX010024: мало якорей, взят акустический сдвиг"}
{"t": "artifact", "stage": "sync", "path": "stages/sync/voice/dji-0839_voice.wav"}
{"t": "done",     "stage": "sync", "status": "ok", "output": "stages/sync/output.json"}
```

Ход процесса пишется в лог отдельно (stderr → `logs/`). В stdout — только события, чтобы их всегда можно было разобрать.

### 11.3. `edit.json`

```json
{
  "mode": "cut",
  "keep": [{"start": 3.20, "end": 61.84, "camera_id": "cam-a"}, {"start": 62.91, "end": 140.02, "camera_id": "cam-b"}],
  "cuts": [
    {"start": 0.0,   "end": 3.20,  "reason": "head",   "confidence": 1.0,  "note": "до первого слова", "accepted": true},
    {"start": 61.84, "end": 62.91, "reason": "pause",  "confidence": 0.95, "note": "пауза 1.47 с → 0.40 с", "accepted": true},
    {"start": 88.10, "end": 94.55, "reason": "retake", "confidence": 0.8,  "note": "дубль 1 из 2", "accepted": true}
  ],
  "markers": [{"at": 120.3, "kind": "filler", "text": "«как бы»"}]
}
```

### 11.4. Мультикам в FCPXML (скелет)

```xml
<resources>
  <format id="r1" frameDuration="1/25s" width="1920" height="1080"/>
  <asset id="r2" src="camA/DJI_0838.MP4" .../>
  <asset id="r3" src="camB/GX010024.MP4" .../>
  <asset id="r4" src="_studio/stages/sync/voice/dji-0838_voice.wav" .../>
  <media id="r10" name="Эпизод — мультикам">
    <multicam format="r1">
      <mc-angle name="Камера A" angleID="camA"><asset-clip ref="r2" offset="0s" duration="…"/></mc-angle>
      <mc-angle name="Камера B" angleID="camB"><gap offset="0s" duration="…"/><asset-clip ref="r3" lane="1" offset="…" duration="…"/></mc-angle>
      <mc-angle name="Голос"    angleID="voice"><asset-clip ref="r4" offset="0s" duration="…"/></mc-angle>
    </multicam>
  </media>
</resources>
<!-- в spine проекта: по mc-clip на каждый KeepRange -->
<mc-clip ref="r10" offset="0s" start="80/25s" duration="1466/25s">
  <mc-source angleID="camA"  srcEnable="video"/>
  <mc-source angleID="voice" srcEnable="audio"/>
</mc-clip>
```

### 11.5. XML для Premiere (xmeml v4, скелет)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE xmeml>
<xmeml version="4">
  <sequence id="seq-1">
    <name>Эпизод — черновик</name>
    <rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>
    <media>
      <video>
        <format><samplecharacteristics><width>1920</width><height>1080</height></samplecharacteristics></format>
        <track>
          <clipitem id="ci-1">
            <name>DJI_0838</name>
            <enabled>TRUE</enabled>
            <start>0</start><end>1466</end><in>80</in><out>1546</out>
            <file id="f-1"><pathurl>file://localhost/…/camA/DJI_0838.MP4</pathurl></file>
            <link><linkclipref>ci-1</linkclipref></link>
            <link><linkclipref>ca-1</linkclipref></link>
          </clipitem>
        </track>
      </video>
      <audio>
        <track>
          <clipitem id="ca-1">
            <file id="f-2"><pathurl>file://localhost/…/dji-0838_voice.wav</pathurl></file>
            <start>0</start><end>1466</end><in>80</in><out>1546</out>
            <sourcetrack><mediatype>audio</mediatype><trackindex>1</trackindex></sourcetrack>
          </clipitem>
        </track>
      </audio>
    </media>
    <marker><name>Пауза 1.47 с → 0.40 с</name><in>1466</in><out>-1</out></marker>
  </sequence>
</xmeml>
```

Скелеты 11.4 и 11.5 — ориентир для реализации. Точный набор обязательных атрибутов сверяется с реальным импортом в фазе 2 (чек-лист 8.4).
