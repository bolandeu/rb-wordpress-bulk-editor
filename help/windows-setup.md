# Установка WordPress Bulk Editor на Windows

## Что копировать

Скопируй следующие файлы и папки на Windows машину:

```text
wordpress-bulk-editor/
├── src/                ← исходный код (обязательно)
├── templates/          ← шаблоны Excel (обязательно)
├── help/               ← справочник команд (опционально)
├── pyproject.toml      ← зависимости и конфигурация
├── uv.lock             ← точные версии пакетов
├── Makefile            ← make-команды
└── .env.example        ← шаблон настроек
```

**Не копировать:** `.venv/`, `projects/`, `data/`, `credentials/`, `__pycache__/`, `*.egg-info/`, любые `.xlsx` файлы с данными.

Проще всего — клонировать через git:

```bat
git clone <repo-url> wordpress-bulk-editor
cd wordpress-bulk-editor
```

---

## Установка (один раз)

### 1. Python 3.10+

Скачать с python.org или через Microsoft Store.
При установке отметить галку **"Add Python to PATH"**.

Проверка:

```bat
python --version
```

### 2. uv (менеджер пакетов и запуска)

В PowerShell:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Или через pip:

```bat
pip install uv
```

Проверка:

```bat
uv --version
```

### 3. GNU Make

Через Scoop (рекомендуется):

```bat
scoop install make
```

Или через Chocolatey:

```bat
choco install make
```

Проверка:

```bat
make --version
```

---

## Первый запуск

В папке проекта — создать виртуальное окружение и установить зависимости:

```bat
uv venv
uv pip install -e .
```

Проверить что make работает:

```bat
make help
```

> **Почему не `wp-bulk --help` напрямую?**
> На Windows после `uv pip install -e .` скрипт `wp-bulk.exe` попадает в `.venv\Scripts\`,
> которая не добавляется в PATH автоматически. Makefile решает это через `uv run wp-bulk` —
> uv сам находит `.venv` в папке проекта и запускает команду без активации окружения.
> Все `make`-команды работают сразу — активировать venv не нужно.

---

## Настройка проекта

### Создать папку проекта

```bat
mkdir projects\mysite
copy .env.example projects\mysite\.env
```

### Заполнить .env

Открыть `projects\mysite\.env` в Блокноте или VS Code и заполнить:

```env
WP_URL=https://mysite.com
WP_USERNAME=admin
WP_PASSWORD=xxxx xxxx xxxx xxxx
```

Для локального Docker с self-signed SSL добавить:

```env
WP_VERIFY_SSL=false
```

---

## Типовой рабочий процесс

```bat
# 1. Выгрузить данные сайта в Excel
make fetch PROJECT=mysite

# 2. Отредактировать Excel: action=update/add/delete

# 3. Загрузить обратно в WordPress
make async PROJECT=mysite FILE=projects\mysite\fetch.xlsx

# Тестовый режим перед синхронизацией
make async PROJECT=mysite FILE=projects\mysite\fetch.xlsx DRY_RUN=1
```

---

## Возможные проблемы

### `uv` не найден после установки

Закрыть и снова открыть терминал — PATH обновляется только в новой сессии.
Если не помогло, добавить вручную в PowerShell:

```powershell
$env:PATH += ";$env:USERPROFILE\.local\bin"
```

### `make` не найден

Проверить установку: `scoop install make` или `choco install make`.
Или использовать Git Bash — там make доступен без установки.

### `SSL: CERTIFICATE_VERIFY_FAILED`

Локальный Docker с самоподписанным сертификатом. Добавить в `.env` проекта:

```env
WP_VERIFY_SSL=false
```

### Кириллица в терминале выглядит как кракозябры

Переключить кодировку сессии (только для текущего окна):

```bat
chcp 65001
```

Или задать постоянно через переменную окружения системы: `PYTHONUTF8=1`.

### `uv sync` vs `uv pip install -e .`

`uv sync` ставит точно по `uv.lock` — воспроизводимо на любой машине.
`uv pip install -e .` ставит по `pyproject.toml` — свежие совместимые версии.
Для продакшена и командной работы использовать `uv sync`.
