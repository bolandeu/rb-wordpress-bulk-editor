"""
Команды загрузки данных из WordPress: fetch, fetch-media, update
"""

from pathlib import Path
from typing import Optional, List
import asyncio

import pandas as pd
from loguru import logger

from . import app, console
from .. import output
from ..output import log_stream
from .common import handle_command_errors, load_and_print_credentials
from .helpers import get_project, resolve_env_file, resolve_logs_folder, resolve_excel_file, require_excel_file
from ..config import LoggingConfig, WordPressConfig, Settings
from ..constants import is_taxonomy_column, is_dot_notation_column, read_nested_value
from ..utils.wp_helpers import wp_rendered
from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter
from ..wordpress.client import WordPressClient
from ..wordpress.posts import PostsEndpoint
from ..wordpress.taxonomies import TaxonomiesEndpoint, normalize_taxonomy_name
from ..mappers.response_mapper import ResponseMapper
from ..utils.logger import setup_logger
from ..exceptions import NotFoundError

import typer


@app.command()
def fetch(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к Excel файлу (если не указан, создается fetch.xlsx)",
        exists=False,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    domain: Optional[str] = typer.Option(
        None,
        "--domain",
        help="WordPress domain (например, https://example.com)"
    ),
    username: Optional[str] = typer.Option(
        None,
        "--username",
        help="WordPress username"
    ),
    password: Optional[str] = typer.Option(
        None,
        "--password",
        help="WordPress Application Password"
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Список листов через запятую (posts,pages)"
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    status: str = typer.Option(
        "any",
        "--status",
        help="Фильтр по статусу (publish, draft, any)"
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        help="Максимум записей на лист"
    ),
    exclude: Optional[str] = typer.Option(
        None,
        "--exclude",
        help="Список типов для исключения через запятую (posts,category)"
    ),
    confirm: bool = output.ConfirmOption,
    non_interactive: bool = output.NonInteractiveOption,
):
    """
    Получение данных из WordPress -> Excel

    Получает записи из WordPress и добавляет их в Excel файл
    после последней непустой строки каждого листа.
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    if non_interactive:
        output.get_options(ctx).non_interactive = True

    console.print(f"\n[bold blue]WordPress Bulk Editor - Fetch[/bold blue]\n")

    # Определяем режим работы
    if file is None:
        file = proj.resolve_output("fetch.xlsx") if proj.active else Path.cwd() / "fetch.xlsx"
        auto_mode = True
        console.print(f"[yellow]Auto mode:[/yellow] будет создан файл {file}\n")

        if file.exists():
            with handle_command_errors(ctx):
                output.require_confirmation(
                    ctx, f"Файл {file} уже существует. Перезаписать?", confirm
                )
    else:
        auto_mode = False
        console.print(f"[yellow]Manual mode:[/yellow] используется файл {file}\n")

    console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    with handle_command_errors(ctx):
        # 1. Настройка логирования
        logs_folder = resolve_logs_folder(ctx)
        logs_folder.mkdir(parents=True, exist_ok=True)

        logging_config = LoggingConfig()
        log_file = setup_logger(
            logs_folder=logs_folder,
            config=logging_config,
            command_name="fetch",
            console_stream=log_stream(ctx),
        )
        logger.info(f"Лог-файл создан: {log_file}")

        # 2. Загружаем credentials
        domain, username, password = load_and_print_credentials(
            env_file=env_file,
            domain=domain,
            username=username,
            password=password,
        )

        # 3. Определяем листы для обработки
        if auto_mode:
            if sheets:
                filter_list = [s.strip() for s in sheets.split(",")]
                sheets_to_fetch = filter_list
                console.print(f"[yellow]Листы для обработки:[/yellow] {', '.join(sheets_to_fetch)}\n")
            else:
                sheets_to_fetch = []
                console.print("[yellow]Листы будут определены автоматически[/yellow]\n")
        else:
            console.print("[yellow]Чтение структуры Excel файла...[/yellow]")
            with ExcelReader(file, known_taxonomies=Settings().get_taxonomies()) as reader:
                sheets_by_type = reader.get_sheets_by_type()

                if sheets:
                    filter_list = [s.strip() for s in sheets.split(",")]
                    sheets_to_fetch = filter_list
                else:
                    sheets_to_fetch = sheets_by_type["content"] + sheets_by_type["taxonomy"]

                console.print(f"[green]OK[/green] Листы для обработки: {', '.join(sheets_to_fetch)}\n")

        # Применяем исключения
        if exclude:
            exclude_list = [s.strip() for s in exclude.split(",")]
            sheets_to_fetch = [s for s in sheets_to_fetch if s not in exclude_list]
            console.print(f"[yellow]Исключены:[/yellow] {', '.join(exclude_list)}\n")

        # 4. Подключаемся к WordPress
        console.print("[yellow]Подключение к WordPress...[/yellow]")

        wp_config = WordPressConfig.from_credentials(domain, username, password)

        asyncio.run(fetch_and_append(
            wp_config=wp_config,
            excel_file=file,
            sheets=sheets_to_fetch,
            status=status,
            limit=limit,
            auto_mode=auto_mode,
            exclude=exclude,
            content_output_dir=proj.content_folder if proj.active else Path("data/content"),
        ))

        console.print(f"\n[bold green]SUCCESS! Данные добавлены в Excel![/bold green]\n")


async def fetch_and_append(
    wp_config: WordPressConfig,
    excel_file: Path,
    sheets: List[str],
    status: str,
    limit: Optional[int],
    auto_mode: bool = False,
    exclude: Optional[str] = None,
    content_output_dir: Optional[Path] = None,
):
    """
    Получить данные из WordPress и добавить в Excel.
    """
    async with WordPressClient(wp_config) as client:
        await client.test_connection()

        if auto_mode:
            # === AUTO MODE: Создаём файл с нуля ===
            from ..wordpress.discovery import WordPressDiscovery
            from ..excel.creator import ExcelCreator

            settings = Settings()
            discovery = WordPressDiscovery(
                client,
                skip_post_type_patterns=settings.get_skip_post_type_patterns(),
                skip_taxonomy_patterns=settings.get_skip_taxonomy_patterns(),
            )

            # 1. Обнаружение типов записей
            logger.info("Обнаружение типов записей...")
            explicit_sheets = bool(sheets)
            all_post_types = await discovery.discover_post_types(include_skipped=explicit_sheets)

            # 2. Обнаружение таксономий
            logger.info("Обнаружение таксономий...")
            all_taxonomies = await discovery.discover_taxonomies(include_skipped=explicit_sheets)

            # 3. Если указан --sheets, фильтруем
            if sheets:
                all_post_types = [pt for pt in all_post_types if pt in sheets]
                all_taxonomies = [t for t in all_taxonomies if t in sheets]

            # 4. Если указан --exclude, исключаем
            if exclude:
                exclude_list = [s.strip() for s in exclude.split(",")]
                all_post_types = [pt for pt in all_post_types if pt not in exclude_list]
                all_taxonomies = [t for t in all_taxonomies if t not in exclude_list]
                logger.info(f"Исключены: {', '.join(exclude_list)}")

            # 5. Выгружаем данные параллельно
            logger.info("Выгрузка данных (параллельно)...")
            sheets_data = {}

            async def _fetch_post_type(post_type: str):
                try:
                    posts_endpoint = PostsEndpoint(client, post_type)
                    posts = await posts_endpoint.get_all(status=status)
                    if posts:
                        fields = await discovery.analyze_fields_from_data(posts, "content")
                        logger.info(f"'{post_type}': выгружено {len(posts)} записей, {len(fields)} полей")
                        return post_type, {"data": posts, "fields": fields, "type": "content"}
                    else:
                        logger.warning(f"'{post_type}': нет данных, лист не будет создан")
                except NotFoundError:
                    logger.warning(f"'{post_type}': REST API недоступен (404), пропускаем. "
                                   f"Добавьте '{post_type}' в WP_SKIP_POST_TYPE_PATTERNS в .env")
                return None, None

            async def _fetch_taxonomy(taxonomy: str):
                try:
                    normalized_taxonomy = normalize_taxonomy_name(taxonomy)
                    tax_endpoint = TaxonomiesEndpoint(client, normalized_taxonomy)
                    terms = await tax_endpoint.get_all()
                    if terms:
                        fields = await discovery.analyze_fields_from_data(terms, "taxonomy")
                        logger.info(f"'{taxonomy}': выгружено {len(terms)} терминов, {len(fields)} полей")
                        return taxonomy, {"data": terms, "fields": fields, "type": "taxonomy"}
                    else:
                        logger.warning(f"'{taxonomy}': нет данных, лист не будет создан")
                except NotFoundError:
                    logger.warning(f"'{taxonomy}': REST API недоступен (404), пропускаем. "
                                   f"Добавьте '{taxonomy}' в WP_SKIP_TAXONOMY_PATTERNS в .env")
                return None, None

            # Запускаем все запросы параллельно
            tasks = []
            tasks.extend([_fetch_post_type(pt) for pt in all_post_types])
            tasks.extend([_fetch_taxonomy(t) for t in all_taxonomies])

            results = await asyncio.gather(*tasks)
            for name, data in results:
                if name and data:
                    sheets_data[name] = data

            # 6. Создаём Excel файл
            logger.info(f"Создание Excel файла {excel_file}...")
            sheets_fields = {name: meta["fields"] for name, meta in sheets_data.items()}
            creator = ExcelCreator()
            creator.create_file_with_fields(excel_file, sheets_fields)

            # 7. Записываем данные
            logger.info("Запись данных в Excel...")
            excel_writer = ExcelWriter(excel_file)
            excel_writer.load()

            # Строим кэш таксономий из уже загруженных данных (без повторных запросов)
            logger.info("Построение кэша таксономий из загруженных данных...")
            taxonomy_cache = {}

            # Используем уже загруженные данные таксономий
            for sheet_name, sheet_meta in sheets_data.items():
                if sheet_meta["type"] == "taxonomy":
                    taxonomy_cache[sheet_name] = {
                        term["id"]: term["name"]
                        for term in sheet_meta["data"]
                    }

            # categories/tags могут не быть как листы, но нужны для маппинга ID→имена в контенте
            for tax_name in ["categories", "tags"]:
                if tax_name not in taxonomy_cache:
                    try:
                        normalized = normalize_taxonomy_name(tax_name)
                        tax_endpoint = TaxonomiesEndpoint(client, normalized)
                        terms = await tax_endpoint.get_all()
                        taxonomy_cache[tax_name] = {
                            term["id"]: term["name"] for term in terms
                        }
                        logger.debug(f"Дозагружено {len(terms)} терминов для '{tax_name}'")
                    except Exception as e:
                        logger.warning(f"Не удалось загрузить термины для '{tax_name}': {e}")
                        taxonomy_cache[tax_name] = {}

            logger.info(f"Кэш таксономий: {len(taxonomy_cache)} таксономий")

            mapper = ResponseMapper(
                taxonomy_cache=taxonomy_cache,
                nested_paths=Settings().get_seo_fields(),
                content_output_dir=content_output_dir,
            )

            for sheet_name, sheet_meta in sheets_data.items():
                logger.info(f"Запись данных в лист '{sheet_name}'...")

                if sheet_meta["type"] == "content":
                    rows = [mapper.map_post_to_excel(post) for post in sheet_meta["data"]]
                else:
                    rows = [mapper.map_term_to_excel(term) for term in sheet_meta["data"]]

                if limit:
                    rows_limited = rows[:limit]
                    excel_writer.replace_rows(sheet_name, rows_limited)
                    logger.info(f"Записано {len(rows_limited)} строк (лимит: {limit})")
                else:
                    excel_writer.replace_rows(sheet_name, rows)
                    logger.info(f"Записано {len(rows)} строк")

            excel_writer.save()
            logger.info("OK Данные успешно записаны в Excel")
            return

        # === MANUAL MODE ===
        logger.info("Загрузка кэша таксономий...")
        from ..mappers.field_detector import FieldDetector

        taxonomies_to_load = set(["categories", "tags"])

        excel_reader = ExcelReader(excel_file, known_taxonomies=Settings().get_taxonomies())
        for sheet_name in sheets:
            try:
                df = excel_reader.read_sheet(sheet_name)
                detector = FieldDetector(df.columns.tolist())
                taxonomy_fields = detector.get_taxonomy_fields()

                for tax_field in taxonomy_fields:
                    if tax_field not in ["categories", "tags"]:
                        taxonomies_to_load.add(tax_field)
            except Exception as e:
                logger.warning(f"Не удалось прочитать структуру листа '{sheet_name}': {e}")

        logger.info(f"Найдено таксономий для загрузки: {taxonomies_to_load}")

        taxonomy_cache_data = {}
        for taxonomy in taxonomies_to_load:
            try:
                normalized_taxonomy = normalize_taxonomy_name(taxonomy)
                tax_endpoint = TaxonomiesEndpoint(client, normalized_taxonomy)
                terms = await tax_endpoint.get_all()
                taxonomy_cache_data[taxonomy] = {
                    term["id"]: term["name"] for term in terms
                }
                logger.info(f"Загружено {len(terms)} терминов для '{taxonomy}'")
            except Exception as e:
                logger.warning(f"Не удалось загрузить таксономию '{taxonomy}': {e}")
                taxonomy_cache_data[taxonomy] = {}

        mapper = ResponseMapper(
            taxonomy_cache=taxonomy_cache_data,
            nested_paths=Settings().get_seo_fields(),
            content_output_dir=content_output_dir,
        )

        excel_writer = ExcelWriter(excel_file)
        excel_writer.load()

        for sheet_name in sheets:
            logger.info(f"\n{'='*50}")
            logger.info(f"Получение данных для листа: {sheet_name}")
            logger.info(f"{'='*50}")

            if is_taxonomy_column(sheet_name):
                taxonomy_name = normalize_taxonomy_name(sheet_name)
                tax_endpoint = TaxonomiesEndpoint(client, taxonomy_name)

                logger.info(f"Получение терминов таксономии '{taxonomy_name}'...")
                terms = await tax_endpoint.get_all()

                if limit:
                    terms = terms[:limit]

                logger.info(f"Получено {len(terms)} терминов")

                if not terms:
                    logger.warning(f"Нет терминов для добавления в {sheet_name}")
                    continue

                rows = [mapper.map_term_to_excel(term) for term in terms]
            else:
                posts_endpoint = PostsEndpoint(client, sheet_name)

                logger.info(f"Получение записей из WordPress (status={status})...")
                posts = await posts_endpoint.get_all(status=status)

                if limit:
                    posts = posts[:limit]

                logger.info(f"Получено {len(posts)} записей")

                if not posts:
                    logger.warning(f"Нет записей для добавления в {sheet_name}")
                    continue

                rows = [mapper.map_post_to_excel(post) for post in posts]

            excel_writer.replace_rows(sheet_name, rows)
            logger.info(f"Заменено {len(rows)} строк в листе '{sheet_name}'")

        excel_writer.save()
        logger.info("Excel файл сохранен")


@app.command()
def fetch_media(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к Excel файлу (авто-определяется из --project)",
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи"
    ),
):
    """
    Загрузить список медиафайлов из WordPress в Excel (лист media)
    """
    env_file = resolve_env_file(ctx, env_file)
    file = require_excel_file(ctx, file)

    console.print(f"\n[bold blue]WordPress Bulk Editor - Fetch Media[/bold blue]\n")
    console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    with handle_command_errors(ctx):
        domain, username, password = load_and_print_credentials(
            env_file=env_file,
        )

        wp_config = WordPressConfig.from_credentials(domain, username, password)

        logging_config = LoggingConfig(level="DEBUG" if verbose else "INFO")

        logs_folder = resolve_logs_folder(ctx)
        logs_folder.mkdir(parents=True, exist_ok=True)
        log_file = setup_logger(
            logs_folder=logs_folder,
            config=logging_config,
            command_name="fetch_media",
            console_stream=log_stream(ctx),
        )

        asyncio.run(run_fetch_media(wp_config, file))

        console.print(f"\n[bold green]SUCCESS! Медиафайлы загружены в лист 'media'![/bold green]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")


async def run_fetch_media(wp_config: WordPressConfig, excel_file: Path):
    """
    Основная логика fetch media.
    """
    from ..wordpress.media import MediaEndpoint

    async with WordPressClient(wp_config) as client:
        media_endpoint = MediaEndpoint(client)

        logger.info("Fetching media from WordPress...")
        all_media = await media_endpoint.fetch_all_media()

        if not all_media:
            logger.info("No media files found")
            console.print("[yellow]Медиафайлы не найдены[/yellow]")
            return

        media_rows = []
        for media in all_media:
            media_rows.append({
                "action": "skip",
                "img_path": "",
                "id": media.get("id", ""),
                "date": media.get("date", ""),
                "modified": media.get("modified", ""),
                "title": wp_rendered(media.get("title", {})),
                "type": media.get("type", ""),
                "slug": media.get("slug", ""),
                "link": media.get("link", ""),
                "status": media.get("status", ""),
                "guid": wp_rendered(media.get("guid", {})),
                "caption": wp_rendered(media.get("caption", {})),
                "alt_text": media.get("alt_text", ""),
                "media_type": media.get("media_type", ""),
                "mime_type": media.get("mime_type", ""),
                "post": media.get("post", ""),
                "source_url": media.get("source_url", ""),
                "description": wp_rendered(media.get("description", {})),
            })

        df = pd.DataFrame(media_rows)

        logger.info(f"Writing {len(df)} media records to Excel...")
        mode = "a" if excel_file.exists() else "w"
        kwargs = {"if_sheet_exists": "replace"} if mode == "a" else {}
        with pd.ExcelWriter(excel_file, engine="openpyxl", mode=mode, **kwargs) as writer:
            df.to_excel(writer, sheet_name="media", index=False)

        logger.info(f"Successfully written {len(df)} media records to sheet 'media'")
        console.print(f"[green]Загружено:[/green] {len(df)} медиафайлов")


# ─────────────────────────────────────────────────────────────────────────────
# update — подтянуть конкретные поля из WP в Excel по ID (read-only для WP)
# ─────────────────────────────────────────────────────────────────────────────

def _extract_column_value(api_response: dict, column: str) -> Optional[str]:
    """
    Извлечь значение одной колонки из ответа WordPress API.
    Поддерживает: обычные поля, rendered/raw, excerpt (strip HTML),
    ACF (acf_*), dot-нотацию (seo.title).
    Возвращает None если поле не найдено в ответе.
    """
    import re
    from ..excel.writer import normalize_api_value

    # dot-нотация: seo.title, seo.robots.index
    if is_dot_notation_column(column):
        return read_nested_value(column, api_response)

    # ACF: acf_price → api_response['acf']['price']
    if column.startswith("acf_"):
        acf = api_response.get("acf", {})
        if isinstance(acf, dict):
            val = acf.get(column[4:])
            return normalize_api_value(val) if val is not None else None
        return None

    # excerpt — raw + strip HTML
    if column == "excerpt":
        raw = normalize_api_value(api_response.get("excerpt"))
        if raw:
            raw = re.sub(r"<[^>]+>", "", str(raw)).strip()
        return raw

    # rendered/raw поля
    if column in ("title", "content", "name", "description"):
        if column in api_response:
            return normalize_api_value(api_response[column])
        return None

    # прямые поля (slug, status, date, modified, link, featured_media, …)
    if column in api_response:
        val = api_response[column]
        return normalize_api_value(val) if isinstance(val, dict) else val

    return None


@app.command("update")
def update_from_wp(
    ctx: typer.Context,
    ids: Optional[str] = typer.Option(
        None,
        "--id",
        help="ID записей через запятую: 123,456,789. Без --id — все строки с id на листе.",
    ),
    columns: str = typer.Option(
        ...,
        "--columns",
        "-c",
        help="Колонки для обновления из WordPress: title,excerpt,seo_title",
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Excel файл (авто-определяется из --project)",
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Листы для поиска ID (по умолчанию — все контентные)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Показать изменения без записи в файл"),
    test_mode: bool = typer.Option(False, "--test-mode", help="Обработать только первые 3 строки (для проверки)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    env_file: Optional[Path] = typer.Option(None, "--env-file"),
):
    """
    Подтянуть указанные поля из WordPress в Excel по ID.
    Без --id обновляет все строки с заполненным id на листе.
    WordPress не изменяется — только чтение. Обязательно укажи --columns.
    """
    env_file = resolve_env_file(ctx, env_file)
    file = require_excel_file(ctx, file)

    id_list = [i.strip() for i in ids.split(",") if i.strip()] if ids else None
    column_list = [c.strip() for c in columns.split(",") if c.strip()]

    if not column_list:
        console.print("[red]ERROR: --columns не может быть пустым — укажи хотя бы одну колонку[/red]")
        raise typer.Exit(1)

    console.print(f"\n[bold blue]WordPress Bulk Editor — Update from WP[/bold blue]\n")
    console.print(f"Excel файл: [cyan]{file}[/cyan]")
    if id_list:
        console.print(f"ID:         [cyan]{', '.join(id_list)}[/cyan]")
    else:
        console.print(f"ID:         [cyan]все строки с id[/cyan]")
    console.print(f"Колонки:    [cyan]{', '.join(column_list)}[/cyan]")
    if dry_run:
        console.print("[yellow]Режим DRY-RUN — файл не будет изменён[/yellow]")
    if test_mode:
        console.print("[yellow]Режим TEST — только первые 3 строки[/yellow]")
    console.print()

    with handle_command_errors(ctx):
        domain, username, password = load_and_print_credentials(env_file)
        wp_config = WordPressConfig.from_credentials(domain, username, password)
        sheets_filter = [s.strip() for s in sheets.split(",")] if sheets else None

        asyncio.run(_run_update(
            wp_config=wp_config,
            excel_file=file,
            id_list=id_list,
            column_list=column_list,
            sheets_filter=sheets_filter,
            dry_run=dry_run,
            test_mode=test_mode,
        ))


async def _run_update(
    wp_config: WordPressConfig,
    excel_file: Path,
    id_list: Optional[List[str]],
    column_list: List[str],
    sheets_filter: Optional[List[str]],
    dry_run: bool,
    test_mode: bool = False,
):
    """Основная логика update: WP → Excel (read-only для WP).
    id_list=None означает «все строки с заполненным id».
    """
    from ..constants import SYSTEM_SHEETS

    id_set = set(id_list) if id_list else None
    writer = ExcelWriter(excel_file)
    writer.load()

    total_found = 0
    total_updated = 0

    async with WordPressClient(wp_config) as client:
        await client.test_connection()

        for sheet_name, df in writer._dataframes.items():
            # Пропускаем служебные и нефильтрованные листы
            if sheet_name in SYSTEM_SHEETS:
                continue
            if sheets_filter and sheet_name not in sheets_filter:
                continue
            if "id" not in df.columns:
                logger.debug(f"Лист '{sheet_name}': нет колонки id, пропускаем")
                continue

            # Собрать строки для обновления
            rows_to_update: List[tuple] = []
            for idx, row in df.iterrows():
                raw_id = str(row.get("id", "")).strip()
                if not raw_id or raw_id == "nan":
                    continue
                try:
                    normalized = str(int(float(raw_id)))
                except (ValueError, TypeError):
                    normalized = raw_id
                # Если id_set задан — фильтруем, иначе берём все
                if id_set is not None and normalized not in id_set:
                    continue
                rows_to_update.append((idx, int(float(raw_id))))

            if not rows_to_update:
                continue

            if test_mode:
                rows_to_update = rows_to_update[:3]
                console.print(f"[dim]TEST-MODE: лист '{sheet_name}' — первые {len(rows_to_update)} строк[/dim]")
            elif id_set is None:
                console.print(f"[dim]Лист '{sheet_name}': {len(rows_to_update)} записей[/dim]")

            if not rows_to_update:
                continue

            endpoint = PostsEndpoint(client, sheet_name)

            for row_idx, post_id in rows_to_update:
                total_found += 1
                try:
                    api_response = await endpoint.get(post_id)
                except NotFoundError:
                    console.print(f"[yellow]WARN[/yellow] ID={post_id} не найден на '{sheet_name}' (404)")
                    continue
                except Exception as e:
                    console.print(f"[yellow]WARN[/yellow] ID={post_id} на '{sheet_name}': {e}")
                    continue

                # Извлечь только запрошенные колонки
                existing_cols = df.columns.tolist()
                update_data: dict = {}
                missing_in_excel: list = []
                missing_in_api: list = []

                for col in column_list:
                    if col not in existing_cols:
                        missing_in_excel.append(col)
                        continue
                    val = _extract_column_value(api_response, col)
                    if val is None:
                        missing_in_api.append(col)
                        continue
                    update_data[col] = val

                if missing_in_excel:
                    logger.debug(f"ID={post_id}: колонок нет в Excel: {missing_in_excel}")
                if missing_in_api:
                    logger.debug(f"ID={post_id}: полей нет в ответе API: {missing_in_api}")

                if not update_data:
                    console.print(f"[yellow]SKIP[/yellow] ID={post_id}: ничего не найдено для обновления")
                    continue

                if dry_run:
                    console.print(f"[dim]DRY-RUN[/dim] ID={post_id} ({sheet_name}[{row_idx}]): {update_data}")
                else:
                    writer.update_row(sheet_name, row_idx, update_data)
                    console.print(
                        f"[green]✓[/green] ID={post_id} ({sheet_name}): "
                        f"{', '.join(update_data.keys())}"
                    )
                total_updated += 1

    if not dry_run:
        writer.save()

    action = "Будет обновлено" if dry_run else "Обновлено"
    console.print(f"\n[bold]{action}: {total_updated} из {total_found} найденных записей[/bold]")
