# Новые возможности

## 1. Автоматическая очистка после синхронизации

После успешной операции sync (add/update/delete) Excel файл автоматически обновляется:
- **action** меняется на `skip` - чтобы строка не обрабатывалась повторно
- **content_path** очищается - файл контента уже загружен в WordPress
- **img_path** очищается - изображение уже загружено в WordPress

Это позволяет безопасно повторно запускать синхронизацию без дублирования операций.

**Файл:** `src/excel/writer.py` (метод `update_from_api_response`)

## 2. Работа с Google Sheets

### 2.1. Загрузка Excel в Google Sheets

**Новая команда:** `upload-to-google-sheets`

Позволяет загрузить локальный Excel файл в Google Sheets (отдельная операция без синхронизации с WordPress).

```bash
# Загрузить весь файл (используя .env)
python -m src.cli upload-to-google-sheets --file export.xlsx

# Загрузить только определенные листы
python -m src.cli upload-to-google-sheets --file export.xlsx --sheets posts,pages

# С явным указанием параметров
python -m src.cli upload-to-google-sheets --file data.xlsx --sheet-id "1B5gj1n..." --credentials "credentials/google.json"
```

**Параметры:**
- `--file` (обязательный) - путь к Excel файлу
- `--sheet-id` - ID Google Sheets (или из .env: `GOOGLE_SHEET_ID`)
- `--credentials` - путь к credentials.json (или из .env: `GOOGLE_SHEET_CREDENTIALS_PATH`)
- `--sheets` - список листов для обновления (по умолчанию все)
- `--verbose` - подробные логи

### 2.2. Двусторонняя синхронизация с Google Sheets

**Новая команда:** `sync-google-bidirectional`

Реализует полный цикл работы с Google Sheets:

### Пайплайн:
1. **Скачивание** Google Sheets → Excel
2. **Синхронизация** Excel ↔ WordPress
3. **Загрузка** обновленного Excel → Google Sheets

### Использование:

```bash
# Полный цикл (используя credentials из .env)
python -m src.cli sync-google-bidirectional

# С фильтром листов
python -m src.cli sync-google-bidirectional --sheets posts,pages

# Без загрузки обратно (только скачать и синхронизировать)
python -m src.cli sync-google-bidirectional --no-upload-back

# Dry-run режим
python -m src.cli sync-google-bidirectional --dry-run --verbose

# Указать другой путь к локальному файлу
python -m src.cli sync-google-bidirectional --output "data/my_project.xlsx"
```

### Параметры:

- `--sheet-id` - ID Google Sheets (или из .env: `GOOGLE_SHEET_ID`)
- `--credentials` - Путь к JSON credentials (или из .env: `GOOGLE_SHEET_CREDENTIALS_PATH`)
- `--output` - Путь к локальному Excel файлу (по умолчанию: `export.xlsx`)
- `--sheets` - Список листов через запятую для обновления (по умолчанию: все листы)
- `--no-upload-back` - Не загружать обратно в Google Sheets
- `--env-file` - Путь к .env файлу
- `--dry-run` - Режим симуляции
- `--verbose` - Подробные логи
- Все стандартные параметры sync: `--concurrency`, `--skip-media`, `--delete-media` и т.д.

### Переменные окружения (.env):

```bash
# Google Sheets
GOOGLE_SHEET_ID=1B5gj1nxzVhQ-9nk1o_KVgtwRyj_gl4yibIXmG4zZUq4
GOOGLE_SHEET_CREDENTIALS_PATH=credentials/google_sheets_credentials.json

# WordPress (обычные)
WORDPRESS_URL=https://example.com
WORDPRESS_USERNAME=admin
WORDPRESS_PASSWORD=your_app_password
```

### Технические детали:

**Файлы:**
- `src/utils/google_sheets.py` - Расширен класс `GoogleSheetsClient` (ранее `GoogleSheetsDownloader`)
- `src/cli.py` - Добавлена команда `sync_google_bidirectional`

**Новые методы:**
- `GoogleSheetsClient.upload_excel_to_sheets()` - Загрузка Excel файла в Google Sheets
  - Читает все листы из Excel
  - Обновляет существующие листы в Google Sheets
  - Создает новые листы если их нет
  - Поддерживает фильтрацию по списку листов

**Права доступа:**
Обновлены scopes для Service Account:
- `https://www.googleapis.com/auth/spreadsheets` (вместо readonly)
- `https://www.googleapis.com/auth/drive` (вместо readonly)

### Примеры использования:

#### Ежедневная синхронизация
```bash
# Утром: скачать изменения из Google Sheets и загрузить в WordPress
python -m src.cli sync-google-bidirectional

# Вечером: проверить что всё обновилось
# (можно запустить снова - благодаря action=skip повторно ничего не обработается)
```

#### Командная работа
```bash
# Редакторы работают в Google Sheets
# Автоматический скрипт (cron) каждый час запускает:
python -m src.cli sync-google-bidirectional --sheets posts,pages

# Результат синхронизации сразу виден в Google Sheets
# (обновленные ID, даты, ссылки, action=skip)
```

#### Разработка и тестирование
```bash
# Dry-run для проверки без изменений
python -m src.cli sync-google-bidirectional --dry-run --verbose

# Синхронизация без загрузки обратно (для тестирования)
python -m src.cli sync-google-bidirectional --no-upload-back
```

## Сводка команд Google Sheets

Теперь доступно 4 команды для работы с Google Sheets:

1. **`download-google-sheet`** - 📥 Скачать Google Sheets → Excel
   - Только скачивание, без синхронизации

2. **`upload-to-google-sheets`** - 📤 Загрузить Excel → Google Sheets *(новая)*
   - Только загрузка, без синхронизации

3. **`sync-from-google`** - 📥🔄 Google Sheets → Excel → WordPress
   - Односторонняя синхронизация (без загрузки обратно)

4. **`sync-google-bidirectional`** - 📥🔄📤 Google Sheets ↔ WordPress ↔ Google Sheets *(новая)*
   - Полный двусторонний цикл

## Обратная совместимость

- Существующие команды работают без изменений
- `GoogleSheetsDownloader` остался как алиас к `GoogleSheetsClient`
- Команды `download-google-sheet` и `sync-from-google` обновлены для использования `GoogleSheetsClient`

## Требования

Для работы с Google Sheets необходимы дополнительные зависимости (уже должны быть установлены):
- `google-auth`
- `google-api-python-client`
- `pandas`
- `openpyxl`
