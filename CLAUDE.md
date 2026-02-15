# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

WordPress Bulk Editor — CLI tool for bidirectional Excel ↔ WordPress synchronization. Supports custom post types, taxonomies, ACF fields, media, SEO, LLM content generation, Google Sheets integration, and image processing.

## Commands

```bash
# Install in dev mode
pip install -e .

# Run CLI
wp-bulk --help
wp-bulk <command> --help

# Run as module (without install)
python -m src.cli

# Typical usage with project context
wp-bulk sync --project mysite
wp-bulk async --project mysite          # parallel version
wp-bulk fetch --project mysite --type posts
```

No tests exist in this project.

## Architecture

### Entry Point & CLI

`src/cli/__init__.py` defines the Typer app with a `--project` callback that loads project context from `projects/<name>/` (auto-resolves .env, .xlsx, media/, logs/).

Commands are split across files in `src/cli/`:
- `sync_commands.py` — `sync`, `async` (Excel → WordPress)
- `fetch_commands.py` — `fetch`, `fetch-media` (WordPress → Excel)
- `export_commands.py` — `export`, `export-media` (content to HTML/CSV)
- `google_commands.py` — 6 Google Sheets ↔ WordPress commands
- `tool_commands.py` — `validate`, `options`, `bulk-delete`, `counter`, `llm`
- `image_commands.py` — `image-crop`, `image-compress`, `image-convert`
- `common.py` — shared helpers: `handle_command_errors()` context manager, `setup_command_logging()`, `load_and_print_credentials()`

All commands use `handle_command_errors()` for unified error handling and `load_and_print_credentials()` for credential loading. Follow this pattern when adding new commands.

### Data Flow: sync/async Commands

```
Excel → ExcelReader → FieldDetector → ContentMapper.map_row() → WordPress API
                                                                      ↓
Excel ← ExcelWriter.update_row_after_sync() ← ResponseMapper ← API Response
```

### WordPress Module (`src/wordpress/`)

`WordPressClient` — async httpx client with `@async_retry` decorator and `CircuitBreaker`. Used as context manager (`async with`). All endpoints receive a client instance:
- `PostsEndpoint` — CRUD for any post type (posts, pages, CPT)
- `TaxonomiesEndpoint` — term CRUD + `TaxonomyCache` (thread-safe with `asyncio.Lock`)
- `MediaEndpoint` — file upload, URL upload, multipart handling
- `WordPressDiscovery` — auto-detect post types, taxonomies, ACF fields from site

### Sync Module (`src/sync/`)

Base class pattern: `ContentSyncBase` has shared logic (`_add_item`, `_update_item`, `_delete_item`), inherited by `ContentSync` (sequential) and `ContentSyncAsync` (parallel with `asyncio.Semaphore`). Same pattern for `TaxonomySyncBase` → `TaxonomySync`/`TaxonomySyncAsync`.

### Mappers (`src/mappers/`)

- `FieldDetector` — categorizes Excel columns into content fields, taxonomy fields, ACF fields (`acf_` prefix), system columns
- `ContentMapper` — Excel row → WordPress API payload. Sends empty strings for SEO/text fields to allow clearing values
- `ResponseMapper` — WordPress API response → Excel row for writeback

### Configuration (`src/config.py`)

Pydantic models: `WordPressConfig`, `SyncConfig`, `PathsConfig`, `LoggingConfig` → aggregated in `AppConfig`. `Settings(BaseSettings)` loads from .env with `WP_` prefix.

### Exception Hierarchy (`src/exceptions.py`)

```
WPBulkEditorError
├── ConfigurationError
├── ExcelError (ExcelStructureError, ExcelValidationError, ProfileSheetMissingError)
├── WordPressError (AuthenticationError, NotFoundError, ServerError, NetworkError, MediaUploadError)
├── ValidationError
└── ImageProcessingError
```

`retry.py` uses this hierarchy: retries `NetworkError`, `ServerError`, 429/502/503/504; never retries `AuthenticationError`, `NotFoundError`.

### Retry & Circuit Breaker (`src/utils/retry.py`)

Custom implementation — do NOT replace with tenacity. `@async_retry` decorator with configurable exponential backoff. `CircuitBreaker` with CLOSED → OPEN → HALF_OPEN states and `asyncio.Lock`.

## Key Conventions

- **Language**: Code comments and logs are in Russian. CLI help text is in Russian.
- **Async-first**: WordPress API calls are async. Sync commands use `asyncio.run()` at the CLI level.
- **Pillow lazy loading**: `image_processor.py` imports PIL inside `__init__`, not at module level — Pillow is optional.
- **Path safety**: All user-provided file paths must be validated against path traversal (`..`, absolute paths, `resolve().relative_to()`).
- **Credentials masking**: Never log full credentials. Use `username[:3]***`, `path.name` only.
- **System columns** (`src/constants.py`): `action`, `id`, `img_path`, `content_path`, `image_url`, `alt_text`, `caption` — these are NOT sent to WordPress API.
- **Taxonomy delimiter**: `"; "` (semicolon + space) separates multiple terms in Excel cells.
- **ACF prefix**: Excel columns starting with `acf_` are mapped to ACF fields.
- **`projects/` folder**: Contains user data (credentials, Excel files), not part of the codebase. Do not modify.
