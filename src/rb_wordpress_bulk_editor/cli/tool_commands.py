"""
Утилитарные команды: validate, options, bulk-delete, taxonomies
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import asyncio

from loguru import logger

from . import app, console
from .common import (
    confirm_tab_rebuild,
    load_and_print_credentials,
    refuse_project_registry,
    setup_command_logging,
)
from .helpers import (
    get_project,
    require_data_file,
    resolve_env_file,
    resolve_excel_file,
)
from .. import output
from ..config import WordPressConfig
from ..exceptions import AuthenticationError, ValidationError
from ..excel.reader import ExcelReader
from ..records.excel import ExcelRecordSource
from ..records.json_validation import check_tab
from ..records.preflight import FieldRules, check_sources, raise_if_errors
from ..records.base import is_blank
from ..records.selection import dataset_names, is_json_source, require_json_tabs
from ..records.store import open_store
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
        None, "--file", "-f", help="Файл данных .xlsx или .json (авто-определяется из --project)"
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
        resolve_path=True,
    ),
    offline: bool = typer.Option(
        False,
        "--offline",
        help="Не обращаться к сайту: таксономии — по соглашению, WP_TAXONOMIES и самому файлу",
    ),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Проверка файла данных перед отправкой — без синхронизации.

    Та же проверка автоматически идёт перед sync/async (отключается
    --skip-checks). Сообщаются все проблемы сразу:

    - имена полей: всё, кроме acf.* / acf_* / meta.*, — известное поле
      WordPress, SEO-поле или своё поле из WP_EXTRA_FIELDS; к опечатке
      подсказывается похожее имя (seo.descripton → seo.description);
    - таксономии и типы — по сайту (только чтение): колонка-таксономия
      есть на сайте и привязана к типу записи, type / taxonomy записей
      существуют; к опечатке — похожее имя с сайта;
    - значения записей add/update/delete: id у update/delete, status,
      дубли slug, разделитель терминов "; ", да/нет в seo.robots.*,
      существование файлов в значениях-путях (acf.more = "content/1.html").

    Записи skip / без action на сайт не уходят: их проблемы — предупреждения.

    Сайт используется, если есть доступы. Без них или с --offline
    таксономии узнаются по соглашению (cat/tax/term/tag через _ или -),
    WP_TAXONOMIES и самому файлу (записи-термины, листы таксономий);
    в ответе site_checked=false.

    Exit-коды: 0 — файл валиден, 2 — ошибки в файле/аргументах,
    3 — сайт отверг доступы, 5 — сайт недоступен (повторите с --offline),
    1 — непредвиденная ошибка.

    Example: {cli} validate --file site.json --sheets site.ru --json
    """
    env_file = resolve_env_file(ctx, env_file)
    file = require_data_file(ctx, file)
    is_json = is_json_source(file)
    # Выбор табов — вопрос к файлу: проверяется до обращения к сайту
    tabs = require_json_tabs(file, sheets) if is_json else None

    options = output.get_options(ctx)
    if not options.json and not options.quiet:
        console.print(f"\n[bold blue]Валидация файла[/bold blue]: {file}\n")

    setup_command_logging(ctx, "validate")
    domain, site = _site_for_validate(ctx, env_file, offline)
    rules = FieldRules.from_settings(site=site)

    by_type = None
    if is_json:
        checks = [check_tab(file, tab, rules) for tab in tabs or []]  # is_json ⇒ tabs задан
    else:
        output.vecho(ctx, "Проверка листов...")
        with ExcelReader(file, known_taxonomies=rules.taxonomies()) as reader:
            by_type = reader.get_sheets_by_type()
            names = dataset_names(sheets) if sheets is not None else by_type["content"] + by_type["taxonomy"]
            checks = check_sources(
                [ExcelRecordSource(reader, name) for name in names],
                rules,
                base_dir=file.parent,
            )
    raise_if_errors(file.name, checks)

    def _render(payload: dict):
        if not payload["site_checked"]:
            console.print("[yellow]Без сайта: таксономии и типы записей с сайтом не сверялись[/yellow]")
        _render_checks(payload["tabs"])

    result = {
        "file": str(file),
        "format": "json" if is_json else "xlsx",
        "valid": True,
        "domain": domain,
        "site_checked": site is not None,
        "tabs": [c.to_dict() for c in checks],
    }
    if by_type is not None:
        result["content_sheets"] = list(by_type["content"])
        result["taxonomy_sheets"] = list(by_type["taxonomy"])
    output.emit_success(ctx, result, _render)


def _site_for_validate(ctx: typer.Context, env_file: Optional[Path], offline: bool):
    """
    Схема сайта для validate: (домен, схема) или (None, None) без сайта.

    Без доступов — проверка без сайта (это нормальный режим, не ошибка).
    Доступы есть, а сайт не ответил — ошибка: тихо проверить хуже, чем
    обещано, нельзя; для этого есть явный --offline.
    """
    if offline:
        return None, None
    try:
        domain, username, password = load_and_print_credentials(env_file=env_file, ctx=ctx)
    except AuthenticationError:
        logger.warning("Нет доступов к сайту — проверка без сайта (таксономии по соглашению и файлу)")
        return None, None
    wp_config = _make_wp_config(domain, username, password)
    return domain, asyncio.run(_load_site_schema(wp_config))


async def _load_site_schema(wp_config: WordPressConfig):
    from ..wordpress.site_schema import load_site_schema

    async with WordPressClient(wp_config) as client:
        await client.test_connection()
        return await load_site_schema(client)


def _render_checks(tabs: List[dict]) -> None:
    for check in tabs:
        console.print(f"[green]OK[/green] '{check['tab']}': {check['records']} записей")
        for warning in check["warnings"]:
            console.print(f"  [yellow]WARN[/yellow] {warning}")
    console.print("\n[bold green]SUCCESS! File is valid![/bold green]\n")


# Таб настроек сайта (плагин siteoptions): записи {key, value}
OPTIONS_TAB = "options"


@app.command()
@output.guard
def options(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Файл данных .json или .xlsx (таб/лист options)",
        exists=False,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    fetch: bool = typer.Option(False, "--fetch", help="Выгрузить настройки с сайта в файл (вместо отправки)"),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Показать что будет отправлено, без отправки"),
    confirm: bool = output.ConfirmOption,
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Настройки сайта WordPress (плагин siteoptions) <-> таб options файла данных.

    Формат выбирается по расширению --file: JSON (таб "options") или Excel
    (лист options). Запись таба — {key, value}; прочие поля (label и т.п.)
    игнорируются, записи с пустым key или value не отправляются.

    Без --fetch: отправка настроек из таба на сайт. Без --file берётся файл
    данных проекта (xlsx или json).
    С --fetch: выгрузка настроек с сайта в таб options; таб пересобирается
    целиком, остальные табы не трогаются. Без --file — xlsx проекта или
    ./options.xlsx. В реестр проекта <проект>/<проект>.json не пишет.

    Запись настроек на сайт и пересборку непустого таба откатить нельзя:
    сначала доступен --dry-run, затем спрашивается подтверждение, а под
    --non-interactive требуется явный --confirm.

    Exit-коды: 0 — успех, 2 — аргументы/файл или отсутствующий --confirm,
    3 — нет credentials,
    5 — недоступен WordPress, 1 — непредвиденная ошибка.

    Example: {cli} options --fetch --file wp-fetch/site.ru.json --json
    """
    env_file = resolve_env_file(ctx, env_file)
    proj = get_project(ctx)
    project_name = proj.name if proj.active else None

    opts = output.get_options(ctx)
    human_mode = not opts.json and not opts.quiet

    if human_mode:
        console.print("\n[bold blue]WordPress Bulk Editor - Options[/bold blue]\n")

    command_name = "options_fetch" if fetch else "options"
    setup_command_logging(ctx, command_name)
    logger.info(f"Режим: {'FETCH (выгрузка)' if fetch else 'UPDATE (загрузка)'}")

    if fetch:
        file = file or resolve_excel_file(ctx, None) or Path.cwd() / "options.xlsx"
        refuse_project_registry(proj, file)
        store = open_store(file, project=project_name)
        confirm_tab_rebuild(ctx, store, OPTIONS_TAB, confirm)

        domain, username, password = load_and_print_credentials(env_file=env_file, ctx=ctx)
        output.vecho(ctx, "Получение настроек из WordPress...")
        site_options = asyncio.run(_fetch_options(_make_wp_config(domain, username, password)))

        base = {"mode": "fetch", "file": str(file), "format": store.format, "tab": OPTIONS_TAB}
        if not site_options:
            output.emit_success(
                ctx,
                {**base, "records": 0, "options": {}, "written": False},
                lambda _: console.print("[yellow]WARNING: Нет настроек для выгрузки[/yellow]\n"),
            )
            return

        store.write_tab(OPTIONS_TAB, [{"key": key, "value": value} for key, value in site_options.items()])

        def _render_fetch(payload: dict):
            console.print(f"[green]OK[/green] Получено настроек: {len(payload['options'])}\n")
            output.human.key_values("Полученные настройки:", payload["options"])
            console.print(f"\n[bold green]SUCCESS! Настройки выгружены в {payload['file']}![/bold green]\n")

        output.emit_success(
            ctx,
            {**base, "records": len(site_options), "options": site_options, "written": True},
            _render_fetch,
        )
        return

    file = require_data_file(ctx, file)
    store = open_store(file, project=project_name)
    records = store.read_tab(OPTIONS_TAB)
    if records is None:
        raise ValidationError(f"В файле {file.name} нет таба '{OPTIONS_TAB}'")
    site_options = options_from_records(records)

    base = {"mode": "update", "file": str(file), "format": store.format, "tab": OPTIONS_TAB}
    if not site_options:
        output.emit_success(
            ctx,
            {**base, "options": {}, "updated": False, "dry_run": dry_run},
            lambda _: console.print("[yellow]WARNING: Нет настроек для обновления[/yellow]\n"),
        )
        return

    if dry_run:

        def _render_dry(payload: dict):
            console.print(f"[green]OK[/green] Найдено настроек: {len(payload['options'])}\n")
            output.human.key_values("Настройки для обновления:", payload["options"])
            console.print("\n[yellow]DRY-RUN режим: изменения не будут применены[/yellow]")
            console.print("[bold green]DRY-RUN завершен![/bold green]\n")

        output.emit_success(ctx, {**base, "options": site_options, "updated": False, "dry_run": True}, _render_dry)
        return

    domain, username, password = load_and_print_credentials(env_file=env_file, ctx=ctx)

    # Перезапись настроек живого сайта — операция без отката:
    # прежние значения нигде не сохраняются (гейт после проверки --dry-run).
    output.require_confirmation(
        ctx,
        f"Перезаписать {len(site_options)} настроек сайта {domain} "
        f"значениями из {file.name}? Прежние значения не сохраняются.",
        confirm,
    )

    output.vecho(ctx, "Отправка настроек в WordPress...")
    asyncio.run(_update_options(_make_wp_config(domain, username, password), site_options))

    def _render_update(payload: dict):
        console.print(f"[green]OK[/green] Найдено настроек: {len(payload['options'])}\n")
        output.human.key_values("Обновлённые настройки:", payload["options"])
        console.print("\n[bold green]SUCCESS! Настройки обновлены![/bold green]\n")

    output.emit_success(ctx, {**base, "options": site_options, "updated": True, "dry_run": False}, _render_update)


def options_from_records(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Записи таба options → {key: value} для siteoptions.

    Ключ — поле `key` (старые листы Excel — `option`). Записи с пустым ключом
    или значением пропускаются: пустая ячейка не должна затирать настройку.
    """
    site_options: Dict[str, Any] = {}
    for record in records:
        key = record.get("key", record.get("option"))
        value = record.get("value")
        if is_blank(key) or is_blank(value):
            continue
        site_options[str(key).strip()] = value.strip() if isinstance(value, str) else value
    return site_options


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
@output.guard
def bulk_delete(
    ctx: typer.Context,
    domain: Optional[str] = typer.Option(None, "--domain", help="WordPress domain (например, https://example.com)"),
    username: Optional[str] = typer.Option(None, "--username", help="WordPress username"),
    password: Optional[str] = typer.Option(None, "--password", help="WordPress Application Password"),
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env файлу (по умолчанию .env)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    post_type: str = typer.Option(
        ..., "--post-type", "-t", help="Тип контента для удаления (posts, pages, service, и т.д.)"
    ),
    status: str = typer.Option("any", "--status", help="Статус записей для удаления (any, publish, draft, etc.)"),
    force: bool = typer.Option(False, "--force", help="Удалить полностью (минуя корзину)"),
    delete_media: bool = typer.Option(False, "--delete-media", help="Удалять связанные featured media"),
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Показать что будет удалено, без удаления"),
    confirm: bool = output.ConfirmOption,
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
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

    Example: {cli} bulk-delete --post-type posts --dry-run
    """
    env_file = resolve_env_file(ctx, env_file)
    opts = output.get_options(ctx)

    def say(message: str = "") -> None:
        """Человеческий вывод. В --json stdout занят конвертом, значит молчим."""
        if not opts.json and not opts.quiet:
            console.print(message)

    say("\n[bold red]Bulk Delete[/bold red]")
    say("=" * 60 + "\n")

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

    targets = [{"id": post.get("id"), "title": wp_rendered(post.get("title", {}), "No title")} for post in all_posts]

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
            f"Удалить {len(all_posts)} записей типа '{post_type}' (статус: {status}) {mode}{media_note}?",
            confirm,
        )

    # Запускаем удаление
    stats = asyncio.run(_run_bulk_delete(wp_config, all_posts, post_type, force, delete_media))

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
    wp_config: WordPressConfig, all_posts: List[Dict], post_type: str, force: bool, delete_media: bool
) -> Dict[str, int]:
    """Выполнить массовое удаление уже собранных записей."""
    from ..wordpress.media import MediaEndpoint

    stats = {"found": len(all_posts), "deleted": 0, "media_deleted": 0, "errors": 0}

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
@output.guard
def taxonomies(
    ctx: typer.Context,
    env_file: Optional[Path] = typer.Option(
        None,
        "--env-file",
        help="Путь к .env (по умолчанию .env проекта)",
        exists=True,
        file_okay=True,
        dir_okay=False,
        resolve_path=True,
    ),
    include_system: bool = typer.Option(
        False,
        "--include-system",
        help="Включать служебные таксономии (nav_menu, post_format, wp_pattern_category и т.п.)",
    ),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Вывести список таксономий сайта (справка).

    sync/async/validate берут таксономии с сайта сами — вписывать их в
    WP_TAXONOMIES не нужно. env_line — для ручного исключения или для
    проверки без сайта (validate --offline).

    Пример:
        wp-bulk --project ~/sites/mysite taxonomies
        → WP_TAXONOMIES=product_cat,brand,regions

    Exit-коды: 0 — успех, 3 — нет credentials, 5 — недоступен WordPress,
    1 — непредвиденная ошибка.

    Example: {cli} taxonomies --json
    """
    env_file = resolve_env_file(ctx, env_file)
    setup_command_logging(ctx, "taxonomies")

    domain, username, password = load_and_print_credentials(env_file=env_file, ctx=ctx)
    wp_config = _make_wp_config(domain, username, password)

    slugs = asyncio.run(_discover_taxonomy_slugs(wp_config, include_system))

    def _render(payload: dict):
        if not payload["taxonomies"]:
            console.print("[yellow]Таксономии не обнаружены.[/yellow]\n")
            return
        console.print("\n[dim]# Таксономии сайта (sync берёт их с сайта сам):[/dim]")
        console.print(payload["env_line"])
        console.print()

    output.emit_success(
        ctx,
        {
            "domain": domain,
            "taxonomies": slugs,
            "env_line": f"WP_TAXONOMIES={','.join(slugs)}" if slugs else None,
        },
        _render,
    )


async def _discover_taxonomy_slugs(wp_config: WordPressConfig, include_system: bool) -> List[str]:
    """Запросить таксономии через WP REST и вернуть отфильтрованный список slug-ов."""
    from ..wordpress.discovery import WordPressDiscovery
    from ..config import Settings

    settings = Settings()
    async with WordPressClient(wp_config) as client:
        await client.test_connection()
        skip_patterns = [] if include_system else settings.get_skip_taxonomy_patterns()
        discovery = WordPressDiscovery(client, skip_taxonomy_patterns=skip_patterns)
        return await discovery.discover_taxonomies()
