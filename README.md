# WordPress Bulk Editor

Python CLI-инструмент для синхронизации данных между Excel/JSON и WordPress через REST API.

## Установка

```bash
uv tool install "git+https://github.com/bolandeu/worppress-bulk-editor.git"
uv tool upgrade wp-bulk-editor
```

Репозиторий приватный — авторизация как у `git clone` (`gh auth login`, либо
SSH-форма `git+ssh://git@github.com/bolandeu/worppress-bulk-editor.git`).

`wp-bulk --help` — список команд, `wp-bulk --ai` — соглашения для агентов.

## Возможности

- ✅ Синхронизация постов, страниц, кастомных типов контента
- ✅ Работа с таксономиями (категории, теги, кастомные)
- ✅ Поддержка ACF полей
- ✅ Загрузка медиафайлов (локальные и по URL)
- ✅ SEO поля (Yoast/Rank Math)
- ✅ Множественные листы Excel
- ✅ Умная работа с таксономиями (автосоздание)
- ✅ Обратная запись ID в Excel
- ✅ VLOOKUP файлов по ID (привязка content/media к строкам Excel)
