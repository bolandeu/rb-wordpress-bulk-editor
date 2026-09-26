"""
Утилитарные команды: validate, options, bulk-delete, counter, llm, reshape
"""

from pathlib import Path
from typing import Optional, List, Dict
import asyncio

import pandas as pd
from loguru import logger

from . import app, console
from .common import handle_command_errors, load_and_print_credentials, setup_command_logging
from .helpers import (
    get_project, require_data_file, require_excel_file, resolve_env_file, resolve_excel_file, resolve_output,
)
from .project import find_repo_root
from .. import output
from ..config import WordPressConfig
from ..constants import normalize_action
from ..exceptions import ValidationError
from ..excel.reader import ExcelReader
from ..records.excel import ExcelRecordSource
from ..records.json_validation import check_tab
from ..records.preflight import FieldRules, check_sources, raise_if_errors
from ..records.selection import dataset_names, is_json_source, require_json_tabs
from ..wordpress.client import WordPressClient
from ..wordpress.posts import PostsEndpoint
from ..wordpress.options import OptionsEndpoint
from ..utils.wp_helpers import wp_rendered

import typer


def _make_wp_config(domain: str, username: str, password: str) -> WordPressConfig:
    """Создать WordPressConfig из credentials (с WP_VERIFY_SSL из .env)."""
    return WordPressConfig.from_credentials(domain, username, password)


@app.command()
@output.guard
def validate(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Файл данных .xlsx или .json (авто-определяется из --project)"
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Листы Excel / табы JSON через запятую (для JSON обязателен)",
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
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Проверка файла данных перед отправкой — без синхронизации и без сайта.

    Та же проверка автоматически идёт перед sync/async (отключается
    --skip-checks). Сообщаются все проблемы сразу:

    - имена полей: всё, кроме acf.* / acf_* / meta.*, — известное поле
      WordPress, SEO-поле или своё поле из WP_EXTRA_FIELDS; к опечатке
      подсказывается похожее имя (seo.descripton → seo.description);
    - имена таксономий: встроенные (categories, tags) или с частью
      cat / tax / term / tag через _ или - (rb_services_cat, region-term),
      строчными; свои — в WP_TAXONOMIES;
    - значения записей add/update/delete: id у update/delete, status,
      дубли slug, разделитель терминов "; ", да/нет в seo.robots.*,
      существование файлов в значениях-путях (acf.more = "content/1.html").

    Записи skip / без action на сайт не уходят: их проблемы — предупреждения.

    Excel: content- и taxonomy-листы (или --sheets), затем проверка
    credentials. JSON: --sheets обязателен, плюс структура таба (ровно одно
    из type/taxonomy, уникальный _row_num); credentials не нужны.

    Exit-коды: 0 — файл валиден, 2 — ошибки в файле/аргументах, 3 — нет
    credentials (Excel), 1 — непредвиденная ошибка.
    """
    env_file = resolve_env_file(ctx, env_file)
    file = require_data_file(ctx, file)
    if is_json_source(file):
        _validate_json(ctx, file, sheets)
        return

    options = output.get_options(ctx)
    if not options.json and not options.quiet:
        console.print(f"\n[bold blue]Валидация файла[/bold blue]: {file}\n")

    setup_command_logging(ctx, "validate")

    # Файл проверяется до credentials: это вопрос к файлу, не к сайту
    output.vecho(ctx, "Проверка листов...")
    rules = FieldRules.from_settings()
    with ExcelReader(file, known_taxonomies=rules.known_taxonomies) as reader:
        by_type = reader.get_sheets_by_type()
        names = dataset_names(sheets) if sheets is not None else by_type["content"] + by_type["taxonomy"]
        checks = check_sources(
            [ExcelRecordSource(reader, name) for name in names], rules, base_dir=file.parent,
        )
    raise_if_errors(file.name, checks)

    domain, username, password = load_and_print_credentials(env_file=env_file, ctx=ctx)

    def _render(payload: dict):
        _render_checks(payload["tabs"])

    output.emit_success(
        ctx,
        {
            "file": str(file),
            "format": "xlsx",
            "valid": True,
            "domain": domain,
            "content_sheets": list(by_type["content"]),
            "taxonomy_sheets": list(by_type["taxonomy"]),
            "tabs": [c.to_dict() for c in checks],
        },
        _render,
    )


def _render_checks(tabs: List[dict]) -> None:
    for check in tabs:
        console.print(f"[green]OK[/green] '{check['tab']}': {check['records']} записей")
        for warning in check["warnings"]:
            console.print(f"  [yellow]WARN[/yellow] {warning}")
    console.print("\n[bold green]SUCCESS! File is valid![/bold green]\n")


def _validate_json(ctx: typer.Context, file: Path, sheets: Optional[str]) -> None:
    """validate для JSON: все проблемы табов сразу, без credentials и сети."""
    tabs = require_json_tabs(file, sheets)
    rules = FieldRules.from_settings()
    checks = [check_tab(file, tab, rules) for tab in tabs]
    raise_if_errors(file.name, checks)

    def _render(payload: dict):
        _render_checks(payload["tabs"])

    output.emit_success(
        ctx,
        {"file": str(file), "format": "json", "valid": True, "tabs": [c.to_dict() for c in checks]},
        _render,
    )


@app.command()
@output.guard
def options(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к Excel файлу",
        exists=False,
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
    fetch: bool = typer.Option(
        False,
        "--fetch",
        help="Получить настройки из WordPress в Excel (вместо загрузки)"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Показать что будет отправлено, без отправки"
    ),
    confirm: bool = output.ConfirmOption,
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Управление настройками сайта WordPress.

    Без --fetch: обновление WordPress из Excel
    С --fetch: выгрузка настроек из WordPress в Excel

    Запись настроек на сайт и перезапись существующего xlsx откатить нельзя:
    сначала доступен --dry-run, затем спрашивается подтверждение, а под
    --non-interactive требуется явный --confirm.

    Exit-коды: 0 — успех, 2 — аргументы/файл или отсутствующий --confirm,
    3 — нет credentials,
    5 — недоступен WordPress, 1 — непредвиденная ошибка.
    """
    env_file = resolve_env_file(ctx, env_file)
    # Если активен --project и --file не указан — берём xlsx из папки проекта.
    # Без проекта это даёт None, и каждая ветка ниже сама решает что делать.
    if file is None:
        file = resolve_excel_file(ctx, file)

    opts = output.get_options(ctx)
    human_mode = not opts.json and not opts.quiet

    if human_mode:
        console.print(f"\n[bold blue]WordPress Bulk Editor - Options[/bold blue]\n")

    command_name = "options_fetch" if fetch else "options"
    setup_command_logging(ctx, command_name)
    logger.info(f"Режим: {'FETCH (выгрузка)' if fetch else 'UPDATE (загрузка)'}")

    # Режим FETCH - выгрузка из WordPress в Excel
    if fetch:
        # Определяем файл
        if file is None:
            file = Path.cwd() / "options.xlsx"
            if human_mode:
                console.print(f"[yellow]Auto mode:[/yellow] будет создан файл {file}\n")

            # Проверяем существование
            if file.exists():
                output.require_confirmation(
                    ctx, f"Файл {file} уже существует. Перезаписать?", confirm
                )
        elif human_mode:
            console.print(f"[yellow]Manual mode:[/yellow] используется файл {file}\n")

        if human_mode:
            console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

        # 1. Загружаем credentials
        domain, username, password = load_and_print_credentials(env_file=env_file, ctx=ctx)

        # 2. Получаем настройки из WordPress
        output.vecho(ctx, "Получение настроек из WordPress...")

        wp_config = _make_wp_config(domain, username, password)

        site_options = asyncio.run(_fetch_options(wp_config))

        if not site_options:
            output.emit_success(
                ctx,
                {"mode": "fetch", "file": str(file), "options": {}, "written": False},
                lambda _: console.print("[yellow]WARNING: Нет настроек для выгрузки[/yellow]\n"),
            )
            return

        # 3. Записываем в Excel
        output.vecho(ctx, "Запись настроек в Excel...")
        from ..excel.options import OptionsWriter
        writer = OptionsWriter(file)
        writer.write(site_options, mode="replace")

        def _render_fetch(payload: dict):
            console.print(f"[green]OK[/green] Получено настроек: {len(payload['options'])}\n")
            output.human.key_values("Полученные настройки:", payload["options"])
            console.print(f"\n[bold green]SUCCESS! Настройки выгружены в {payload['file']}![/bold green]\n")

        output.emit_success(
            ctx,
            {"mode": "fetch", "file": str(file), "options": site_options, "written": True},
            _render_fetch,
        )

    # Режим UPDATE - загрузка из Excel в WordPress
    else:
        if file is None:
            raise ValidationError("Параметр --file обязателен для загрузки настроек")

        if not file.exists():
            raise ValidationError(f"Файл не найден: {file}")

        if human_mode:
            console.print(f"Excel файл: [cyan]{file}[/cyan]\n")

        # 1. Загружаем credentials
        domain, username, password = load_and_print_credentials(env_file=env_file, ctx=ctx)

        # 2. Читаем опции
        output.vecho(ctx, "Чтение настроек из листа 'options'...")
        from ..excel.options import OptionsReader
        options_reader = OptionsReader(file)
        site_options = options_reader.read()

        if not site_options:
            output.emit_success(
                ctx,
                {"mode": "update", "file": str(file), "options": {}, "updated": False, "dry_run": dry_run},
                lambda _: console.print("[yellow]WARNING: Нет настроек для обновления[/yellow]\n"),
            )
            return

        if dry_run:
            def _render_dry(payload: dict):
                console.print(f"[green]OK[/green] Найдено настроек: {len(payload['options'])}\n")
                output.human.key_values("Настройки для обновления:", payload["options"])
                console.print("\n[yellow]DRY-RUN режим: изменения не будут применены[/yellow]")
                console.print("[bold green]DRY-RUN завершен![/bold green]\n")

            output.emit_success(
                ctx,
                {"mode": "update", "file": str(file), "options": site_options, "updated": False, "dry_run": True},
                _render_dry,
            )
            return

        # 3. Перезапись настроек живого сайта — операция без отката:
        # прежние значения нигде не сохраняются (гейт после проверки --dry-run).
        output.require_confirmation(
            ctx,
            f"Перезаписать {len(site_options)} настроек сайта {domain} "
            f"значениями из {file.name}? Прежние значения не сохраняются.",
            confirm,
        )

        # 4. Подключаемся к WordPress и отправляем
        output.vecho(ctx, "Отправка настроек в WordPress...")

        wp_config = _make_wp_config(domain, username, password)

        asyncio.run(_update_options(wp_config, site_options))

        def _render_update(payload: dict):
            console.print(f"[green]OK[/green] Найдено настроек: {len(payload['options'])}\n")
            output.human.key_values("Обновлённые настройки:", payload["options"])
            console.print(f"\n[bold green]SUCCESS! Настройки обновлены![/bold green]\n")

        output.emit_success(
            ctx,
            {"mode": "update", "file": str(file), "options": site_options, "updated": True, "dry_run": False},
            _render_update,
        )


async def _update_options(wp_config: WordPressConfig, site_options: dict):
    """Отправка настроек в WordPress."""
    async with WordPressClient(wp_config) as client:
        await client.test_connection(authenticated=True)
        options_endpoint = OptionsEndpoint(client)
        await options_endpoint.update(site_options)


async def _fetch_options(wp_config: WordPressConfig) -> dict:
    """Получение настроек из WordPress."""
    async with WordPressClient(wp_config) as client:
        await client.test_connection(authenticated=True)
        options_endpoint = OptionsEndpoint(client)
        return await options_endpoint.get()


@app.command()
def bulk_delete(
    ctx: typer.Context,
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
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    post_type: str = typer.Option(
        ...,
        "--post-type",
        "-t",
        help="Тип контента для удаления (posts, pages, service, и т.д.)"
    ),
    status: str = typer.Option(
        "any",
        "--status",
        help="Статус записей для удаления (any, publish, draft, etc.)"
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Удалить полностью (минуя корзину)"
    ),
    delete_media: bool = typer.Option(
        False,
        "--delete-media",
        help="Удалять связанные featured media"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Показать что будет удалено, без удаления"
    ),
    confirm: bool = output.ConfirmOption,
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = typer.Option(
        False,
        "--verbose",
        "-v",
        help="Подробные логи"
    ),
):
    """
    Массовое удаление всех записей указанного типа

    ОПАСНО: удаление необратимо. Сначала --dry-run (ничего не удаляет),
    затем — подтверждение: человеку задаётся вопрос, а под --non-interactive
    (и в --json, и когда stdin не терминал) нужен явный --confirm.

    Примеры:
        # Посмотреть что будет удалено, ничего не трогая
        wp-bulk bulk-delete --post-type posts --dry-run

        # Удалить все черновики постов в корзину (спросит подтверждение)
        wp-bulk bulk-delete --post-type posts --status draft

        # Неинтерактивно: подтверждение обязательно флагом
        wp-bulk bulk-delete --post-type service --force --delete-media \
            --non-interactive --confirm

    Exit-коды: 0 — успех, 1 — отменено пользователем/непредвиденная ошибка,
    2 — аргументы или отсутствующий --confirm, 3 — нет credentials,
    5 — недоступен WordPress.
    """
    env_file = resolve_env_file(ctx, env_file)
    # Локальные флаги (`bulk-delete --json`) работают так же, как глобальные
    # (`--json bulk-delete`): команда идёт не через @output.guard, поэтому
    # подмешиваем их сами.
    opts = output.get_options(ctx)
    opts.json = opts.json or json_output
    opts.quiet = opts.quiet or quiet
    opts.non_interactive = opts.non_interactive or non_interactive

    def say(message: str = "") -> None:
        """Человеческий вывод. В --json stdout занят конвертом, значит молчим."""
        if not opts.json and not opts.quiet:
            console.print(message)

    say("\n[bold red]Bulk Delete[/bold red]")
    say("=" * 60 + "\n")

    with handle_command_errors(ctx):
        log_file = setup_command_logging(ctx, "bulk_delete", verbose)

        # Неинтерактивный запуск без --confirm отвергаем сразу, до обращения
        # к сайту: ошибка должна быть про отсутствующий --confirm, а не про сеть.
        if not dry_run and not confirm and not output.is_interactive(ctx):
            output.require_confirmation(
                ctx,
                f"удаление всех записей типа '{post_type}' (статус: {status})",
                confirm,
            )

        # Получаем credentials (приоритет: CLI параметры -> .env файл)
        domain, username, password = load_and_print_credentials(
            env_file=env_file,
            domain=domain,
            username=username,
            password=password,
            ctx=ctx,
        )

        say(f"[yellow]->[/yellow] Тип контента: {post_type}")
        say(f"[yellow]->[/yellow] Статус: {status}")
        say(f"[yellow]->[/yellow] Режим удаления: {'Полное' if force else 'В корзину'}")
        say(f"[yellow]->[/yellow] Удалять медиа: {'Да' if delete_media else 'Нет'}")
        if dry_run:
            say("[yellow]->[/yellow] DRY-RUN режим: изменения не будут применены")
        say()

        # Создаем WordPress клиент
        wp_config = _make_wp_config(domain, username, password)

        # Сначала читаем список — он же и есть превью для --dry-run,
        # и количество записей для вопроса о подтверждении.
        all_posts = asyncio.run(_collect_posts(wp_config, post_type, status))
        say(f"[yellow]->[/yellow] Найдено записей: {len(all_posts)}\n")

        targets = [
            {"id": post.get("id"), "title": wp_rendered(post.get("title", {}), "No title")}
            for post in all_posts
        ]

        if dry_run:
            # Превью никогда не спрашивает и ничего не меняет.
            def _render_dry(payload: Dict):
                console.print(f"[yellow]DRY-RUN: Будет удалено {payload['found']} записей[/yellow]")
                for item in payload["posts"][:10]:
                    console.print(f"  ID={item['id']}: {item['title']}")
                if payload["found"] > 10:
                    console.print(f"  ... и еще {payload['found'] - 10} записей")
                console.print()

            output.emit_success(
                ctx,
                {
                    "dry_run": True,
                    "post_type": post_type,
                    "status": status,
                    "found": len(targets),
                    "deleted": 0,
                    "posts": targets,
                },
                _render_dry,
            )
            return

        if all_posts:
            mode = "полностью (минуя корзину)" if force else "в корзину"
            media_note = " вместе с featured media" if delete_media else ""
            output.require_confirmation(
                ctx,
                f"Удалить {len(all_posts)} записей типа '{post_type}' "
                f"(статус: {status}) {mode}{media_note}?",
                confirm,
            )

        # Запускаем удаление
        stats = asyncio.run(_run_bulk_delete(
            wp_config,
            all_posts,
            post_type,
            force,
            delete_media
        ))

        # Показываем результаты
        def _render_done(payload: Dict):
            errors_color = "red" if payload["errors"] > 0 else "green"
            console.print("\n[bold green]Операция завершена![/bold green]")
            console.print("=" * 60)
            console.print(f"  Найдено записей: [cyan]{payload['found']}[/cyan]")
            console.print(f"  Удалено записей: [cyan]{payload['deleted']}[/cyan]")
            if delete_media:
                console.print(f"  Удалено медиа: [cyan]{payload['media_deleted']}[/cyan]")
            console.print(f"  Ошибок: [{errors_color}]{payload['errors']}[/{errors_color}]")
            console.print(f"\n[dim]Логи: {log_file}[/dim]")

        output.emit_success(
            ctx,
            {
                "dry_run": False,
                "post_type": post_type,
                "status": status,
                "force": force,
                **stats,
            },
            _render_done,
        )


async def _collect_posts(
    wp_config: WordPressConfig,
    post_type: str,
    status: str,
) -> List[Dict]:
    """Прочитать список записей, которые попадут под удаление (без изменений на сайте)."""
    async with WordPressClient(wp_config) as client:
        posts_endpoint = PostsEndpoint(client, post_type)
        logger.info(f"Fetching all {post_type} with status={status}...")
        all_posts = await posts_endpoint.get_all(status=status)
        logger.info(f"Found {len(all_posts)} {post_type}")
        return all_posts


async def _run_bulk_delete(
    wp_config: WordPressConfig,
    all_posts: List[Dict],
    post_type: str,
    force: bool,
    delete_media: bool
) -> Dict[str, int]:
    """Выполнить массовое удаление уже собранных записей."""
    from ..wordpress.media import MediaEndpoint

    stats = {
        "found": len(all_posts),
        "deleted": 0,
        "media_deleted": 0,
        "errors": 0
    }

    async with WordPressClient(wp_config) as client:
        posts_endpoint = PostsEndpoint(client, post_type)
        media_endpoint = MediaEndpoint(client) if delete_media else None

        for post in all_posts:
            try:
                post_id = post["id"]
                title_text = wp_rendered(post.get("title", {}), "No title")

                if delete_media and media_endpoint:
                    featured_media_id = post.get("featured_media")
                    if featured_media_id and featured_media_id > 0:
                        try:
                            await media_endpoint.delete(featured_media_id, force=True)
                            logger.info(f"Deleted featured media ID={featured_media_id} for post ID={post_id}")
                            stats["media_deleted"] += 1
                        except Exception as e:
                            logger.warning(f"Failed to delete featured media ID={featured_media_id}: {e}")

                await posts_endpoint.delete(post_id, force=force)
                logger.info(f"Deleted {post_type} ID={post_id}: {title_text}")
                stats["deleted"] += 1

            except Exception as e:
                logger.error(f"Failed to delete post ID={post.get('id')}: {e}")
                stats["errors"] += 1

        logger.info(f"Bulk delete completed: deleted={stats['deleted']}, errors={stats['errors']}")
        return stats


@app.command()
def counter(
    ctx: typer.Context,
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
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True
    ),
    post_type: str = typer.Option(
        "posts",
        "--type",
        "-t",
        help="Тип контента (posts, pages, или custom post type)"
    ),
    status: str = typer.Option(
        "publish",
        "--status",
        "-s",
        help="Статус записей (publish, draft, any)"
    ),
    output: Path = typer.Option(
        Path("counter_output.csv"),
        "--output",
        "-o",
        help="Путь к выходному CSV файлу"
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Максимум записей для обработки"
    ),
    concurrency: int = typer.Option(
        5,
        "--concurrency",
        "-c",
        help="Количество параллельных запросов"
    )
):
    """
    Подсчет количества слов и символов в контенте записей WordPress.

    Выводит результаты в CSV файл с полями:
    id, slug, link, title, words, chars

    Пример:
        wp-bulk counter --domain https://example.com --type posts --output stats.csv
    """
    env_file = resolve_env_file(ctx, env_file)
    output = resolve_output(ctx, output, "counter_output.csv")

    console.print(f"\n[bold blue]WordPress Bulk Editor - Counter[/bold blue]\n")

    setup_command_logging(ctx, "counter")

    with handle_command_errors(ctx):
        # 1. Загружаем credentials
        domain, username, password = load_and_print_credentials(
            env_file=env_file,
            domain=domain,
            username=username,
            password=password
        )

        # 2. Запускаем асинхронный подсчет
        asyncio.run(_count_content_stats(
            domain=domain,
            username=username,
            password=password,
            post_type=post_type,
            status=status,
            output_file=output,
            limit=limit,
            concurrency=concurrency
        ))


async def _count_content_stats(
    domain: str,
    username: str,
    password: str,
    post_type: str,
    status: str,
    output_file: Path,
    limit: Optional[int],
    concurrency: int
):
    """Асинхронный подсчет статистики контента."""
    import csv
    import html
    from bs4 import BeautifulSoup

    wp_config = _make_wp_config(domain, username, password)

    results = []

    async with WordPressClient(wp_config) as client:
        await client.test_connection()
        console.print("[green]OK[/green] Подключение к WordPress установлено\n")

        posts_endpoint = PostsEndpoint(client, post_type)

        console.print(f"[yellow]Получение записей типа '{post_type}'...[/yellow]")

        params = {}
        if status != "any":
            params["status"] = status

        all_posts = []
        page = 1
        per_page = 100

        while True:
            posts = await posts_endpoint.list(per_page=per_page, page=page, **params)

            if not posts:
                break

            all_posts.extend(posts)
            console.print(f"  Загружено: {len(all_posts)} записей...")

            if len(posts) < per_page:
                break

            if limit and len(all_posts) >= limit:
                all_posts = all_posts[:limit]
                break

            page += 1

        console.print(f"[green]OK[/green] Всего записей: {len(all_posts)}\n")

        if not all_posts:
            console.print("[yellow]Нет записей для обработки[/yellow]")
            return

        console.print("[yellow]Подсчет статистики...[/yellow]")

        def count_words_and_chars(html_content: str) -> tuple[int, int]:
            """Подсчет слов и символов в HTML контенте."""
            if not html_content:
                return 0, 0

            soup = BeautifulSoup(html_content, "html.parser")
            text = soup.get_text(separator=" ")
            text = html.unescape(text)
            text = " ".join(text.split())

            chars = len(text)
            words = len(text.split()) if text else 0

            return words, chars

        for post in all_posts:
            content_html = wp_rendered(post.get("content", {}))
            title_text = wp_rendered(post.get("title", {}))

            words, chars = count_words_and_chars(content_html)

            results.append({
                "id": post.get("id", ""),
                "slug": post.get("slug", ""),
                "link": post.get("link", ""),
                "title": html.unescape(BeautifulSoup(title_text, "html.parser").get_text()),
                "words": words,
                "chars": chars
            })

        console.print(f"[green]OK[/green] Обработано: {len(results)} записей\n")

    # Записываем в CSV
    console.print(f"[yellow]Запись в CSV: {output_file}[/yellow]")

    with open(output_file, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["id", "slug", "link", "title", "words", "chars"],
            delimiter=";"
        )
        writer.writeheader()
        writer.writerows(results)

    console.print(f"[green]OK[/green] Файл сохранен: {output_file}\n")

    # Выводим статистику
    total_words = sum(r["words"] for r in results)
    total_chars = sum(r["chars"] for r in results)
    avg_words = total_words / len(results) if results else 0
    avg_chars = total_chars / len(results) if results else 0

    console.print("[bold]Статистика:[/bold]")
    console.print(f"  Всего записей: {len(results)}")
    console.print(f"  Всего слов: {total_words:,}")
    console.print(f"  Всего символов: {total_chars:,}")
    console.print(f"  Среднее слов на запись: {avg_words:.1f}")
    console.print(f"  Среднее символов на запись: {avg_chars:.1f}")
    console.print()


@app.command()
def llm(
    ctx: typer.Context,
    column: str = typer.Option(
        ...,
        "--column",
        "-c",
        help="Колонка для генерации контента"
    ),
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к Excel файлу (авто-определяется из --project)",
    ),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Список листов через запятую (posts,pages)"
    ),
    model: Optional[str] = typer.Option(
        None,
        "--model",
        "-m",
        help=(
            "Модели через запятую. Текст: цепочка llmkit ('провайдер/модель'), "
            "по умолчанию LLM_MODELS из .env. Картинки (--column img_path): "
            "модели endpoint'а, по умолчанию LLM_IMG_MODELS/LLM_MODELS"
        )
    ),
    level: Optional[str] = typer.Option(
        None,
        "--level",
        help=(
            "Уровень llmkit (cheap/judge/strong — что настроено в его конфиге). "
            "Только для текста, по умолчанию LLM_LEVEL из .env. Если не задан "
            "ни --level, ни --model — llmkit берёт свой уровень по умолчанию"
        )
    ),
    prompt: Optional[str] = typer.Option(
        None,
        "--prompt",
        "-p",
        help="Промпт для генерации (используйте [column] для подстановки значений)"
    ),
    endpoint: Optional[str] = typer.Option(
        None,
        "--endpoint",
        help="LLM API endpoint — ТОЛЬКО для картинок (--column img_path), по умолчанию .env: LLM_ENDPOINT"
    ),
    token: Optional[str] = typer.Option(
        None,
        "--token",
        help="LLM API token — ТОЛЬКО для картинок (--column img_path), по умолчанию .env: LLM_TOKEN"
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Перезаписывать существующие значения в колонке"
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        "-d",
        help="Режим тестирования без записи в Excel"
    ),
    strip_tags: bool = typer.Option(
        False,
        "--strip-tags",
        help="Удалить HTML теги из результата"
    ),
    md_to_html: bool = typer.Option(
        False,
        "--md-to-html",
        help="Конвертировать Markdown в HTML"
    ),
    html_to_md: bool = typer.Option(
        False,
        "--html-to-md",
        help="Конвертировать HTML в Markdown"
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
    )
):
    """
    Массовая генерация контента через LLM.

    Обрабатывает строки где action=update и генерирует контент в указанную колонку.
    Поддерживает подстановку значений из других колонок через [column_name].

    ТЕКСТ идёт через общую библиотеку llmkit: цепочки моделей с фолбэком, ретраи,
    кэш и учёт стоимости. Ключи провайдеров llmkit берёт из своего конфига
    (~/Library/Application Support/llmkit/config.toml) или связки ключей ОС —
    НЕ из .env проекта. Нужен свой OpenAI-совместимый сервер для текста — заведите
    его там провайдером kind = "openai_compatible"; флаги --endpoint/--token
    на текст больше не влияют.

    КАРТИНКИ (--column img_path) llmkit не умеет — они по-прежнему идут своим
    вызовом, и только для них нужны --endpoint/--token (LLM_ENDPOINT/LLM_TOKEN)
    и модели LLM_IMG_MODELS.

    Примеры:
        # Генерация excerpt уровнем llmkit
        llm --column excerpt --level cheap --prompt "Напиши короткий lead абзац к услуге [title]"

        # Явная цепочка моделей с fallback (формат llmkit: провайдер/модель)
        llm --column description --prompt "Опиши [title]" \
            --model "ollama/gpt-oss:120b,openrouter/openai/gpt-4o-mini"

        # С конвертацией Markdown в HTML, уровень llmkit по умолчанию
        llm --column content --prompt "Напиши статью про [title]" --md-to-html

        # Картинка: нужен endpoint + token
        llm --column img_path --model "some-image-model" \
            --endpoint https://api.example/v1/chat/completions --token ...
    """
    file = require_excel_file(ctx, file)
    env_file = resolve_env_file(ctx, env_file)

    console.print(f"\n[bold blue]WordPress Bulk Editor - LLM Generator[/bold blue]\n")

    log_file = setup_command_logging(ctx, "llm", verbose)

    with handle_command_errors(ctx):
        # 1. Загружаем credentials для LLM
        import os
        from dotenv import load_dotenv

        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

        is_img = (column == "img_path")

        # endpoint/token нужны только картинкам: текст ходит через llmkit,
        # который берёт ключи из своего конфига, а не из .env проекта.
        final_endpoint = endpoint or os.getenv("LLM_ENDPOINT")
        final_token = token or os.getenv("LLM_TOKEN")

        # Для img_path: приоритет LLM_IMG_PROMPT/LLM_IMG_MODELS, потом LLM_PROMPT/LLM_MODELS
        if is_img:
            final_prompt = prompt or os.getenv("LLM_IMG_PROMPT") or os.getenv("LLM_PROMPT")
            final_models = model or os.getenv("LLM_IMG_MODELS") or os.getenv("LLM_MODELS")
            final_level = None
        else:
            final_prompt = prompt or os.getenv("LLM_PROMPT")
            final_models = model or os.getenv("LLM_MODELS")
            final_level = level or os.getenv("LLM_LEVEL")

        if is_img and not final_endpoint:
            console.print("[red]ERROR: LLM_ENDPOINT не указан (используйте --endpoint или .env)[/red]\n")
            raise typer.Exit(code=1)

        if is_img and not final_token:
            console.print("[red]ERROR: LLM_TOKEN не указан (используйте --token или .env)[/red]\n")
            raise typer.Exit(code=1)

        if not final_prompt:
            env_hint = "LLM_IMG_PROMPT или LLM_PROMPT" if is_img else "LLM_PROMPT"
            console.print(f"[red]ERROR: Промпт не указан (используйте --prompt или .env: {env_hint})[/red]\n")
            raise typer.Exit(code=1)

        # Картинкам список моделей обязателен; тексту — нет: без --model/--level
        # llmkit возьмёт свой уровень по умолчанию.
        if is_img and not final_models:
            console.print(
                "[red]ERROR: Модели не указаны "
                "(используйте --model или .env: LLM_IMG_MODELS или LLM_MODELS)[/red]\n"
            )
            raise typer.Exit(code=1)

        # 2. Парсим список моделей
        models = [m.strip() for m in (final_models or "").split(",") if m.strip()]
        logger.info(f"Модели для генерации: {models or 'по конфигу llmkit'}")

        console.print(f"[green]OK[/green] Колонка: {column}")
        if is_img:
            console.print(f"[green]OK[/green] LLM Endpoint (картинки): {final_endpoint}")
            console.print(f"[green]OK[/green] Модели: {', '.join(models)}")
        else:
            console.print("[green]OK[/green] Текст: через llmkit (ключи — из конфига llmkit)")
            if models:
                console.print(f"[green]OK[/green] Цепочка моделей: {', '.join(models)}")
            elif final_level:
                console.print(f"[green]OK[/green] Уровень: {final_level}")
            else:
                console.print("[green]OK[/green] Модели: уровень по умолчанию llmkit")
        console.print(f"[green]OK[/green] Промпт: {final_prompt[:100]}...")
        if dry_run:
            console.print(f"[yellow]DRY-RUN режим: изменения не будут сохранены[/yellow]")
        console.print()

        # 3. Определяем папки для файлов (content_path / img_path)
        proj = get_project(ctx)
        content_folder = proj.content_folder
        media_folder = proj.media_folder

        # 3. Запускаем генерацию
        asyncio.run(_run_llm_generation(
            file=file,
            sheets=sheets,
            column=column,
            models=models,
            level=final_level,
            prompt_template=final_prompt,
            endpoint=final_endpoint,
            token=final_token,
            project_name=proj.name,
            overwrite=overwrite,
            dry_run=dry_run,
            strip_tags=strip_tags,
            md_to_html=md_to_html,
            html_to_md=html_to_md,
            log_file=log_file,
            content_folder=content_folder,
            media_folder=media_folder
        ))


async def _run_llm_generation(
    file: Path,
    sheets: Optional[str],
    column: str,
    models: List[str],
    prompt_template: str,
    endpoint: Optional[str],
    token: Optional[str],
    overwrite: bool,
    dry_run: bool,
    strip_tags: bool,
    md_to_html: bool,
    html_to_md: bool,
    log_file: Path,
    content_folder: Path = Path("data/content"),
    media_folder: Path = Path("data/media"),
    level: Optional[str] = None,
    project_name: Optional[str] = None,
):
    """Основная логика генерации через LLM."""
    from ..utils.llm_client import LLMClient
    from ..excel.reader import ExcelReader
    from ..excel.writer import ExcelWriter

    # Метки уезжают в журнал llmkit: `llm cost --by tool,project`
    tags = {"tool": "wp-bulk-editor"}
    if project_name:
        tags["project"] = project_name

    llm_client = LLMClient(endpoint=endpoint, token=token, tags=tags)

    # Определяем режим работы по имени колонки
    is_content_path = (column == "content_path")
    is_img_path = (column == "img_path")

    if is_content_path:
        content_folder.mkdir(parents=True, exist_ok=True)
        console.print(f"[yellow]Режим:[/yellow] content_path (текст -> файл -> путь в Excel)")
        console.print(f"[yellow]Папка контента:[/yellow] {content_folder}")
    elif is_img_path:
        media_folder.mkdir(parents=True, exist_ok=True)
        console.print(f"[yellow]Режим:[/yellow] img_path (изображение -> файл -> путь в Excel)")
        console.print(f"[yellow]Папка медиа:[/yellow] {media_folder}")
    console.print()

    console.print("[yellow]Чтение Excel файла...[/yellow]")

    reader = ExcelReader(file)
    available_sheets = reader.get_sheet_names()

    if sheets:
        target_sheets = [s.strip() for s in sheets.split(",")]
        missing_sheets = [s for s in target_sheets if s not in available_sheets]
        if missing_sheets:
            console.print(f"[red]ERROR: Листы не найдены: {', '.join(missing_sheets)}[/red]\n")
            raise typer.Exit(code=1)
    else:
        target_sheets = available_sheets

    console.print(f"[green]OK[/green] Найдено листов для обработки: {len(target_sheets)}")
    console.print(f"  Листы: {', '.join(target_sheets)}\n")

    writer = ExcelWriter(file)
    writer.load()

    total_processed = 0
    total_skipped = 0
    total_errors = 0

    for sheet_name in target_sheets:
        console.print(f"[bold cyan]Обработка листа: {sheet_name}[/bold cyan]")

        df = pd.read_excel(file, sheet_name=sheet_name)

        if "action" not in df.columns:
            console.print(f"  [yellow]Пропущен (нет колонки 'action')[/yellow]\n")
            continue

        if column not in df.columns:
            console.print(f"  [yellow]Пропущен (нет колонки '{column}')[/yellow]\n")
            continue

        update_rows = df[df["action"].map(normalize_action) == "update"]

        if len(update_rows) == 0:
            console.print(f"  [yellow]Нет строк с action=update[/yellow]\n")
            continue

        console.print(f"  Найдено строк с action=update: {len(update_rows)}")

        for idx, row in update_rows.iterrows():
            current_value = row.get(column, "")
            if current_value and str(current_value).strip() and not overwrite:
                console.print(f"  [{idx}] Пропущено (значение уже есть, используйте --overwrite)")
                total_skipped += 1
                continue

            row_dict = row.to_dict()
            filled_prompt = llm_client.replace_placeholders(prompt_template, row_dict)

            # Получаем ID записи для именования файлов
            row_id = row_dict.get("id", "")
            if not row_id or (isinstance(row_id, float) and str(row_id) == 'nan'):
                row_id = f"row_{idx}"
            else:
                row_id = str(int(row_id)) if isinstance(row_id, float) else str(row_id)

            logger.info(f"[{sheet_name}:{idx}] Промпт: {filled_prompt[:100]}...")

            try:
                # === Режим img_path: генерация изображения ===
                if is_img_path:
                    console.print(f"  [{idx}] Генерация изображения (ID={row_id})...")
                    image_bytes, ext = await llm_client.generate_image(
                        prompt=filled_prompt,
                        models=models,
                        max_retries=3
                    )

                    filename = f"{row_id}.{ext}"
                    filepath = media_folder / filename
                    if not dry_run:
                        filepath.write_bytes(image_bytes)
                        logger.info(f"[{sheet_name}:{idx}] Изображение сохранено: {filepath} ({len(image_bytes)} bytes)")

                    # Записываем относительный путь в Excel
                    relative_path = f"{media_folder.name}/{filename}"
                    df.at[idx, column] = relative_path

                    if 'action' in df.columns:
                        df.at[idx, 'action'] = 'skip'

                    console.print(f"  [{idx}] OK Изображение ({len(image_bytes)} bytes) -> {relative_path}")
                    total_processed += 1

                # === Режим content_path: генерация текста в файл ===
                elif is_content_path:
                    console.print(f"  [{idx}] Генерация контента (ID={row_id})...")
                    generated_text = await llm_client.generate(
                        prompt=filled_prompt,
                        models=models,
                        level=level,
                    )

                    # Применяем постобработку к тексту
                    if strip_tags:
                        generated_text = llm_client.strip_html_tags(generated_text)
                        logger.debug(f"[{sheet_name}:{idx}] Применен strip_tags")

                    if md_to_html:
                        generated_text = llm_client.markdown_to_html(generated_text)
                        logger.debug(f"[{sheet_name}:{idx}] Применен md_to_html")

                    if html_to_md:
                        generated_text = llm_client.html_to_markdown(generated_text)
                        logger.debug(f"[{sheet_name}:{idx}] Применен html_to_md")

                    # Сохраняем текст в файл
                    filename = f"{row_id}.html"
                    filepath = content_folder / filename
                    if not dry_run:
                        filepath.write_text(generated_text, encoding="utf-8")
                        logger.info(f"[{sheet_name}:{idx}] Контент сохранен: {filepath} ({len(generated_text)} символов)")

                    # Записываем относительный путь в Excel
                    relative_path = f"{content_folder.name}/{filename}"
                    df.at[idx, column] = relative_path

                    if 'action' in df.columns:
                        df.at[idx, 'action'] = 'skip'

                    console.print(f"  [{idx}] OK Контент ({len(generated_text)} символов) -> {relative_path}")
                    total_processed += 1

                # === Обычный режим: текст в колонку ===
                else:
                    console.print(f"  [{idx}] Генерация...")
                    generated_text = await llm_client.generate(
                        prompt=filled_prompt,
                        models=models,
                        level=level,
                    )

                    if strip_tags:
                        generated_text = llm_client.strip_html_tags(generated_text)
                        logger.debug(f"[{sheet_name}:{idx}] Применен strip_tags")

                    if md_to_html:
                        generated_text = llm_client.markdown_to_html(generated_text)
                        logger.debug(f"[{sheet_name}:{idx}] Применен md_to_html")

                    if html_to_md:
                        generated_text = llm_client.html_to_markdown(generated_text)
                        logger.debug(f"[{sheet_name}:{idx}] Применен html_to_md")

                    df.at[idx, column] = generated_text

                    if 'action' in df.columns:
                        df.at[idx, 'action'] = 'skip'
                        logger.debug(f"[{sheet_name}:{idx}] Action изменен на 'skip'")

                    console.print(f"  [{idx}] OK Сгенерировано ({len(generated_text)} символов)")
                    logger.info(f"[{sheet_name}:{idx}] Успешно: {generated_text[:100]}...")
                    total_processed += 1

            except Exception as e:
                error_msg = "error"
                df.at[idx, column] = error_msg
                console.print(f"  [{idx}] Ошибка: {str(e)[:80]}")
                logger.error(f"[{sheet_name}:{idx}] Полная ошибка: {e}", exc_info=True)
                total_errors += 1

        if not dry_run:
            writer.replace_rows(sheet_name, df.to_dict('records'))
            logger.info(f"Лист '{sheet_name}' обновлен в памяти")

        console.print()

    if not dry_run:
        console.print("[yellow]Сохранение изменений в Excel...[/yellow]")
        writer.save()
        console.print(f"[green]OK[/green] Файл сохранен: {file}\n")
    else:
        console.print("[yellow]DRY-RUN: изменения НЕ сохранены[/yellow]\n")

    console.print("[bold]Итоги:[/bold]")
    console.print(f"  Обработано: {total_processed}")
    console.print(f"  Пропущено: {total_skipped}")
    console.print(f"  Ошибок: {total_errors}")
    console.print()

    if total_errors > 0:
        console.print(f"[yellow]Обнаружены ошибки. Проверьте лог: {log_file}[/yellow]\n")


def _resolve_template(template_name: Optional[str]) -> Path:
    """Резолвинг пути к шаблону: None → default.xlsx, имя → templates/<имя>, путь → как есть."""
    repo_root = find_repo_root()
    templates_dir = repo_root / "templates"

    if template_name is None:
        return templates_dir / "default.xlsx"

    template_path = Path(template_name)
    if template_path.is_absolute() or template_path.exists():
        return template_path

    return templates_dir / template_name


@app.command()
def reshape(
    ctx: typer.Context,
    source: Optional[Path] = typer.Option(
        None, "--source", "--src", "-s",
        help="Excel-источник (по умолчанию fetch.xlsx)",
    ),
    template: Optional[str] = typer.Option(
        None, "--template", "--tpl", "-t",
        help="Шаблон: имя в templates/ или полный путь (по умолчанию default.xlsx)",
    ),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o",
        help="Результирующий файл (по умолчанию export.xlsx)",
    ),
    extra_columns: Optional[str] = typer.Option(
        None, "--extra", "-e",
        help="Доп. колонки из источника: список через запятую или 'none' чтобы не добавлять",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v",
        help="Подробные логи",
    ),
):
    """
    Перенос данных из fetch-файла в структуру шаблона.

    Берёт Excel-источник (результат fetch), Excel-шаблон (из templates/),
    и создаёт результирующий Excel, где данные из источника перенесены
    в структуру шаблона (листы, колонки, порядок).

    По умолчанию колонки из источника, которых нет в шаблоне, дописываются
    в конец. --extra none отключает это, --extra "col1,col2" добавляет только указанные.

    Примеры:
        wp-bulk reshape
        wp-bulk reshape --extra none
        wp-bulk reshape --extra "acf_price,acf_address"
        wp-bulk reshape --src custom.xlsx --tpl my_template.xlsx -o result.xlsx
    """
    from openpyxl import Workbook

    proj = get_project(ctx)

    # Резолвинг source
    if source is None:
        if proj.active:
            source = proj.root / "fetch.xlsx"
        else:
            source = Path("fetch.xlsx")

    # Резолвинг template
    template_path = _resolve_template(template)

    # Резолвинг output
    if output is None:
        if proj.active:
            output = proj.root / "export.xlsx"
        else:
            output = Path("export.xlsx")

    log_file = setup_command_logging(ctx, "reshape", verbose)

    console.print(f"\n[bold blue]WordPress Bulk Editor - Reshape[/bold blue]\n")
    console.print(f"  Source:   [cyan]{source}[/cyan]")
    console.print(f"  Template: [cyan]{template_path}[/cyan]")
    console.print(f"  Output:   [cyan]{output}[/cyan]\n")

    with handle_command_errors(ctx):
        if not source.exists():
            console.print(f"[red]ERROR: Файл-источник не найден: {source}[/red]\n")
            raise typer.Exit(code=1)

        if not template_path.exists():
            console.print(f"[red]ERROR: Шаблон не найден: {template_path}[/red]\n")
            raise typer.Exit(code=1)

        console.print("[yellow]Обработка листов:[/yellow]")

        # Парсим --extra
        if extra_columns is not None and extra_columns.strip().lower() == "none":
            extra_whitelist = []  # не добавлять лишние
        elif extra_columns is not None:
            extra_whitelist = [c.strip() for c in extra_columns.split(",") if c.strip()]
        else:
            extra_whitelist = None  # добавлять все

        with ExcelReader(source) as src, ExcelReader(template_path) as tpl:
            wb = Workbook()
            # Удаляем дефолтный лист
            wb.remove(wb.active)

            source_sheets = src.get_sheet_names()

            for tpl_sheet in tpl.get_sheet_names():
                tpl_columns = tpl.get_content_columns(tpl_sheet)

                if tpl_sheet in source_sheets:
                    src_df = src.read_sheet(tpl_sheet)
                    src_columns = src_df.columns.tolist()

                    # Колонки шаблона (в порядке шаблона)
                    all_columns = list(tpl_columns)

                    # Доп. колонки из source, которых нет в шаблоне
                    tpl_set = set(tpl_columns)
                    if extra_whitelist is None:
                        # Все extra-колонки
                        extra = [c for c in src_columns if c not in tpl_set]
                        if extra:
                            all_columns.append(None)  # пустой столбец-разрыв перед extra
                    elif extra_whitelist:
                        # Только указанные, в порядке whitelist, если есть в source
                        src_set = set(src_columns)
                        extra = [c for c in extra_whitelist if c in src_set and c not in tpl_set]
                    else:
                        # none — без extra
                        extra = []
                    all_columns.extend(extra)

                    # Создаём лист и записываем
                    ws = wb.create_sheet(tpl_sheet)
                    for col_idx, col_name in enumerate(all_columns, 1):
                        ws.cell(row=1, column=col_idx, value=col_name)

                    for row_idx, (_, row) in enumerate(src_df.iterrows(), 2):
                        for col_idx, col_name in enumerate(all_columns, 1):
                            value = row.get(col_name, "")
                            if pd.notna(value) and value != "":
                                ws.cell(row=row_idx, column=col_idx, value=value)

                    extra_info = f" (+{len(extra)} extra)" if extra else ""
                    console.print(
                        f"  {tpl_sheet}:".ljust(20)
                        + f"{len(src_df)} строк, "
                        + f"{len(tpl_columns)} → {len(all_columns)} колонок{extra_info}"
                    )
                else:
                    # Листа нет в source — пустой лист с заголовками шаблона
                    ws = wb.create_sheet(tpl_sheet)
                    for col_idx, col_name in enumerate(tpl_columns, 1):
                        ws.cell(row=1, column=col_idx, value=col_name)

                    console.print(
                        f"  {tpl_sheet}:".ljust(20)
                        + "0 строк (нет в источнике)"
                    )

            wb.save(output)

        console.print(f"\n[bold green]SUCCESS! Файл создан: {output}[/bold green]\n")


@app.command()
def taxonomies(
    ctx: typer.Context,
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env (по умолчанию projects/<name>/.env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    include_system: bool = typer.Option(
        False,
        "--include-system",
        help="Включать служебные таксономии (nav_menu, link_category, post_format)",
    ),
):
    """
    Вывести список таксономий сайта в формате для копирования в .env проекта.

    Пример:
        wp-bulk --project mysite taxonomies
        → WP_TAXONOMIES=product_cat,brand,regions
    """
    env_file = resolve_env_file(ctx, env_file)
    setup_command_logging(ctx, "taxonomies")

    with handle_command_errors(ctx):
        domain, username, password = load_and_print_credentials(env_file=env_file)
        wp_config = _make_wp_config(domain, username, password)

        slugs = asyncio.run(_discover_taxonomy_slugs(wp_config, include_system))

        if not slugs:
            console.print("[yellow]Таксономии не обнаружены.[/yellow]\n")
            return

        line = f"WP_TAXONOMIES={','.join(slugs)}"
        console.print(f"\n[dim]# Скопируй в .env проекта:[/dim]")
        console.print(line)
        console.print()


async def _discover_taxonomy_slugs(
    wp_config: WordPressConfig, include_system: bool
) -> List[str]:
    """Запросить таксономии через WP REST и вернуть отфильтрованный список slug-ов."""
    from ..wordpress.discovery import WordPressDiscovery
    from ..config import Settings

    settings = Settings()
    async with WordPressClient(wp_config) as client:
        await client.test_connection()
        skip_patterns = [] if include_system else settings.get_skip_taxonomy_patterns()
        discovery = WordPressDiscovery(client, skip_taxonomy_patterns=skip_patterns)
        return await discovery.discover_taxonomies()
