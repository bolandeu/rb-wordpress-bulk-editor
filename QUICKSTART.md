# Quick Start Guide - WordPress Bulk Editor

Краткое руководство для быстрого старта.

## 1. Установка

```bash
# Установить зависимости
pip install -r requirements.txt
```

## 2. Подготовка Excel файла

### Создайте лист `profile` с credentials:

| DOMAIN | API_USER | API_TOKEN |
|--------|----------|-----------|
| https://yoursite.com | admin | xxxx xxxx xxxx xxxx |

**Где взять Application Password:**
1. Войдите в WordPress как администратор
2. Перейдите в "Пользователи" → "Профиль"
3. Прокрутите до секции "Application Passwords"
4. Введите название (например "Excel Sync") и нажмите "Add New"
5. Скопируйте сгенерированный пароль (формат: xxxx xxxx xxxx xxxx)

### Создайте лист контента (например `posts`):

| action | id | title | content | status | categories | img_path |
|--------|-----|-------|---------|--------|------------|----------|
| add | | Мой первый пост | Содержимое поста | publish | Новости | |
| add | | Второй пост | Еще текст | draft | Технологии | |

**Обязательные колонки:**
- `action` - что делать: `add`, `update`, `delete`, `skip`
- `id` - WordPress ID (пустое для новых постов)

## 3. Валидация

Проверьте файл перед синхронизацией:

```bash
python -m src.cli validate --file yourfile.xlsx
```

## 4. Синхронизация

### Тестовый запуск (без изменений):

```bash
python -m src.cli sync --file yourfile.xlsx --dry-run --verbose
```

### Реальная синхронизация:

```bash
python -m src.cli sync --file yourfile.xlsx
```

## 5. Проверка результатов

После синхронизации:
1. Колонка `id` будет заполнена WordPress ID
2. Посты появятся в WordPress админке
3. Лог файл будет создан в папке `logs/`

## Типичные сценарии

### Создать новые посты

```
action: add
id: (пустое)
title: Название поста
```

### Обновить существующий пост

```
action: update
id: 123
title: Новое название
```

### Удалить пост

```
action: delete
id: 123
```

### Загрузить изображение

```
action: add
title: Пост с фото
img_path: data/media/photo.jpg
```

Или с URL:
```
img_path: https://example.com/image.jpg
```

### Добавить категории

```
categories: Новости; Технологии; WordPress
```

Разделитель: `; ` (точка с запятой + пробел)

### ACF поля

```
acf_h1: Заголовок H1
acf_price: 1000
acf_event_date: 2024-12-31
```

## Troubleshooting

### "401 Unauthorized"
- Проверьте credentials в листе `profile`
- Убедитесь что Application Password правильный

### "Connection refused"
- Проверьте что DOMAIN включает `https://`
- Убедитесь что сайт доступен

### Медиафайл не найден
- Используйте путь относительно корня проекта
- Пример: `data/media/image.jpg`

## Дополнительные команды

### Получение данных из WordPress (fetch)

Загружает посты из WordPress и добавляет их в Excel:

```bash
# Получить все опубликованные посты
python -m src.cli fetch --file yourfile.xlsx --status publish

# Получить все данные
python -m src.cli fetch --file yourfile.xlsx

# Только определенные листы
python -m src.cli fetch --file yourfile.xlsx --sheets posts,pages

# Ограничить количество
python -m src.cli fetch --file yourfile.xlsx --limit 50
```

### Экспорт всего контента сайта (export)

Экспортирует весь контент WordPress в файлы (HTML + изображения) с CSV индексом.

```bash
# Экспорт с авторизацией
python -m src.cli export --domain https://example.com --username admin --password "xxxx xxxx xxxx"

# Экспорт БЕЗ авторизации (для публичных сайтов)
python -m src.cli export --domain https://example.com --output my_export

# Экспорт используя профиль из Excel
python -m src.cli export --file yourfile.xlsx --output my_export

# Ускоренный экспорт (больше параллельных загрузок)
python -m src.cli export --domain https://example.com --concurrency 20
```

**Что создается:**
- `index.csv` - полный индекс всех записей (с разделителем `;` и кодировкой UTF-8-BOM)
- `posts/123.html` - HTML контент записи
- `posts/123.jpg` - featured image записи
- Отдельные папки для каждого типа контента

**CSV индекс содержит:**
- post_type, id, title, slug, link
- html_path, image_path (пути к файлам или пустые)
- seo_title, status, date

**Особенности:**
- Параллельная обработка (по умолчанию 10 потоков, настраивается через `--concurrency`)
- Пропуск уже скачанных файлов (можно перезапустить экспорт без повтора)
- Автоматическое игнорирование служебных типов (wp_*, attachment, nav_menu, spectra)

### Обновление настроек сайта (options)

Создайте лист `options` в Excel:

| option | value |
|--------|-------|
| site_title | My Website |
| posts_per_page | 10 |

Затем запустите:

```bash
# Обновить настройки
python -m src.cli options --file yourfile.xlsx

# Показать что будет отправлено (без изменений)
python -m src.cli options --file yourfile.xlsx --dry-run
```

## Дополнительные опции команды sync

```bash
# Только определенные листы
python -m src.cli sync --file file.xlsx --sheets posts,pages

# Без загрузки медиа
python -m src.cli sync --file file.xlsx --skip-media

# Не записывать ID обратно в Excel
python -m src.cli sync --file file.xlsx --no-update-excel

# Изменить количество параллельных запросов
python -m src.cli sync --file file.xlsx --concurrency 10
```

## Логи

Логи сохраняются в папке `logs/`:
- `sync_20240110_143022.log` - текстовый лог
- `sync_20240110_143022.json` - JSON со статистикой

## Подробная документация

Смотрите [README.md](README.md) для полной документации.
