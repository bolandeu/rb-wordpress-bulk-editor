# WordPress Bulk Editor — Makefile
# GNU Make: Windows (scoop install make / choco install make / Git Bash)
#
# Использование:
#   make fetch PROJECT=mysite
#   make async PROJECT=mysite FILE=projects/mysite/data.xlsx SHEETS=posts CONCURRENCY=10
#   make sync  FILE=data.xlsx DRY_RUN=1 VERBOSE=1 SKIP_MEDIA=1 SKIP_TAX=1
#   make llm   PROJECT=mysite FILE=data.xlsx COLUMN=excerpt MODEL=gpt-4

# На Windows wp-bulk не попадает в PATH без активации venv.
# uv run находит .venv автоматически — работает везде без активации.
WP = uv run wp-bulk

# ── Общие параметры ───────────────────────────────────────────────────────────
PROJECT     ?=
FILE        ?=
ID          ?=
SHEETS      ?=
SHEET       ?=
COLUMNS     ?=
STATUS      ?=
EXCLUDE     ?=
TYPE        ?= posts
LIMIT       ?=
COLUMN      ?=
POST_TYPE   ?=
OUTPUT      ?=
INPUT       ?=
CONCURRENCY ?= 5
DRY_RUN     ?=
VERBOSE     ?=
OVERWRITE   ?=
FETCH       ?=

# sync / async
SKIP_MEDIA          ?=
SKIP_TAX            ?=
SKIP_EXISTING_MEDIA ?=
DELETE_MEDIA        ?=
NO_UPDATE_EXCEL     ?=
TEST_MODE           ?=
MAX_RETRIES         ?=
API_TIMEOUT         ?=
NO_CB               ?=

# bulk-delete
FORCE ?=

# llm
MODEL      ?=
PROMPT     ?=
LLM_ENDPOINT ?=
LLM_TOKEN  ?=
STRIP_TAGS ?=
MD_TO_HTML ?=
HTML_TO_MD ?=

# reshape
SRC   ?=
TPL   ?=
EXTRA ?=

# vlook
VLOOK_MODE ?=
FOLDER     ?=

# Google Sheets
SHEET_ID      ?=
CREDENTIALS   ?=
NO_UPLOAD_BACK ?=

# image commands
WIDTH      ?=
HEIGHT     ?=
MAX_WIDTH  ?=
MAX_HEIGHT ?=
RATIO      ?=
STRATEGY   ?= center
QUALITY    ?= 80
FORMAT     ?= webp
GRADIENT        ?=
GRADIENT_HEIGHT ?=
WM_TEXT         ?=
WM_IMAGE        ?=
WM_POS          ?= bottom-right
WM_OPACITY      ?=
WM_SIZE         ?=
WM_COLOR        ?=
WM_SCALE        ?=
STRIP_META      ?=
RECURSIVE       ?=
GLOB            ?=

# ── Флаги ─────────────────────────────────────────────────────────────────────
_P            = $(if $(PROJECT),-P $(PROJECT),)
_FILE         = $(if $(FILE),--file "$(FILE)",)
_SHEETS       = $(if $(SHEETS),--sheets $(SHEETS),)
_SHEET        = $(if $(SHEET),--sheet $(SHEET),)
_COLS         = $(if $(COLUMNS),--columns $(COLUMNS),)
_STATUS       = $(if $(STATUS),--status $(STATUS),)
_EXCL         = $(if $(EXCLUDE),--exclude $(EXCLUDE),)
_LIMIT        = $(if $(LIMIT),--limit $(LIMIT),)
_OUTPUT       = $(if $(OUTPUT),--output "$(OUTPUT)",)
_INPUT        = $(if $(INPUT),--input "$(INPUT)",)
_DRY          = $(if $(DRY_RUN),--dry-run,)
_VERB         = $(if $(VERBOSE),--verbose,)
_OVR          = $(if $(OVERWRITE),--overwrite,)
_SKIP_MEDIA   = $(if $(SKIP_MEDIA),--skip-media,)
_SKIP_TAX     = $(if $(SKIP_TAX),--skip-tax,)
_SKIP_EXMEDIA = $(if $(SKIP_EXISTING_MEDIA),--skip-existing-media,)
_DEL_MEDIA    = $(if $(DELETE_MEDIA),--delete-media,)
_NO_UPD_XL    = $(if $(NO_UPDATE_EXCEL),--no-update-excel,)
_TEST_MODE    = $(if $(TEST_MODE),--test-mode,)
_MAX_RETRY    = $(if $(MAX_RETRIES),--max-retries $(MAX_RETRIES),)
_API_TO       = $(if $(API_TIMEOUT),--api-timeout $(API_TIMEOUT),)
# --circuit-breaker включён по умолчанию; NO_CB=1 отключает
_CB           = $(if $(NO_CB),--no-circuit-breaker,--circuit-breaker)
_FORCE        = $(if $(FORCE),--force,)
_MODEL        = $(if $(MODEL),--model "$(MODEL)",)
_PROMPT       = $(if $(PROMPT),--prompt "$(PROMPT)",)
_STRIP_TAGS   = $(if $(STRIP_TAGS),--strip-tags,)
_MD_TO_HTML   = $(if $(MD_TO_HTML),--md-to-html,)
_HTML_TO_MD   = $(if $(HTML_TO_MD),--html-to-md,)
_SRC          = $(if $(SRC),--src "$(SRC)",)
_TPL          = $(if $(TPL),--tpl "$(TPL)",)
_EXTRA        = $(if $(EXTRA),--extra $(EXTRA),)
_VLOOK_MODE   = $(if $(VLOOK_MODE),--mode $(VLOOK_MODE),)
_FOLDER       = $(if $(FOLDER),--folder "$(FOLDER)",)
_SHEET_ID     = $(if $(SHEET_ID),--sheet-id $(SHEET_ID),)
_CREDS        = $(if $(CREDENTIALS),--credentials "$(CREDENTIALS)",)
_NO_UP_BACK   = $(if $(NO_UPLOAD_BACK),--no-upload-back,)
_INCL_MEDIA   = $(if $(INCLUDE_MEDIA),--include-media,)
_NO_DL        = $(if $(NO_DOWNLOAD),--no-download-files,)
_WIDTH        = $(if $(WIDTH),--width $(WIDTH),)
_HEIGHT       = $(if $(HEIGHT),--height $(HEIGHT),)
_MAX_W        = $(if $(MAX_WIDTH),--max-width $(MAX_WIDTH),)
_MAX_H        = $(if $(MAX_HEIGHT),--max-height $(MAX_HEIGHT),)
_RATIO        = $(if $(RATIO),--aspect-ratio $(RATIO),)
_STRAT        = --strategy $(STRATEGY)
_QUAL         = --quality $(QUALITY)
_FMT          = --format $(FORMAT)
_GRADIENT     = $(if $(GRADIENT),--gradient $(GRADIENT),)
_GRAD_H       = $(if $(GRADIENT_HEIGHT),--gradient-height $(GRADIENT_HEIGHT),)
_WM_TEXT      = $(if $(WM_TEXT),--watermark-text "$(WM_TEXT)",)
_WM_IMAGE     = $(if $(WM_IMAGE),--watermark-image "$(WM_IMAGE)",)
_WM_POS       = $(if $(WM_TEXT)$(WM_IMAGE),--watermark-position $(WM_POS),)
_WM_OPACITY   = $(if $(WM_OPACITY),--watermark-opacity $(WM_OPACITY),)
_WM_SIZE      = $(if $(WM_SIZE),--watermark-size $(WM_SIZE),)
_WM_COLOR     = $(if $(WM_COLOR),--watermark-color "$(WM_COLOR)",)
_WM_SCALE     = $(if $(WM_SCALE),--watermark-scale $(WM_SCALE),)
_STRIP_META   = $(if $(STRIP_META),--strip-metadata,)
_RECURSIVE    = $(if $(RECURSIVE),--recursive,)
_GLOB         = $(if $(GLOB),--glob "$(GLOB)",)
_SRC_F        = $(if $(SRC),--source "$(SRC)",)
_TPL_F        = $(if $(TPL),--template "$(TPL)",)
_ENDPOINT     = $(if $(LLM_ENDPOINT),--endpoint "$(LLM_ENDPOINT)",)
_TOKEN        = $(if $(LLM_TOKEN),--token "$(LLM_TOKEN)",)

# ── Targets ───────────────────────────────────────────────────────────────────
.PHONY: help \
        fetch sync async validate options taxonomies update \
        fetch-media export export-media \
        download-google-sheet upload-to-google-sheets \
        sync-from-google sync-google-bidirectional \
        async-from-google async-google-bidirectional \
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
	$(info   make update             Подтянуть поля из WP в Excel по ID (WP не меняется))
	$(info )
	$(info МЕДИА)
	$(info   make fetch-media        Библиотека медиа -> лист media в Excel)
	$(info   make export             Контент -> HTML файлы + CSV индекс)
	$(info   make export-media       Скачать медиафайлы на диск + CSV)
	$(info )
	$(info GOOGLE SHEETS)
	$(info   make download-google-sheet      Google Sheets -> Excel)
	$(info   make upload-to-google-sheets    Excel -> Google Sheets)
	$(info   make sync-from-google           Google Sheets -> WordPress)
	$(info   make sync-google-bidirectional  Google Sheets <-> WordPress)
	$(info   make async-from-google          Google Sheets -> WordPress (async))
	$(info   make async-google-bidirectional Google Sheets <-> WordPress (async))
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
	$(info ПЕРЕМЕННЫЕ — общие)
	$(info   PROJECT=mysite          Папка проекта (projects/mysite/))
	$(info   FILE=data.xlsx          Excel файл)
	$(info   SHEETS=posts,pages      Список листов)
	$(info   STATUS=publish          Фильтр по статусу)
	$(info   CONCURRENCY=10          Параллельных запросов)
	$(info   DRY_RUN=1               Режим симуляции (без изменений))
	$(info   VERBOSE=1               Подробные логи)
	$(info )
	$(info ПЕРЕМЕННЫЕ — update)
	$(info   ID=123,456              ID записей через запятую (без ID — все строки листа))
	$(info )
	$(info ПЕРЕМЕННЫЕ — sync / async)
	$(info   COLUMNS=title,excerpt   Обновлять только эти колонки)
	$(info   SKIP_MEDIA=1            Пропустить загрузку медиа)
	$(info   SKIP_TAX=1              Пропустить обновление таксономий)
	$(info   SKIP_EXISTING_MEDIA=1   Не перезаписывать существующие featured_media)
	$(info   DELETE_MEDIA=1          Удалять featured media при action=delete)
	$(info   NO_UPDATE_EXCEL=1       Не записывать ID обратно в Excel)
	$(info   TEST_MODE=1             Обработать только первые 3 строки)
	$(info   MAX_RETRIES=5           Максимум повторов (async))
	$(info   API_TIMEOUT=60          Таймаут API в секундах (async))
	$(info   NO_CB=1                 Отключить circuit breaker (async))
	$(info )
	$(info ПЕРЕМЕННЫЕ — llm)
	$(info   COLUMN=excerpt          Колонка для записи результата (обязательно))
	$(info   MODEL=gpt-4             Модели через запятую (fallback))
	$(info   PROMPT="Напиши [title]" Промпт (поддерживает [column_name]))
	$(info   STRIP_TAGS=1            Удалить HTML теги из ответа)
	$(info   MD_TO_HTML=1            Markdown -> HTML)
	$(info   HTML_TO_MD=1            HTML -> Markdown)
	$(info )
	$(info ПЕРЕМЕННЫЕ — reshape / vlook)
	$(info   SRC=fetch.xlsx          Источник для reshape)
	$(info   TPL=default.xlsx        Шаблон для reshape)
	$(info   EXTRA=none              Доп. колонки reshape (none / список / пусто=все))
	$(info   VLOOK_MODE=content      Режим vlook: content/media/both/custom)
	$(info   SHEET=posts             Конкретный лист для vlook)
	$(info   FOLDER=./exports        Папка для vlook в режиме custom)
	$(info )
	$(info ПЕРЕМЕННЫЕ — Google Sheets)
	$(info   SHEET_ID=1B5gj...       ID Google таблицы)
	$(info   CREDENTIALS=creds.json  Путь к credentials.json)
	$(info   NO_UPLOAD_BACK=1        Не загружать обратно в Google Sheets)
	$(info )
	$(info ПЕРЕМЕННЫЕ — изображения)
	$(info   INPUT=./photos          Папка с изображениями)
	$(info   OUTPUT=./out            Папка для результата)
	$(info   RATIO=16:9              Соотношение сторон (image-crop))
	$(info   WIDTH=800 HEIGHT=600    Точный размер (image-crop))
	$(info   STRATEGY=center         Стратегия обрезки: center/top/bottom/left/right)
	$(info   GRADIENT=0.5            Затемняющий градиент снизу (0.0-1.0))
	$(info   WM_TEXT="© MySite"      Текст водяного знака)
	$(info   WM_IMAGE=logo.png       Картинка водяного знака)
	$(info   WM_POS=bottom-right     Позиция водяного знака)
	$(info   WM_OPACITY=0.3          Прозрачность водяного знака)
	$(info   WM_SCALE=0.2            Масштаб картинки-знака (доля ширины))
	$(info   QUALITY=80              Качество JPEG/WebP (image-compress/convert))
	$(info   MAX_WIDTH=1920          Максимальная ширина (image-compress))
	$(info   MAX_HEIGHT=1080         Максимальная высота (image-compress))
	$(info   FORMAT=webp             Формат: webp/jpg/png (image-convert))
	$(info   STRIP_META=1            Удалить EXIF метаданные)
	$(info   RECURSIVE=1             Искать в подпапках)
	@:

# ── Основные ──────────────────────────────────────────────────────────────────

fetch:
	$(WP) $(_P) fetch $(_FILE) $(_SHEETS) $(_EXCL) $(_STATUS) $(_LIMIT) $(_VERB)

sync:
	$(WP) $(_P) sync $(_FILE) $(_SHEETS) $(_COLS) \
		$(_SKIP_MEDIA) $(_SKIP_TAX) $(_SKIP_EXMEDIA) $(_DEL_MEDIA) \
		$(_NO_UPD_XL) $(_TEST_MODE) $(_DRY) $(_VERB)

async:
	$(WP) $(_P) async $(_FILE) $(_SHEETS) $(_COLS) --concurrency $(CONCURRENCY) \
		$(_SKIP_MEDIA) $(_SKIP_TAX) $(_DEL_MEDIA) $(_NO_UPD_XL) $(_TEST_MODE) \
		$(_MAX_RETRY) $(_API_TO) $(_CB) $(_DRY) $(_VERB)

validate:
	$(WP) $(_P) validate $(_FILE)

options:
	$(WP) $(_P) options $(_FILE) $(if $(FETCH),--fetch,) $(_DRY)

taxonomies:
	$(WP) $(_P) taxonomies

update:
ifndef COLUMNS
	$(error COLUMNS обязателен: make update COLUMNS=title,excerpt)
endif
	$(WP) $(_P) update $(if $(ID),--id $(ID),) --columns $(COLUMNS) $(_FILE) $(_SHEETS) $(_TEST_MODE) $(_DRY) $(_VERB)

# ── Медиа ─────────────────────────────────────────────────────────────────────

fetch-media:
	$(WP) $(_P) fetch-media $(_FILE) $(_VERB)

export:
	$(WP) $(_P) export $(_OUTPUT) --concurrency $(CONCURRENCY) $(_INCL_MEDIA) $(_VERB)

export-media:
	$(WP) $(_P) export-media $(_OUTPUT) --concurrency $(CONCURRENCY) $(_NO_DL) $(_VERB)

# ── Google Sheets ─────────────────────────────────────────────────────────────

download-google-sheet:
	$(WP) $(_P) download-google-sheet $(_SHEET_ID) $(_CREDS) $(_OUTPUT)

upload-to-google-sheets:
	$(WP) $(_P) upload-to-google-sheets $(_FILE) $(_SHEETS) $(_SHEET_ID) $(_CREDS) $(_VERB)

sync-from-google:
	$(WP) $(_P) sync-from-google $(_SHEET_ID) $(_CREDS) $(_SHEETS) \
		$(_SKIP_MEDIA) $(_SKIP_TAX) $(_SKIP_EXMEDIA) $(_DEL_MEDIA) $(_NO_UPD_XL) \
		--concurrency $(CONCURRENCY) $(_DRY) $(_VERB)

sync-google-bidirectional:
	$(WP) $(_P) sync-google-bidirectional $(_SHEET_ID) $(_CREDS) $(_SHEETS) $(_OUTPUT) \
		$(_SKIP_MEDIA) $(_SKIP_TAX) $(_SKIP_EXMEDIA) $(_DEL_MEDIA) $(_NO_UPD_XL) \
		$(_NO_UP_BACK) --concurrency $(CONCURRENCY) $(_DRY) $(_VERB)

async-from-google:
	$(WP) $(_P) async-from-google $(_SHEET_ID) $(_CREDS) $(_SHEETS) $(_OUTPUT) \
		$(_SKIP_MEDIA) $(_DEL_MEDIA) $(_NO_UPD_XL) \
		--concurrency $(CONCURRENCY) $(_MAX_RETRY) $(_CB) $(_DRY) $(_VERB)

async-google-bidirectional:
	$(WP) $(_P) async-google-bidirectional $(_SHEET_ID) $(_CREDS) $(_SHEETS) $(_OUTPUT) \
		$(_SKIP_MEDIA) $(_DEL_MEDIA) $(_NO_UPD_XL) $(_NO_UP_BACK) \
		--concurrency $(CONCURRENCY) $(_MAX_RETRY) $(_CB) $(_DRY) $(_VERB)

# ── Утилиты ───────────────────────────────────────────────────────────────────

bulk-delete:
ifndef POST_TYPE
	$(error POST_TYPE обязателен: make bulk-delete POST_TYPE=posts)
endif
	$(WP) $(_P) bulk-delete --post-type $(POST_TYPE) $(_STATUS) $(_FORCE) $(_DEL_MEDIA) $(_DRY) $(_VERB)

counter:
	$(WP) $(_P) counter --type $(TYPE) $(_STATUS) $(_LIMIT) $(_OUTPUT) --concurrency $(CONCURRENCY)

llm:
ifndef COLUMN
	$(error COLUMN обязателен: make llm COLUMN=excerpt)
endif
	$(WP) $(_P) llm --column $(COLUMN) $(_FILE) $(_SHEETS) \
		$(_MODEL) $(_PROMPT) $(_ENDPOINT) $(_TOKEN) \
		$(_OVR) $(_STRIP_TAGS) $(_MD_TO_HTML) $(_HTML_TO_MD) $(_DRY) $(_VERB)

reshape:
	$(WP) $(_P) reshape $(_SRC_F) $(_TPL_F) $(_OUTPUT) $(_EXTRA) $(_VERB)

vlook:
	$(WP) $(_P) vlook $(_VLOOK_MODE) $(_FILE) $(_SHEET) $(_FOLDER) \
		$(if $(COLUMN),--column $(COLUMN),) $(_VERB)

# ── Изображения ───────────────────────────────────────────────────────────────

image-crop:
	$(WP) $(_P) image-crop $(_INPUT) $(_OUTPUT) \
		$(_RATIO) $(_WIDTH) $(_HEIGHT) $(_STRAT) \
		$(_GRADIENT) $(_GRAD_H) \
		$(_WM_TEXT) $(_WM_IMAGE) $(_WM_POS) $(_WM_OPACITY) $(_WM_SIZE) $(_WM_COLOR) $(_WM_SCALE) \
		$(_GLOB) $(_RECURSIVE) $(_OVR) $(_DRY) $(_VERB)

image-compress:
	$(WP) $(_P) image-compress $(_INPUT) $(_OUTPUT) \
		$(_QUAL) $(_MAX_W) $(_MAX_H) $(_STRIP_META) \
		$(_GLOB) $(_RECURSIVE) $(_OVR) $(_DRY) $(_VERB)

image-convert:
	$(WP) $(_P) image-convert $(_INPUT) $(_OUTPUT) \
		$(_FMT) $(_QUAL) $(_STRIP_META) \
		$(_GLOB) $(_RECURSIVE) $(_OVR) $(_DRY) $(_VERB)
