# Development Guide

Руководство для разработчиков WordPress Bulk Editor.

## Архитектура проекта

### Слои приложения

```
┌─────────────────────────────────────┐
│         CLI Layer (cli.py)          │  ← Точка входа, команды Typer
└─────────────────┬───────────────────┘
                  │
┌─────────────────▼───────────────────┐
│    Sync Layer (sync/)               │  ← Оркестрация синхронизации
│  - content_sync.py                  │
└─────────────────┬───────────────────┘
                  │
        ┌─────────┴─────────┐
        │                   │
┌───────▼─────────┐  ┌──────▼──────────┐
│  Excel Layer    │  │ WordPress Layer │
│  (excel/)       │  │ (wordpress/)    │
│  - reader.py    │  │ - client.py     │
│  - writer.py    │  │ - posts.py      │
│  - profile.py   │  │ - taxonomies.py │
└─────────────────┘  │ - media.py      │
                     └─────────────────┘
        │                   │
        └─────────┬─────────┘
                  │
┌─────────────────▼───────────────────┐
│    Mappers (mappers/)               │  ← Преобразование данных
│  - field_detector.py                │
│  - content_mapper.py                │
└─────────────────────────────────────┘
```

### Основные компоненты

#### 1. Excel Layer

**ExcelReader** ([src/excel/reader.py](src/excel/reader.py))
- Читает множественные листы Excel
- Классифицирует листы по типам (profile, content, taxonomy)
- Использует pandas для работы с данными

**ExcelWriter** ([src/excel/writer.py](src/excel/writer.py))
- Записывает данные обратно в Excel
- Обновляет ID, даты, ссылки после синхронизации
- Использует openpyxl для сохранения форматирования

**ProfileReader** ([src/excel/profile.py](src/excel/profile.py))
- Читает credentials из листа `profile`
- Валидирует формат данных

#### 2. WordPress Layer

**WordPressClient** ([src/wordpress/client.py](src/wordpress/client.py))
- Асинхронный HTTP клиент на базе httpx
- Basic Auth с Application Password
- Retry логика (tenacity)
- Connection pooling

**PostsEndpoint** ([src/wordpress/posts.py](src/wordpress/posts.py))
- CRUD операции для постов/страниц/custom types
- Динамическое определение endpoint по типу контента

**TaxonomiesEndpoint** ([src/wordpress/taxonomies.py](src/wordpress/taxonomies.py))
- Работа с таксономиями
- Кэширование существующих терминов
- Автоматическое создание новых терминов

**MediaEndpoint** ([src/wordpress/media.py](src/wordpress/media.py))
- Загрузка локальных файлов
- Загрузка по URL (sideload)
- Определение MIME типов

#### 3. Mappers

**FieldDetector** ([src/mappers/field_detector.py](src/mappers/field_detector.py))
- Анализирует колонки Excel
- Определяет типы полей: system, content, acf, taxonomies
- Константы: `SYSTEM_COLUMNS`, `ACF_PREFIX`, `TAXONOMY_DELIMITER`

**ContentMapper** ([src/mappers/content_mapper.py](src/mappers/content_mapper.py))
- Преобразует Excel row → WordPress API payload
- Обрабатывает ACF поля
- Подставляет taxonomy term IDs

#### 4. Sync Layer

**ContentSync** ([src/sync/content_sync.py](src/sync/content_sync.py))
- Главный оркестратор синхронизации
- Обрабатывает порядок операций: DELETE → UPDATE → ADD
- Управляет загрузкой медиа
- Обновляет Excel после каждой операции

## Поток данных

### Синхронизация (Excel → WordPress)

```
1. CLI: Чтение аргументов команды
   ↓
2. ProfileReader: Чтение credentials
   ↓
3. ExcelReader: Чтение всех листов
   ↓
4. WordPressClient: Подключение к API
   ↓
5. TaxonomyCache: Загрузка существующих терминов
   ↓
6. Для каждого листа:
   ├─ ExcelReader: Чтение строк
   ├─ FieldDetector: Анализ колонок
   ├─ Для каждой строки:
   │  ├─ ContentMapper: Excel row → API payload
   │  ├─ TaxonomiesEndpoint: Преобразование названий → term IDs
   │  ├─ MediaEndpoint: Загрузка медиа (если есть)
   │  ├─ PostsEndpoint: CREATE/UPDATE/DELETE
   │  └─ ExcelWriter: Запись результата
   └─ ExcelWriter: Сохранение файла
   ↓
7. Logger: Вывод summary статистики
```

### Пример обработки одной строки

```python
# Excel row
{
  "action": "add",
  "title": "New Post",
  "content": "Content here",
  "categories": "News; Tech",
  "acf_h1": "Custom H1",
  "img": "media/photo.jpg"
}

# ↓ FieldDetector.analyze()
{
  "system": ["action"],
  "content": ["title", "content"],
  "taxonomies": ["categories"],
  "acf": ["h1"]
}

# ↓ TaxonomiesEndpoint.process_taxonomy_string("News; Tech")
{
  "categories": [12, 45]  # term IDs
}

# ↓ MediaEndpoint.upload_file("media/photo.jpg")
{
  "featured_media": 567
}

# ↓ ContentMapper.map_row()
{
  "title": "New Post",
  "content": "Content here",
  "categories": [12, 45],
  "featured_media": 567,
  "acf": {
    "h1": "Custom H1"
  }
}

# ↓ PostsEndpoint.create()
POST /wp-json/wp/v2/posts
{...payload...}

# ↓ API Response
{
  "id": 123,
  "title": {"rendered": "New Post"},
  "link": "https://site.com/new-post",
  "date": "2024-01-10T14:30:22",
  "modified": "2024-01-10T14:30:22",
  "featured_media": 567,
  "image_url": "https://site.com/wp-content/uploads/2024/01/photo.jpg"
}

# ↓ ExcelWriter.update_from_api_response()
Excel row updated:
{
  "id": 123,
  "featured_media": 567,
  "image_url": "https://site.com/...",
  "link": "https://site.com/new-post",
  "date": "2024-01-10T14:30:22",
  "modified": "2024-01-10T14:30:22"
}
```

## Конфигурация

### Pydantic Models ([src/config.py](src/config.py))

```python
class WordPressConfig(BaseModel):
    url: str
    username: str
    password: str
    verify_ssl: bool = True

class SyncConfig(BaseModel):
    batch_size: int = 10
    max_retries: int = 3
    timeout: int = 30
    concurrency: int = 5
    dry_run: bool = False
    skip_media: bool = False
    update_excel: bool = True

class PathsConfig(BaseModel):
    excel_file: Path
    logs_folder: Path
    media_folder: Path

class LoggingConfig(BaseModel):
    level: str = "INFO"
    rotation: str = "10 MB"
    retention: str = "30 days"

class AppConfig(BaseModel):
    wordpress: WordPressConfig
    sync: SyncConfig
    paths: PathsConfig
    logging: LoggingConfig
```

### Константы ([src/constants.py](src/constants.py))

```python
# Служебные колонки Excel
SYSTEM_COLUMNS = {
    "action", "id", "type", "img", "link",
    "date", "modified", "image_url", "featured_media"
}

# Префикс ACF полей
ACF_PREFIX = "acf_"

# Разделитель таксономий
TAXONOMY_DELIMITER = "; "

# Суффикс кастомных таксономий
TAXONOMY_SUFFIX = "-cat"

# Формат даты в логах
LOG_DATE_FORMAT = "%Y%m%d_%H%M%S"
```

## Обработка ошибок

### Иерархия исключений ([src/exceptions.py](src/exceptions.py))

```python
class WPBulkEditorError(Exception):
    """Базовое исключение"""

class ExcelError(WPBulkEditorError):
    """Ошибки работы с Excel"""

class WordPressAPIError(WPBulkEditorError):
    """Ошибки WordPress API"""

class ValidationError(WPBulkEditorError):
    """Ошибки валидации данных"""

class MediaUploadError(WPBulkEditorError):
    """Ошибки загрузки медиа"""
```

### Retry логика

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10)
)
async def request_with_retry():
    # HTTP request
    pass
```

## Тестирование

### Структура тестов

```
tests/
├── unit/
│   ├── test_field_detector.py
│   ├── test_content_mapper.py
│   ├── test_excel_reader.py
│   └── test_taxonomies.py
├── integration/
│   ├── test_sync_flow.py
│   └── test_wordpress_client.py
└── fixtures/
    └── test_data.xlsx
```

### Запуск тестов

```bash
# Установить зависимости для тестов
pip install pytest pytest-asyncio pytest-cov

# Запустить все тесты
pytest

# С покрытием
pytest --cov=src --cov-report=html

# Только unit тесты
pytest tests/unit/

# С подробным выводом
pytest -v -s
```

### Пример unit теста

```python
import pytest
from src.mappers.field_detector import FieldDetector

def test_field_detector_acf():
    columns = ["action", "title", "acf_h1", "acf_price"]
    detector = FieldDetector(columns)
    analysis = detector.analyze()

    assert "h1" in analysis["acf"]
    assert "price" in analysis["acf"]
    assert "title" in analysis["content"]
    assert "action" in analysis["system"]
```

### Пример integration теста

```python
import pytest
from src.wordpress.client import WordPressClient
from src.config import WordPressConfig

@pytest.mark.asyncio
async def test_wordpress_connection():
    config = WordPressConfig(
        url="https://test-site.com",
        username="admin",
        password="test pass word here"
    )

    async with WordPressClient(config) as client:
        response = await client.get("")
        assert response["name"] == "Test Site"
```

## Расширение функционала

### Добавление нового endpoint

1. Создайте класс в `src/wordpress/`:

```python
# src/wordpress/custom_endpoint.py
from typing import Dict, Any, List
from .client import WordPressClient

class CustomEndpoint:
    def __init__(self, client: WordPressClient):
        self.client = client
        self.endpoint = "/wp-json/custom/v1/items"

    async def get_all(self) -> List[Dict[str, Any]]:
        return await self.client.get(self.endpoint)

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        return await self.client.post(self.endpoint, data)
```

2. Интегрируйте в sync flow.

### Добавление новой команды CLI

```python
# src/cli.py
@app.command()
def my_command(
    file: Path = typer.Option(..., "--file", "-f"),
    option: str = typer.Option("default", "--option")
):
    """
    Описание новой команды
    """
    console.print(f"[bold blue]My Command[/bold blue]")
    # Ваша логика здесь
```

## Производительность

### Оптимизации

1. **Асинхронные операции** - используется `asyncio` для параллельных HTTP запросов
2. **Connection pooling** - httpx переиспользует соединения
3. **Кэширование таксономий** - загружаются один раз перед синхронизацией
4. **Батчевая обработка** - строки обрабатываются пакетами

### Профилирование

```python
import cProfile
import pstats

cProfile.run('asyncio.run(run_sync(config))', 'sync_stats')
p = pstats.Stats('sync_stats')
p.sort_stats('cumulative').print_stats(20)
```

## Логирование

### Конфигурация loguru ([src/utils/logger.py](src/utils/logger.py))

```python
logger.remove()  # Удаляем стандартные обработчики

# Консольный вывод
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
    colorize=True
)

# Файловый вывод
logger.add(
    log_file,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="DEBUG",
    rotation="10 MB",
    retention="30 days"
)
```

### Использование

```python
from loguru import logger

logger.debug("Детальная отладочная информация")
logger.info("Основная информация о процессе")
logger.warning("Предупреждение")
logger.error("Ошибка")
logger.exception("Ошибка с трейсбеком")
```

## Лучшие практики

1. **Используйте type hints** везде
2. **Документируйте публичные функции** docstrings
3. **Обрабатывайте ошибки** явно
4. **Логируйте важные события** на уровне INFO
5. **Используйте async/await** для I/O операций
6. **Валидируйте данные** через Pydantic
7. **Пишите тесты** для критического функционала

## Полезные команды

```bash
# Проверка типов
mypy src/

# Форматирование кода
black src/

# Линтинг
flake8 src/

# Сортировка импортов
isort src/
```
