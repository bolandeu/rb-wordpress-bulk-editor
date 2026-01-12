- [x] python -m src.cli sync --file export.xlsx - ИСПРАВЛЕНО
  - Ошибка: `NotFoundError: /wp-json/wp/v2/category` (неправильный endpoint)
  - Решение: добавлена нормализация имен таксономий (category → categories, tag → tags)
  - Исправлено в 4 местах: TaxonomyCache.load, ContentSync, и двух местах в cli.py
  - Теперь можно использовать как "category", так и "categories" в названиях листов Excel


- [x] python -m src.cli sync-from-google вызывает ошибку - ИСПРАВЛЕНО
  - Добавлен отсутствующий параметр `--delete-media` в функцию sync_from_google
  - Ошибка: `NameError: name 'delete_media' is not defined` - устранена

- [x] Добавить экспорт листа media в команду `export` без скачивания ✅
  - Добавлен параметр `--include-media` в команду `export`
  - Экспортируются только метаданные в CSV файл `media.csv`
  - Команда: `python -m src.cli export --domain URL --username USER --password PASS --include-media`

- [x] Отдельная команда скачивания файла проекта c Google Sheets в формате Excel ✅
  - Команда: `python -m src.cli download-google-sheet`
  - Поддержка параметров из .env: `GOOGLE_SHEET_ID` и `GOOGLE_SHEET_CREDENTIALS_PATH`
  - Можно указать явно: `--sheet-id`, `--credentials`, `--output`
  - По умолчанию сохраняет в `export.xlsx`
  - Пример: `python -m src.cli download-google-sheet --output my_file.xlsx`

- [x] Возможность запуска скачивания с Google Sheets и синхронизации одним пайплайном ✅
  - Команда: `python -m src.cli sync-from-google`
  - Скачивает Google Sheets и затем выполняет sync
  - Логика sync и download-google-sheet остались отдельно
  - Пример: `python -m src.cli sync-from-google --sheets posts,pages`

- [x] Проверка дубликатов по slug при action=add ✅
  - При action=add проверяется наличие записи с таким же slug
  - Если запись существует - автоматически выполняется update вместо add
  - Работает только если поле slug заполнено
  - Предотвращает создание дубликатов

- [x] Команда быстрого удаления всех записей одного типа ✅
  - Команда: `python -m src.cli bulk-delete --post-type TYPE`
  - Опции: `--status`, `--force`, `--delete-media`, `--dry-run`
  - Удаляет связанные featured media (с флагом `--delete-media`)
  - Примеры:
    - `python -m src.cli bulk-delete --post-type posts --status draft`
    - `python -m src.cli bulk-delete --domain URL --username USER --password PASS --post-type service --force --delete-media`
  - Та же логика добавлена в sync с параметром `--delete-media` для action=delete

- [x] Новая система авторизации на основе .env файлов ✅
  - Все команды используют credentials из .env файла (WP_DOMAIN, WP_API_USER, WP_API_TOKEN)
  - Удален класс ProfileReader и лист 'profile' из Excel
  - Добавлен параметр `--env-file` для всех команд (поддержка нескольких проектов)
  - Основные команды поддерживают явные CLI параметры `--domain`, `--username`, `--password`:
    - `sync` - синхронизация
    - `fetch` - загрузка из WordPress
    - `export` - экспорт контента
    - `export-media` - экспорт медиафайлов
    - `bulk-delete` - массовое удаление
  - Приоритет: CLI параметры → кастомный .env (--env-file) → default .env
  - Создан файл MIGRATION.md с инструкциями по миграции
  - Примеры:
    - `python -m src.cli sync --file w.xlsx --domain URL --username USER --password PASS`
    - `python -m src.cli fetch --file w.xlsx --domain URL --username USER --password PASS`
    - `python -m src.cli bulk-delete --domain URL --username USER --password PASS --post-type posts`
    - `python -m src.cli export --env-file project2/.env --output export`





