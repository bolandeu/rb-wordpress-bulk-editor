# WordPress Bulk Editor — Makefile
# GNU Make: Windows (scoop install make / choco install make / Git Bash)
#
# Использование:
#   make fetch PROJECT=mysite
#   make async PROJECT=mysite FILE=projects/mysite/data.xlsx SHEETS=posts CONCURRENCY=10
#   make sync  FILE=data.xlsx DRY_RUN=1 VERBOSE=1
#   make llm   PROJECT=mysite FILE=data.xlsx COLUMN=excerpt

# ── Параметры ─────────────────────────────────────────────────────────────────
PROJECT     ?=
FILE        ?=
SHEETS      ?=
COLUMNS     ?=
STATUS      ?=
EXCLUDE     ?=
TYPE        ?= posts
LIMIT       ?=
COLUMN      ?=
POST_TYPE   ?=
OUTPUT      ?=
CONCURRENCY ?= 5
DRY_RUN     ?=
VERBOSE     ?=
OVERWRITE   ?=
FETCH       ?=

# На Windows wp-bulk не попадает в PATH без активации venv.
# uv run находит .venv автоматически — работает везде без активации.
WP = uv run wp-bulk

# ── Составные флаги ───────────────────────────────────────────────────────────
_P      = $(if $(PROJECT),-P $(PROJECT),)
_FILE   = $(if $(FILE),--file "$(FILE)",)
_SHEETS = $(if $(SHEETS),--sheets $(SHEETS),)
_COLS   = $(if $(COLUMNS),--columns $(COLUMNS),)
_STATUS = $(if $(STATUS),--status $(STATUS),)
_EXCL   = $(if $(EXCLUDE),--exclude $(EXCLUDE),)
_LIMIT  = $(if $(LIMIT),--limit $(LIMIT),)
_OUTPUT = $(if $(OUTPUT),--output "$(OUTPUT)",)
_DRY    = $(if $(DRY_RUN),--dry-run,)
_VERB   = $(if $(VERBOSE),--verbose,)
_OVR    = $(if $(OVERWRITE),--overwrite,)

# ── Targets ───────────────────────────────────────────────────────────────────
.PHONY: help \
        fetch sync async validate options taxonomies \
        fetch-media export export-media \
        bulk-delete counter llm reshape vlook \
        image-crop image-compress image-convert

help:
	$(info WordPress Bulk Editor — make targets)
	$(info )
	$(info ОСНОВНЫЕ)
	$(info   make fetch              WordPress -> Excel (все типы и таксономии))
	$(info   make sync               Excel -> WordPress (последовательно))
	$(info   make async              Excel -> WordPress (параллельно, retry, CB))
	$(info   make validate           Проверить структуру Excel без синхронизации)
	$(info   make options            Настройки WordPress (FETCH=1 для выгрузки))
	$(info   make taxonomies         Список таксономий в формате WP_TAXONOMIES=...)
	$(info )
	$(info МЕДИА)
	$(info   make fetch-media        Библиотека медиа -> лист media в Excel)
	$(info   make export             Контент -> HTML файлы + CSV индекс)
	$(info   make export-media       Скачать медиафайлы на диск + CSV)
	$(info )
	$(info УТИЛИТЫ)
	$(info   make bulk-delete        Массовое удаление — требует POST_TYPE=posts)
	$(info   make counter            Слова/символы в контенте -> CSV)
	$(info   make llm                Массовая генерация через LLM — требует COLUMN=excerpt)
	$(info   make reshape            fetch.xlsx -> шаблон -> export.xlsx)
	$(info   make vlook              Привязать файлы к строкам Excel по ID)
	$(info )
	$(info ИЗОБРАЖЕНИЯ)
	$(info   make image-crop         Обрезка под соотношение (RATIO=16:9))
	$(info   make image-compress     Сжатие для веба (QUALITY=80))
	$(info   make image-convert      Конвертация формата (FORMAT=webp))
	$(info )
	$(info ПЕРЕМЕННЫЕ)
	$(info   PROJECT=mysite          Папка проекта (projects/mysite/))
	$(info   FILE=data.xlsx          Excel файл)
	$(info   SHEETS=posts,pages      Список листов)
	$(info   COLUMNS=title,excerpt   Обновлять только эти колонки)
	$(info   STATUS=publish          Фильтр по статусу)
	$(info   CONCURRENCY=10          Параллельных запросов (для async))
	$(info   DRY_RUN=1               Режим симуляции (без изменений))
	$(info   VERBOSE=1               Подробные логи)
	@:

# ── Основные ──────────────────────────────────────────────────────────────────

fetch:
	$(WP) $(_P) fetch $(_FILE) $(_SHEETS) $(_EXCL) $(_STATUS) $(_LIMIT) $(_VERB)

sync:
	$(WP) $(_P) sync $(_FILE) $(_SHEETS) $(_COLS) $(_DRY) $(_VERB)

async:
	$(WP) $(_P) async $(_FILE) $(_SHEETS) $(_COLS) --concurrency $(CONCURRENCY) $(_DRY) $(_VERB)

validate:
	$(WP) $(_P) validate $(_FILE)

options:
	$(WP) $(_P) options $(_FILE) $(if $(FETCH),--fetch,) $(_DRY)

taxonomies:
	$(WP) $(_P) taxonomies

# ── Медиа ─────────────────────────────────────────────────────────────────────

fetch-media:
	$(WP) $(_P) fetch-media $(_FILE) $(_VERB)

export:
	$(WP) $(_P) export $(_OUTPUT) --concurrency $(CONCURRENCY) $(_VERB)

export-media:
	$(WP) $(_P) export-media $(_OUTPUT) --concurrency $(CONCURRENCY) $(_VERB)

# ── Утилиты ───────────────────────────────────────────────────────────────────

bulk-delete:
ifndef POST_TYPE
	$(error POST_TYPE обязателен: make bulk-delete POST_TYPE=posts)
endif
	$(WP) $(_P) bulk-delete --post-type $(POST_TYPE) $(_STATUS) $(_DRY) $(_VERB)

counter:
	$(WP) $(_P) counter --type $(TYPE) $(_STATUS) $(_LIMIT) $(_OUTPUT)

llm:
ifndef COLUMN
	$(error COLUMN обязателен: make llm COLUMN=excerpt)
endif
	$(WP) $(_P) llm --column $(COLUMN) $(_FILE) $(_SHEETS) $(_OVR) $(_DRY) $(_VERB)

reshape:
	$(WP) $(_P) reshape $(_VERB)

vlook:
	$(WP) $(_P) vlook $(_FILE) $(_VERB)

# ── Изображения ───────────────────────────────────────────────────────────────
# RATIO=16:9  STRATEGY=center  QUALITY=80  FORMAT=webp

RATIO    ?=
STRATEGY ?= center
QUALITY  ?= 80
FORMAT   ?= webp

_RATIO  = $(if $(RATIO),--aspect-ratio $(RATIO),)
_STRAT  = --strategy $(STRATEGY)
_QUAL   = --quality $(QUALITY)
_FMT    = --format $(FORMAT)

image-crop:
	$(WP) $(_P) image-crop $(_RATIO) $(_STRAT) $(_DRY) $(_VERB)

image-compress:
	$(WP) $(_P) image-compress $(_QUAL) $(_DRY) $(_VERB)

image-convert:
	$(WP) $(_P) image-convert $(_FMT) $(_QUAL) $(_DRY) $(_VERB)
