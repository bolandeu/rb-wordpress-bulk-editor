# Команды для меня, чтобы не искать в документации

--dry-run --verbose для тестирования

## Важно: Авторизация

Все команды берут credentials из .env файла (WP_DOMAIN, WP_API_USER, WP_API_TOKEN).
Основные команды (sync, fetch, export, export-media, bulk-delete) также принимают явные CLI параметры --domain, --username, --password.

## Работа с несколькими проектами

Используйте параметр --env-file для указания кастомного .env файла:
python -m src.cli sync --file w.xlsx --env-file project2/.env

## Быстрый экспорт контента сайта

# Используя .env
python -m src.cli export --output "export/tzm" --concurrency 10

# С явным указанием credentials
python -m src.cli export --domain https://terminalzm.ru --username master --password "XimY D2BP NPJr khQj PSbn A8ax" --output "export/tzm" --concurrency 10

## Синхронизация

# Только медиа
python -m src.cli sync --file example.xlsx --sheets media

# Только одного листа
python -m src.cli sync --file w.xlsx --sheets posts
python -m src.cli sync --file w.xlsx --sheets posts --dry-run --verbose

# Всего проекта (включая media)
python -m src.cli sync --file example.xlsx

# С явным указанием credentials
python -m src.cli sync --file w.xlsx --domain https://terminalzm.ru --username master --password "XimY D2BP NPJr khQj PSbn A8ax"

# С кастомным .env файлом
python -m src.cli sync --file w.xlsx --env-file project2/.env

## Экспорт медиафайлов

# С файлами (используя .env)
python -m src.cli export-media --output "export/tzm/media" --concurrency 20

# С явным указанием credentials
python -m src.cli export-media --domain https://terminalzm.ru --username master --password "XimY D2BP NPJr khQj PSbn A8ax" --output "export/tzm/media" --concurrency 20

# Только метаданные
python -m src.cli export-media --no-download-files --output media_list

## Получить список медиафайлов в Excel
python -m src.cli fetch-media --file example.xlsx

## Экспорт контента с метаданными медиа (без скачивания файлов)
python -m src.cli export --output "export/tzm" --include-media

## Скачивание Google Sheets в Excel
# Используя .env (GOOGLE_SHEET_ID и GOOGLE_SHEET_CREDENTIALS_PATH)
python -m src.cli download-google-sheet
! протестирована, работает

# С явным указанием параметров
python -m src.cli download-google-sheet --sheet-id "1B5gj1nxzVhQ-9nk1o_KVgtwRyj_gl4yibIXmG4zZUq4" --credentials "credentials/google_sheets_credentials.json" --output "my_file.xlsx"

## Пайплайн: Скачивание Google Sheets и синхронизация
# Используя .env
python -m src.cli sync-from-google

# С явным указанием параметров и фильтром листов
python -m src.cli sync-from-google --sheet-id "1B5gj1n..." --credentials "credentials/google.json" --sheets posts,pages

# Dry-run режим
python -m src.cli sync-from-google --dry-run --verbose

## Массовое удаление записей одного типа

# Удалить все черновики постов в корзину (используя .env)
python -m src.cli bulk-delete --post-type posts --status draft

# С явным указанием credentials
python -m src.cli bulk-delete --domain https://terminalzm.ru --username master --password "XimY D2BP NPJr khQj PSbn A8ax" --post-type posts --status draft

# Удалить все записи типа service полностью с медиа
python -m src.cli bulk-delete --post-type service --force --delete-media

# Показать что будет удалено (dry-run)
python -m src.cli bulk-delete --post-type posts --dry-run

# С кастомным .env файлом
python -m src.cli bulk-delete --env-file project2/.env --post-type posts

## Синхронизация с удалением featured media
# Если в Excel есть action=delete, featured media тоже удалятся
python -m src.cli sync --file example.xlsx --sheets posts --delete-media

## Получение данных WordPress → Excel - fetch

### Получить все опубликованные посты (используя .env)
python -m src.cli fetch --file example.xlsx --status publish

### С явным указанием credentials
python -m src.cli fetch --file w.xlsx --domain https://terminalzm.ru --username master --password "XimY D2BP NPJr khQj PSbn A8ax"

### Получить все данные (любой статус)
python -m src.cli fetch --file w.xlsx

### Только определенные листы
python -m src.cli fetch --file example.xlsx --sheets posts,pages

### Ограничить количество записей
python -m src.cli fetch --file example.xlsx --limit 50

### С кастомным .env файлом
python -m src.cli fetch --file example.xlsx --env-file project2/.env


