"""
Команды загрузки данных из WordPress: fetch, fetch-media
"""

from pathlib import Path
from typing import Optional, List
import asyncio

import pandas as pd
from loguru import logger

from . import app, console
from ..config import LoggingConfig, WordPressConfig, Settings
from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter
from ..wordpress.client import WordPressClient
from ..wordpress.posts import PostsEndpoint
from ..wordpress.taxonomies import TaxonomiesEndpoint, normalize_taxonomy_name
from ..mappers.response_mapper import ResponseMapper
from ..utils.logger import setup_logger
from ..utils.auth import load_credentials_from_env
from ..exceptions import WPBulkEditorError, NotFoundError

import typer


@app.command()
def fetch(
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
    )
):
    """
    Получение данных из WordPress -> Excel

    Получает записи из WordPress и добавляет их в Excel файл
    после последней непустой строки каждого листа.
    """
    console.print(f"\n[bold blue]WordPress Bulk Editor - Fetch[/bold blue]\n")

    # Определяем режим работы
    if file is None:
        file = Path.cwd() / "fetch.xlsx"
        auto_mode = True
        console.print(f"[yellow]Auto mode:[/yellow] будет создан файл {file}\n")

        if file.exists():
            if not typer.confirm(f"Файл {file} уже существует. Перезаписать?"):
                console.print("[red]Отменено пользователем[/red]")
                raise typer.Exit(1)
    else:
        auto_mode = False
        console.print(f"[yellow]Manual mode:[/yellow] используется файл {file}\n")

    console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    try:
        # 1. Настройка логирования
        logs_folder = Path.cwd() / "logs"
        logs_folder.mkdir(parents=True, exist_ok=True)

        logging_config = LoggingConfig()
        log_file = setup_logger(
            logs_folder=logs_folder,
            config=logging_config,
            command_name="fetch"
        )
        logger.info(f"Лог-файл создан: {log_file}")

        # 2. Загружаем credentials
        console.print("[yellow]Загрузка credentials...[/yellow]")
        domain, username, password = load_credentials_from_env(
            env_file=env_file,
            domain_override=domain,
            username_override=username,
            password_override=password
        )
        console.print(f"[green]OK[/green] WordPress: {domain}\n")

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
            with ExcelReader(file) as reader:
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

        wp_config = WordPressConfig(
            url=domain,
            username=username,
            password=password
        )

        asyncio.run(fetch_and_append(
            wp_config=wp_config,
            excel_file=file,
            sheets=sheets_to_fetch,
            status=status,
            limit=limit,
            auto_mode=auto_mode,
            exclude=exclude
        ))

        console.print(f"\n[bold green]SUCCESS! Данные добавлены в Excel![/bold green]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


async def fetch_and_append(
    wp_config: WordPressConfig,
    excel_file: Path,
    sheets: List[str],
    status: str,
    limit: Optional[int],
    auto_mode: bool = False,
    exclude: Optional[str] = None
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
            all_post_types = await discovery.discover_post_types()

            # 2. Обнаружение таксономий
            logger.info("Обнаружение таксономий...")
            all_taxonomies = await discovery.discover_taxonomies()

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

            # 5. Выгружаем данные и анализируем поля
            logger.info("Выгрузка данных для анализа полей...")
            sheets_data = {}

            for post_type in all_post_types:
                logger.info(f"Выгрузка данных для '{post_type}'...")
                try:
                    posts_endpoint = PostsEndpoint(client, post_type)
                    posts = await posts_endpoint.get_all(status=status)

                    if posts:
                        fields = await discovery.analyze_fields_from_data(posts, "content")
                        sheets_data[post_type] = {
                            "data": posts,
                            "fields": fields,
                            "type": "content"
                        }
                        logger.info(f"'{post_type}': выгружено {len(posts)} записей, {len(fields)} полей")
                    else:
                        logger.warning(f"'{post_type}': нет данных, лист не будет создан")
                except NotFoundError:
                    logger.warning(f"'{post_type}': REST API недоступен (404), пропускаем. "
                                   f"Добавьте '{post_type}' в WP_SKIP_POST_TYPE_PATTERNS в .env")

            for taxonomy in all_taxonomies:
                logger.info(f"Выгрузка данных для таксономии '{taxonomy}'...")
                try:
                    normalized_taxonomy = normalize_taxonomy_name(taxonomy)
                    tax_endpoint = TaxonomiesEndpoint(client, normalized_taxonomy)
                    terms = await tax_endpoint.get_all()

                    if terms:
                        fields = await discovery.analyze_fields_from_data(terms, "taxonomy")
                        sheets_data[taxonomy] = {
                            "data": terms,
                            "fields": fields,
                            "type": "taxonomy"
                        }
                        logger.info(f"'{taxonomy}': выгружено {len(terms)} терминов, {len(fields)} полей")
                    else:
                        logger.warning(f"'{taxonomy}': нет данных, лист не будет создан")
                except NotFoundError:
                    logger.warning(f"'{taxonomy}': REST API недоступен (404), пропускаем. "
                                   f"Добавьте '{taxonomy}' в WP_SKIP_TAXONOMY_PATTERNS в .env")

            # 6. Создаём Excel файл
            logger.info(f"Создание Excel файла {excel_file}...")
            sheets_fields = {name: meta["fields"] for name, meta in sheets_data.items()}
            creator = ExcelCreator()
            creator.create_file_with_fields(excel_file, sheets_fields)

            # 7. Записываем данные
            logger.info("Запись данных в Excel...")
            excel_writer = ExcelWriter(excel_file)
            excel_writer.load()

            # Загружаем taxonomy cache
            logger.info("Загрузка кэша таксономий для преобразования ID в названия...")
            taxonomy_cache = {}

            all_taxonomies_for_cache = set()
            all_taxonomies_for_cache.add("categories")
            all_taxonomies_for_cache.add("tags")

            for sheet_name, sheet_meta in sheets_data.items():
                if sheet_meta["type"] == "taxonomy":
                    all_taxonomies_for_cache.add(sheet_name)

            for taxonomy in all_taxonomies_for_cache:
                try:
                    normalized_taxonomy = normalize_taxonomy_name(taxonomy)
                    tax_endpoint = TaxonomiesEndpoint(client, normalized_taxonomy)
                    terms = await tax_endpoint.get_all()
                    taxonomy_cache[taxonomy] = {
                        term["id"]: term["name"]
                        for term in terms
                    }
                    logger.debug(f"Загружено {len(terms)} терминов для таксономии '{taxonomy}'")
                except Exception as e:
                    logger.warning(f"Не удалось загрузить термины для '{taxonomy}': {e}")
                    taxonomy_cache[taxonomy] = {}

            logger.info(f"Кэш таксономий загружен: {len(taxonomy_cache)} таксономий")

            mapper = ResponseMapper(taxonomy_cache=taxonomy_cache)

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

        excel_reader = ExcelReader(excel_file)
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

        mapper = ResponseMapper(taxonomy_cache=taxonomy_cache_data)

        excel_writer = ExcelWriter(excel_file)
        excel_writer.load()

        for sheet_name in sheets:
            logger.info(f"\n{'='*50}")
            logger.info(f"Получение данных для листа: {sheet_name}")
            logger.info(f"{'='*50}")

            is_taxonomy = (
                sheet_name in ["category", "tag", "categories", "tags"] or
                sheet_name.endswith("-cat")
            )

            if is_taxonomy:
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
    file: Path = typer.Option(
        ...,
        "--file",
        "-f",
        help="Путь к Excel файлу",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
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
    console.print(f"\n[bold blue]WordPress Bulk Editor - Fetch Media[/bold blue]\n")
    console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

    try:
        console.print("[yellow]Загрузка credentials из .env...[/yellow]")
        domain, username, password = load_credentials_from_env(env_file=env_file)
        console.print(f"[green]OK[/green] WordPress: {domain}\n")

        wp_config = WordPressConfig(
            url=domain,
            username=username,
            password=password
        )

        logging_config = LoggingConfig(level="DEBUG" if verbose else "INFO")

        logs_folder = Path("logs")
        logs_folder.mkdir(parents=True, exist_ok=True)
        log_file = setup_logger(
            logs_folder=logs_folder,
            config=logging_config,
            command_name="fetch_media"
        )

        asyncio.run(run_fetch_media(wp_config, file))

        console.print(f"\n[bold green]SUCCESS! Медиафайлы загружены в лист 'media'![/bold green]")
        console.print(f"Лог-файл: [cyan]{log_file}[/cyan]\n")

    except WPBulkEditorError as e:
        console.print(f"\n[bold red]ERROR: {e}[/bold red]\n")
        raise typer.Exit(code=1)
    except Exception as e:
        console.print(f"\n[bold red]UNEXPECTED ERROR: {e}[/bold red]\n")
        logger.exception("Неожиданная ошибка")
        raise typer.Exit(code=1)


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
            title = media.get("title", {})
            if isinstance(title, dict):
                title_text = title.get("rendered", "")
            else:
                title_text = str(title)

            caption = media.get("caption", {})
            if isinstance(caption, dict):
                caption_text = caption.get("rendered", "")
            else:
                caption_text = str(caption) if caption else ""

            description = media.get("description", {})
            if isinstance(description, dict):
                description_text = description.get("rendered", "")
            else:
                description_text = str(description) if description else ""

            media_rows.append({
                "action": "skip",
                "img_path": "",
                "id": media.get("id", ""),
                "date": media.get("date", ""),
                "modified": media.get("modified", ""),
                "title": title_text,
                "type": media.get("type", ""),
                "slug": media.get("slug", ""),
                "link": media.get("link", ""),
                "status": media.get("status", ""),
                "guid": media.get("guid", {}).get("rendered", ""),
                "caption": caption_text,
                "alt_text": media.get("alt_text", ""),
                "media_type": media.get("media_type", ""),
                "mime_type": media.get("mime_type", ""),
                "post": media.get("post", ""),
                "source_url": media.get("source_url", ""),
            })

        df = pd.DataFrame(media_rows)

        logger.info(f"Writing {len(df)} media records to Excel...")
        with pd.ExcelWriter(excel_file, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            df.to_excel(writer, sheet_name="media", index=False)

        logger.info(f"Successfully written {len(df)} media records to sheet 'media'")
        console.print(f"[green]Загружено:[/green] {len(df)} медиафайлов")
