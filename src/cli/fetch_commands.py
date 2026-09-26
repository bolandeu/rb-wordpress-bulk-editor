"""
Команды загрузки данных из WordPress: fetch, fetch-media
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse
import asyncio

import pandas as pd
from loguru import logger

from . import app, console
from .. import output
from ..output import log_stream
from .common import handle_command_errors, load_and_print_credentials, parse_comma_list, setup_command_logging
from .helpers import get_project, resolve_env_file, resolve_logs_folder, require_excel_file
from ..config import LoggingConfig, WordPressConfig, Settings
from ..constants import is_taxonomy_column
from ..utils.wp_helpers import wp_rendered
from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter
from ..wordpress.client import WordPressClient
from ..wordpress.posts import PostsEndpoint
from ..wordpress.taxonomies import TaxonomiesEndpoint, normalize_taxonomy_name
from ..mappers.response_mapper import ResponseMapper
from ..utils.logger import setup_logger
from ..exceptions import NotFoundError, ValidationError
from ..records.json_fetch import build_site_tab
from ..records.json_source import read_tab_if_exists, write_tab
from ..records.selection import is_json_source

import typer


def _site_host(domain: str) -> str:
    """`https://site.ru:8443/wp` → `site.ru` (имя таба и файла по умолчанию)."""
    raw = domain.strip()
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    return parsed.hostname or raw


def _refuse_project_registry(proj, file: Path) -> None:
    """
    Не писать выгрузку в реестр проекта `<project>/<project>.json`.

    Реестр antagor пересобирается из Google-таблицы: записи, которых нет
    в таблице, при следующей пересборке молча исчезают. Выгрузка с сайта
    должна жить в отдельном файле (по умолчанию `wp-fetch/<домен>.json`).
    """
    if not (proj.active and proj.root and proj.name):
        return
    registry = (proj.root / f"{proj.name}.json").resolve()
    if Path(file).resolve() == registry:
        raise ValidationError(
            f"fetch не пишет в реестр проекта {registry.name}: его пересобирают из "
            "Google-таблицы, и снятые с сайта записи при пересборке пропадут. "
            "Укажите другой --file (по умолчанию — wp-fetch/<домен>.json).",
        )


@app.command()
@output.guard
def fetch(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Файл выгрузки: .xlsx (по умолчанию fetch.xlsx) или .json (один таб на сайт)",
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
        help="Типы записей и таксономии через запятую (posts,pages,category)"
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
        help="Максимум записей на лист (в JSON — на тип записи/таксономию)"
    ),
    exclude: Optional[str] = typer.Option(
        None,
        "--exclude",
        help="Список типов для исключения через запятую (posts,category)"
    ),
    to_json: bool = typer.Option(
        False,
        "--to-json",
        help="Выгрузить в JSON без --file: <проект>/wp-fetch/<домен>.json",
    ),
    tab: Optional[str] = typer.Option(
        None,
        "--tab",
        help="Имя таба JSON (по умолчанию — домен сайта)",
    ),
    confirm: bool = output.ConfirmOption,
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Получение данных из WordPress -> Excel или JSON.

    Excel (по умолчанию): лист на каждый тип записи и таксономию.

    JSON (--file *.json или --to-json): один таб на сайт — имя таба = домен
    (или --tab). Термины и контент лежат в одном табе и различаются полями:
    у термина заполнен taxonomy (имя в name, текст в description), у контента —
    type. ACF/SEO пишутся dot-нотацией (acf.f1, seo.title). Таб пересобирается
    целиком, _row_num нумеруется с 2; остальные табы файла не трогаются.
    Существующий непустой таб перезаписывается только после подтверждения.
    В реестр проекта <проект>/<проект>.json fetch не пишет.

    Exit-коды: 0 — успех, 1 — отказ в подтверждении, 2 — аргументы/файл,
    3 — нет credentials, 5 — недоступен WordPress.
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    if non_interactive:
        output.get_options(ctx).non_interactive = True

    if to_json or (file is not None and is_json_source(file)):
        _fetch_to_json(
            ctx, proj, file, env_file, domain, username, password,
            sheets, status, limit, exclude, tab, confirm,
        )
        return
    if tab:
        raise ValidationError("--tab применяется только к выгрузке в JSON (--file *.json или --to-json)")

    opts = output.get_options(ctx)
    say = console.print if not (opts.json or opts.quiet) else (lambda *args, **kwargs: None)
    say("\n[bold blue]WordPress Bulk Editor - Fetch[/bold blue]\n")

    # Определяем режим работы
    if file is None:
        file = proj.resolve_output("fetch.xlsx") if proj.active else Path.cwd() / "fetch.xlsx"
        auto_mode = True
        say(f"[yellow]Auto mode:[/yellow] будет создан файл {file}\n")

        if file.exists():
            output.require_confirmation(
                ctx, f"Файл {file} уже существует. Перезаписать?", confirm
            )
    else:
        auto_mode = False
        say(f"[yellow]Manual mode:[/yellow] используется файл {file}\n")

    say(f"Excel файл: [cyan]{file}[/cyan]\n")

    log_file = setup_command_logging(ctx, "fetch")
    logger.info(f"Лог-файл создан: {log_file}")

    domain, username, password = load_and_print_credentials(
        env_file=env_file,
        domain=domain,
        username=username,
        password=password,
        ctx=ctx,
    )

    # Определяем листы для обработки
    if auto_mode:
        sheets_to_fetch = parse_comma_list(sheets) or []
    else:
        with ExcelReader(file, known_taxonomies=Settings().get_taxonomies()) as reader:
            sheets_by_type = reader.get_sheets_by_type()
            sheets_to_fetch = parse_comma_list(sheets) or (
                sheets_by_type["content"] + sheets_by_type["taxonomy"]
            )
        say(f"[green]OK[/green] Листы для обработки: {', '.join(sheets_to_fetch)}\n")

    if exclude:
        exclude_list = parse_comma_list(exclude) or []
        sheets_to_fetch = [s for s in sheets_to_fetch if s not in exclude_list]

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

    def _render(payload: dict):
        console.print("\n[bold green]SUCCESS! Данные добавлены в Excel![/bold green]\n")

    output.emit_success(ctx, {"file": str(file), "format": "xlsx", "log_file": str(log_file)}, _render)


def _fetch_to_json(
    ctx, proj, file, env_file, domain, username, password,
    sheets, status, limit, exclude, tab, confirm,
) -> None:
    """fetch в JSON: один таб на сайт, таб пересобирается целиком."""
    if file is not None:
        _refuse_project_registry(proj, file)

    domain, username, password = load_and_print_credentials(
        env_file=env_file, domain=domain, username=username, password=password, ctx=ctx,
    )
    host = _site_host(domain)
    if file is None:
        file = (proj.root if proj.active else Path.cwd()) / "wp-fetch" / f"{host}.json"
        _refuse_project_registry(proj, file)
    tab_name = tab or host

    # До обращения к сайту: файл должен быть JSON-файлом данных, а пересборка
    # непустого таба — подтверждена (локальные правки в нём пропадут).
    existing = read_tab_if_exists(file, tab_name)
    if existing:
        output.require_confirmation(
            ctx,
            f"Таб '{tab_name}' в {file} уже содержит {len(existing)} записей и будет "
            "пересобран с нуля. Продолжить?",
            confirm,
        )

    log_file = setup_command_logging(ctx, "fetch")
    wp_config = WordPressConfig.from_credentials(domain, username, password)
    records = asyncio.run(_fetch_json_tab(
        wp_config, parse_comma_list(sheets) or [], status, limit, exclude,
    ))
    write_tab(file, tab_name, records, project=proj.name if proj.active else None)

    by_type: Dict[str, int] = {}
    for record in records:
        key = record.get("taxonomy") or record.get("type")
        by_type[key] = by_type.get(key, 0) + 1

    def _render(payload: dict):
        console.print(f"\n[bold green]SUCCESS![/bold green] {payload['records']} записей → "
                      f"{payload['file']} (таб '{payload['tab']}')")
        for name, count in payload["by_type"].items():
            console.print(f"  {name}: {count}")

    output.emit_success(ctx, {
        "file": str(file), "format": "json", "tab": tab_name,
        "records": len(records), "by_type": by_type, "log_file": str(log_file),
    }, _render)


async def _fetch_json_tab(
    wp_config: WordPressConfig,
    sheets: List[str],
    status: str,
    limit: Optional[int],
    exclude: Optional[str],
) -> List[Dict[str, Any]]:
    """Снять с сайта типы записей и таксономии и собрать из них записи таба."""
    async with WordPressClient(wp_config) as client:
        await client.test_connection()
        sheets_data, taxonomy_cache = await _fetch_site_data(client, sheets, exclude, status)

    mapper = ResponseMapper(
        taxonomy_cache=taxonomy_cache,
        nested_paths=Settings().get_seo_fields(),
        excel_cell_limit=False,
    )

    def _limited(items):
        return items[:limit] if limit else items

    def _of_type(kind: str):
        return [(name, _limited(meta["data"])) for name, meta in sheets_data.items() if meta["type"] == kind]

    terms, posts = _of_type("taxonomy"), _of_type("content")
    return build_site_tab(mapper, terms, posts)


async def _fetch_site_data(
    client: WordPressClient,
    sheets: List[str],
    exclude: Optional[str],
    status: str,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[int, str]]]:
    """
    Обнаружить типы записей и таксономии сайта и выгрузить их данные.

    Returns:
        (sheets_data, taxonomy_cache): sheets_data — {имя: {"data", "fields",
        "type": "content"|"taxonomy"}}; taxonomy_cache — {таксономия: {id: имя}}
        для замены id терминов на имена в контенте.
    """
    from ..wordpress.discovery import WordPressDiscovery

    settings = Settings()
    discovery = WordPressDiscovery(
        client,
        skip_post_type_patterns=settings.get_skip_post_type_patterns(),
        skip_taxonomy_patterns=settings.get_skip_taxonomy_patterns(),
    )

    logger.info("Обнаружение типов записей...")
    explicit_sheets = bool(sheets)
    all_post_types = await discovery.discover_post_types(include_skipped=explicit_sheets)

    logger.info("Обнаружение таксономий...")
    all_taxonomies = await discovery.discover_taxonomies(include_skipped=explicit_sheets)

    if sheets:
        all_post_types = [pt for pt in all_post_types if pt in sheets]
        all_taxonomies = [t for t in all_taxonomies if t in sheets]

    if exclude:
        exclude_list = [s.strip() for s in exclude.split(",")]
        all_post_types = [pt for pt in all_post_types if pt not in exclude_list]
        all_taxonomies = [t for t in all_taxonomies if t not in exclude_list]
        logger.info(f"Исключены: {', '.join(exclude_list)}")

    logger.info("Выгрузка данных (параллельно)...")

    async def _fetch_post_type(post_type: str):
        try:
            posts_endpoint = PostsEndpoint(client, post_type)
            posts = await posts_endpoint.get_all(status=status)
            if posts:
                fields = await discovery.analyze_fields_from_data(posts, "content")
                logger.info(f"'{post_type}': выгружено {len(posts)} записей, {len(fields)} полей")
                return post_type, {"data": posts, "fields": fields, "type": "content"}
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
            logger.warning(f"'{taxonomy}': нет данных, лист не будет создан")
        except NotFoundError:
            logger.warning(f"'{taxonomy}': REST API недоступен (404), пропускаем. "
                           f"Добавьте '{taxonomy}' в WP_SKIP_TAXONOMY_PATTERNS в .env")
        return None, None

    tasks = [_fetch_post_type(pt) for pt in all_post_types]
    tasks += [_fetch_taxonomy(t) for t in all_taxonomies]
    sheets_data: Dict[str, Dict[str, Any]] = {}
    for name, data in await asyncio.gather(*tasks):
        if name and data:
            sheets_data[name] = data

    # Кэш таксономий из уже загруженных данных (без повторных запросов)
    taxonomy_cache: Dict[str, Dict[int, str]] = {
        name: {term["id"]: term["name"] for term in meta["data"]}
        for name, meta in sheets_data.items()
        if meta["type"] == "taxonomy"
    }
    # categories/tags могут не быть выбраны, но нужны для маппинга ID→имена в контенте
    for tax_name in ["categories", "tags"]:
        if tax_name not in taxonomy_cache:
            try:
                terms = await TaxonomiesEndpoint(client, normalize_taxonomy_name(tax_name)).get_all()
                taxonomy_cache[tax_name] = {term["id"]: term["name"] for term in terms}
                logger.debug(f"Дозагружено {len(terms)} терминов для '{tax_name}'")
            except Exception as e:
                logger.warning(f"Не удалось загрузить термины для '{tax_name}': {e}")
                taxonomy_cache[tax_name] = {}
    logger.info(f"Кэш таксономий: {len(taxonomy_cache)} таксономий")

    return sheets_data, taxonomy_cache


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
            from ..excel.creator import ExcelCreator

            sheets_data, taxonomy_cache = await _fetch_site_data(client, sheets, exclude, status)

            logger.info(f"Создание Excel файла {excel_file}...")
            sheets_fields = {name: meta["fields"] for name, meta in sheets_data.items()}
            creator = ExcelCreator()
            creator.create_file_with_fields(excel_file, sheets_fields)

            logger.info("Запись данных в Excel...")
            excel_writer = ExcelWriter(excel_file)
            excel_writer.load()

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
                    rows = rows[:limit]
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
