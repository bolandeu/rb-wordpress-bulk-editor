# Миграция на новую систему авторизации

## Что изменилось

Начиная с этой версии, **все команды используют только .env файлы для авторизации**.
Лист `profile` в Excel файлах больше не используется и может быть удален.

## Что нужно сделать

### 1. Создайте .env файл (если еще не создан)

Скопируйте `.env.example` в `.env`:
```bash
cp .env.example .env
```

### 2. Заполните credentials в .env

Откройте `.env` и заполните:
```env
WP_DOMAIN=https://your-site.com
WP_API_USER=your_username
WP_API_TOKEN=xxxx xxxx xxxx xxxx
```

### 3. Удалите лист `profile` из Excel файлов

Откройте ваши Excel файлы и удалите лист с названием `profile`.
Credentials теперь берутся только из .env файла.

### 4. Обновите команды (если использовали --file для credentials)

**Старая версия:**
```bash
python -m src.cli bulk-delete --file site.xlsx --post-type posts
python -m src.cli export --file site.xlsx --output export
```

**Новая версия:**
```bash
# Используя .env (по умолчанию)
python -m src.cli bulk-delete --post-type posts
python -m src.cli export --output export

# Или с явным указанием credentials
python -m src.cli bulk-delete --domain https://site.com --username user --password "pass word" --post-type posts
```

## Работа с несколькими проектами

Теперь можно использовать разные .env файлы для разных проектов:

```bash
# Проект 1 (использует default .env)
python -m src.cli sync --file project1.xlsx

# Проект 2 (использует кастомный .env)
python -m src.cli sync --file project2.xlsx --env-file project2/.env

# Проект 3 (использует явные credentials)
python -m src.cli sync --file project3.xlsx --domain https://site3.com --username user --password "pass"
```

## Команды поддерживающие явные CLI credentials

Все основные команды теперь поддерживают явное указание credentials через CLI параметры:
- `sync` - синхронизация Excel → WordPress
- `fetch` - загрузка данных из WordPress
- `export` - экспорт контента
- `export-media` - экспорт медиафайлов
- `bulk-delete` - массовое удаление

**Приоритет:**
1. CLI параметры (`--domain`, `--username`, `--password`)
2. Кастомный .env файл (`--env-file`)
3. Default .env файл

## Все команды поддерживающие --env-file

Все команды теперь поддерживают параметр `--env-file`:
- `sync` - синхронизация
- `fetch` - загрузка данных из WordPress
- `validate` - валидация Excel файла
- `options` - обновление настроек сайта
- `fetch-media` - загрузка медиафайлов в Excel
- `export` - экспорт контента
- `export-media` - экспорт медиафайлов
- `sync-from-google` - пайплайн Google Sheets → WordPress
- `bulk-delete` - массовое удаление

## Примеры

### Синхронизация с default .env
```bash
python -m src.cli sync --file site.xlsx
```

### Синхронизация с явными credentials
```bash
python -m src.cli sync --file site.xlsx --domain https://site.com --username user --password "pass word"
```

### Синхронизация с кастомным .env
```bash
python -m src.cli sync --file site.xlsx --env-file project2/.env
```

### Fetch с явными credentials
```bash
python -m src.cli fetch --file site.xlsx --domain https://site.com --username user --password "pass word" --status publish
```

### Экспорт с явными credentials
```bash
python -m src.cli export --domain https://site.com --username user --password "pass word" --output export
```

### Bulk delete с явными credentials
```bash
python -m src.cli bulk-delete --domain https://site.com --username master --password "XimY D2BP NPJr" --post-type posts
```

## Преимущества новой системы

1. **Безопасность**: credentials не хранятся в Excel файлах
2. **Гибкость**: легко работать с несколькими проектами через --env-file
3. **Простота**: один .env файл вместо листа profile в каждом Excel файле
4. **Контроль версий**: .env файлы в .gitignore, credentials не попадут в git
5. **CLI overrides**: возможность переопределить credentials через параметры команды

## Устаревшие функции

Следующие функции **больше не поддерживаются**:
- ❌ Лист `profile` в Excel файлах (был удален)
- ❌ Параметр `--file` для чтения credentials в командах `export`, `export-media`, `bulk-delete`
- ❌ Класс `ProfileReader` (удален из кодовой базы)

## Нужна помощь?

Если возникли проблемы с миграцией, проверьте:
1. Файл .env существует и заполнен корректно
2. Значения WP_DOMAIN, WP_API_USER, WP_API_TOKEN установлены
3. Путь в --env-file (если используете) указан правильно
