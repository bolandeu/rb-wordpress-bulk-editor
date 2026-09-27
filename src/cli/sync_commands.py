"""
Команды синхронизации: sync, async
"""

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional
import asyncio
from datetime import datetime

from loguru import logger

from . import app, console
from .helpers import get_project, resolve_env_file, require_data_file
from .common import load_and_print_credentials, parse_comma_list
from .. import output
from ..config import AppConfig, SyncConfig, LoggingConfig, known_taxonomies
from ..wordpress.site_schema import load_site_schema
from ..excel.reader import ExcelReader
from ..excel.writer import ExcelWriter
from ..exceptions import WPBulkEditorError
from ..wordpress.client import WordPressClient
from ..wordpress.taxonomies import TaxonomyCache
from ..records import filters as record_filters_mod
from ..records.excel import ExcelRecordSource, ExcelRecordWriter
from ..records.json_source import JsonRecordSource, JsonRecordWriter
from ..records.preflight import FieldRules, check_sources, raise_if_errors
from ..records.selection import (
    dataset_names as _dataset_names,
    is_json_source,
    require_json_tabs as _require_json_tabs,
    validate_dataset_selection as _validate_dataset_selection,
)
from ..sync.content_sync import ContentSync
from ..sync.content_sync_async import ContentSyncAsync
from ..sync.media_sync import MediaSync
from ..sync.orchestrator import sync_record_groups
from ..sync.progress import Progress
from ..sync.taxonomy_sync import TaxonomySync
from ..sync.taxonomy_sync_async import TaxonomySyncAsync
from ..utils.logger import setup_logger, log_summary
from ..utils.auth import load_async_config_from_env
from ..constants import is_taxonomy_column, normalize_action

import typer


# ============================================================
# Shared helpers
# ============================================================


def _validate_test_mode(test_mode: bool, sheets: Optional[str]):
    """Валидация --test-mode (общая для sync и async)."""
    if not test_mode:
        return
    if not sheets:
        console.print(
            "[bold red]ERROR: Тестовый режим (--test-mode) требует указания одного листа через --sheets[/bold red]\n"
        )
        raise typer.Exit(code=1)
    sheets_list = [s.strip() for s in sheets.split(",")]
    if len(sheets_list) > 1:
        console.print("[bold red]ERROR: Тестовый режим (--test-mode) работает только с одним листом[/bold red]\n")
        raise typer.Exit(code=1)
    console.print("[bold yellow]ТЕСТОВЫЙ РЕЖИМ: Будут обработаны только первые 3 строки[/bold yellow]\n")


def _build_sync_config(
    dry_run: bool,
    concurrency: int,
    skip_media: bool,
    skip_tax: bool,
    no_update_excel: bool,
    delete_media: bool,
    skip_existing_media: bool,
    columns: Optional[str],
    timeout: int = 30,
    skip_checks: bool = False,
) -> SyncConfig:
    """Создание SyncConfig (общее для sync и async)."""
    return SyncConfig(
        dry_run=dry_run,
        concurrency=concurrency,
        skip_media=skip_media,
        skip_tax=skip_tax,
        update_excel=not no_update_excel,
        delete_featured_media=delete_media,
        skip_existing_media=skip_existing_media,
        timeout=timeout,
        columns_filter=parse_comma_list(columns),
        skip_checks=skip_checks,
    )


def _console_log_level(verbose: bool, opts) -> Literal["DEBUG", "INFO", "WARNING", "ERROR"]:
    """Уровень консольного лога: --quiet оставляет только ошибки (в файл пишется всё)."""
    if verbose or opts.verbose:
        return "DEBUG"
    return "ERROR" if opts.quiet else "INFO"


def _empty_stats(total: int = 0, skipped: int = 0, errors: int = 0) -> dict:
    """Пустая статистика для листа."""
    return {
        "total": total,
        "added": 0,
        "updated": 0,
        "deleted": 0,
        "skipped": skipped,
        "errors": errors,
    }


def _writeback_enabled(config: AppConfig) -> bool:
    """
    Нужна ли обратная запись в файл данных.

    Кроме `--no-update-excel` её отключает `--dry-run`: прогон без изменений
    не должен трогать файл проекта (в dry-run API отдаёт заглушки вроде
    `id=0`, которым в файле делать нечего).
    """
    return config.sync.update_excel and not config.sync.dry_run


# ============================================================
# Наборы данных прогона (лист Excel / таб JSON)
# ============================================================


@dataclass
class _Dataset:
    """
    Один набор данных прогона.

    Attributes:
        name: Имя листа Excel или таба JSON.
        kind: "content"/"taxonomy"/"media" для Excel (тип листа) либо "mixed"
            для JSON — там тип несёт каждая запись, а не таб целиком.
        source: RecordSource этого набора.
        writer: RecordWriter для обратной записи (None, если отключена).
        own_file: Writer владеет своим файлом — грузить и сохранять вокруг
            обработки набора (JSON: таб за табом по одному и тому же файлу).
    """

    name: str
    kind: str
    source: Any
    writer: Optional[Any] = None
    own_file: bool = False


class _ExcelPlan:
    """Прогон по листам Excel. Обратная запись — через один общий ExcelWriter."""

    def __init__(self, reader: ExcelReader, config: AppConfig, sheets_filter: Optional[str]):
        sheets_by_type = reader.get_sheets_by_type()
        logger.info("Найдено листов:")
        logger.info(f"  Content: {len(sheets_by_type['content'])}")
        logger.info(f"  Taxonomy: {len(sheets_by_type['taxonomy'])}")

        if sheets_filter:
            self.names = _dataset_names(sheets_filter)
        else:
            taxonomy_sheets = [] if config.sync.skip_tax else sheets_by_type["taxonomy"]
            self.names = sheets_by_type["content"] + taxonomy_sheets

        self.config = config
        self.reader = reader
        self._writer: Optional[ExcelWriter] = None
        self.datasets = [
            _Dataset(name=name, kind=reader.classify_sheet(name), source=ExcelRecordSource(reader, name))
            for name in self.names
        ]

    def open_writers(self, taxonomy_cache: TaxonomyCache) -> None:
        if not _writeback_enabled(self.config):
            return
        self._writer = ExcelWriter(self.config.paths.excel_file, taxonomy_cache=taxonomy_cache)
        self._writer.load()
        for dataset in self.datasets:
            dataset.writer = ExcelRecordWriter(self._writer, dataset.name)

    def save(self) -> None:
        if self._writer:
            self._writer.save()


class _JsonPlan:
    """
    Прогон по табам JSON.

    Таб выбирается только явно (`--sheets`): у JSON-файла нет безопасного
    «всё подряд» — рядом с контентом лежат служебные табы (`options`,
    `company`). Каждый таб обрабатывается со своим writer'ом, который грузит
    и сохраняет файл вокруг обработки — иначе второй таб перезаписал бы файл
    состоянием, прочитанным до сохранения первого.
    """

    def __init__(self, path: Path, config: AppConfig, sheets_filter: Optional[str]):
        self.path = Path(path)
        self.config = config
        self.names = _require_json_tabs(self.path, sheets_filter)
        logger.info(f"Табы для обработки: {self.names}")

        self.datasets = []
        for tab in self.names:
            source = JsonRecordSource(self.path, tab)
            # Валидация таба целиком — до подключения к сайту: выбранный не тот
            # таб (служебный) должен падать понятной ошибкой, а не на полпути.
            source.read()
            self.datasets.append(_Dataset(name=tab, kind="mixed", source=source, own_file=True))

    def open_writers(self, taxonomy_cache: TaxonomyCache) -> None:
        if not _writeback_enabled(self.config):
            return
        for dataset in self.datasets:
            dataset.writer = JsonRecordWriter(self.path, dataset.name, taxonomy_cache=taxonomy_cache)

    def save(self) -> None:
        """Ничего: каждый таб сохранён сразу после обработки (own_file)."""


def _preflight(plan, config: AppConfig, filters, site=None) -> None:
    """
    Проверка данных перед отправкой: ошибки останавливают прогон до первого
    изменения на сайте (exit 2), предупреждения только пишутся в лог.

    `site` — схема сайта (таксономии, типы, привязка): имена колонок-таксономий,
    `type`/`taxonomy` записей сверяются с ней.

    Проверяются только записи, которые реально пойдут в прогон: --filter и
    --skip-tax отбрасывают свои. Медиа-наборы — своя схема полей, их не трогаем.
    """
    if config.sync.skip_checks:
        logger.warning("--skip-checks: проверка данных перед отправкой отключена")
        return

    def keep(record) -> bool:
        if config.sync.skip_tax and record.is_taxonomy:
            return False
        return not filters or record_filters_mod.matches(record, filters)

    data_file = Path(config.paths.excel_file)
    checks = check_sources(
        [d.source for d in plan.datasets if d.kind != "media"],
        FieldRules.from_settings(site=site),
        base_dir=data_file.parent,
        keep=keep,
    )
    for check in checks:
        for warning in check.warnings:
            logger.warning(f"Проверка: {warning}")
    raise_if_errors(data_file.name, checks)


@contextmanager
def _open_plan(
    config: AppConfig,
    sheets_filter: Optional[str],
    ctx,
    confirm: bool,
    filters=None,
    site=None,
):
    """
    Открыть источник данных и составить план прогона по его наборам.

    Вызывается уже после подключения к сайту (только чтение): лист Excel
    `rb_topic` — таксономия, только если это известно из списка сайта. Проверка
    перед отправкой и подтверждение необратимого — здесь же, до первого
    изменения на сайте.
    """
    data_file = Path(config.paths.excel_file)

    if is_json_source(data_file):
        if config.sync.skip_tax:
            logger.info("--skip-tax: записи таксономий исключены из обработки")
        plan = _JsonPlan(data_file, config, sheets_filter)
        _preflight(plan, config, filters, site)
        # media-наборов в JSON нет, но --delete-media действует и здесь.
        _confirm_irreversible(ctx, None, [], config, confirm)
        yield plan
        return

    with ExcelReader(data_file, known_taxonomies=known_taxonomies(config)) as reader:
        plan = _ExcelPlan(reader, config, sheets_filter)

        if config.sync.skip_tax:
            logger.info("--skip-tax: taxonomy-листы исключены из обработки")
        if config.sync.skip_media:
            logger.info("--skip-media: загрузка медиа пропускается")
        logger.info(f"Листы для обработки: {plan.names}")
        _preflight(plan, config, filters, site)

        # Подтверждение необратимых удалений — до первого изменения на сайте.
        _confirm_irreversible(ctx, reader, plan.names, config, confirm)

        yield plan


def _taxonomies_to_cache(datasets: List[_Dataset], config: AppConfig) -> List[str]:
    """
    Какие таксономии прогреть в кэше до синхронизации.

    Источник-агностично: у записи-термина это её дискриминатор, у контентной —
    её поля, опознанные как таксономические (`_cat`/`-tag`/...).
    """
    if config.sync.skip_tax:
        return []

    known = known_taxonomies(config)
    names = set()
    for dataset in datasets:
        if dataset.kind == "media":
            continue
        for record in dataset.source.read():
            if record.is_taxonomy:
                names.add(record.discriminator)
            else:
                names.update(field for field in record.data if is_taxonomy_column(field, known))
    return list(names)


# ============================================================
# Unified sync engine (used by both sync and async commands)
# ============================================================


def _count_permanent_media_deletes(
    reader: ExcelReader,
    sheets_to_process: list,
    config: AppConfig,
) -> int:
    """
    Сколько медиафайлов этот прогон удалит навсегда (media-строки с action=delete).

    MediaSync удаляет вложения с `force=True` — мимо корзины, без отката.
    Остальные удаления (`action=delete` на контенте) идут в корзину и обратимы.
    """
    if config.sync.skip_media:
        return 0

    count = 0
    for sheet_name in sheets_to_process:
        if reader.classify_sheet(sheet_name) != "media":
            continue
        for record in ExcelRecordSource(reader, sheet_name).read():
            if normalize_action(record.data.get("action")) == "delete":
                count += 1
    return count


def _confirm_irreversible(
    ctx,
    reader: Optional[ExcelReader],
    sheets_to_process: list,
    config: AppConfig,
    confirm: bool,
) -> None:
    """
    Гейт перед необратимой частью синхронизации.

    Сам по себе sync обратим (перезапуск даёт тот же результат, контент
    удаляется в корзину), но два пути удаляют навсегда: media-строки с
    `action=delete` и `--delete-media` (featured-изображения). Только они и
    требуют подтверждения — вызывается до первого изменения на сайте (после
    чтения схемы сайта, которое ничего не меняет).

    `--delete-media` не зависит от источника: featured-изображения удаляются
    из любой записи с `action=delete`, читана она из Excel или из JSON. А вот
    media-наборов у JSON нет — там `reader=None` и этот подсчёт пропускается.
    """
    if config.sync.dry_run:
        return

    reasons = []
    media_deletes = _count_permanent_media_deletes(reader, sheets_to_process, config) if reader is not None else 0
    if media_deletes:
        reasons.append(f"{media_deletes} медиафайлов будут удалены навсегда (action=delete)")
    if config.sync.delete_featured_media:
        reasons.append("--delete-media: featured-изображения удаляемых записей удаляются навсегда")

    if not reasons:
        return

    output.require_confirmation(ctx, "Необратимо: " + "; ".join(reasons) + ". Продолжить?", confirm)


async def _run_sync_engine(
    config: AppConfig,
    sheets_filter: Optional[str] = None,
    test_mode: bool = False,
    content_sync_cls=ContentSync,
    taxonomy_sync_cls=TaxonomySync,
    retry_config=None,
    circuit_breaker=None,
    command_name: str = "sync",
    ctx=None,
    confirm: bool = False,
    filters=None,
):
    """
    Единый движок синхронизации для sync и async команд.

    Источник (Excel или JSON) выбирается по расширению файла данных,
    дальше всё общее: план прогона → кэш таксономий → группы записей.

    Различия параметризованы:
    - content_sync_cls: ContentSync или ContentSyncAsync
    - taxonomy_sync_cls: TaxonomySync или TaxonomySyncAsync
    - retry_config / circuit_breaker: только для async
    - filters: список условий --filter (field, value), объединяются по И
    """
    started_at = datetime.now()

    stats = {
        "file": str(config.paths.excel_file),
        "started_at": started_at.isoformat(),
        "sheets": {},
        "overall": {"total": 0, "successful": 0, "errors": 0, "success_rate": 0.0},
    }

    try:
        # WordPressClient — async версия передаёт retry/circuit_breaker/timeout
        wp_kwargs: Dict[str, Any] = {"config": config.wordpress}
        if retry_config is not None:
            wp_kwargs["retry_config"] = retry_config
        if circuit_breaker is not None:
            wp_kwargs["circuit_breaker"] = circuit_breaker
        if retry_config is not None or circuit_breaker is not None:
            wp_kwargs["timeout"] = config.sync.timeout

        async with WordPressClient(**wp_kwargs) as wp_client:
            await wp_client.test_connection()

            # Таксономии и типы записей — с сайта (только чтение), а не из
            # списка в .env: новые таксономии подхватываются сами. Ошибка
            # загрузки — ошибка прогона, без подмены набором по умолчанию.
            site = await load_site_schema(wp_client)
            config.sync.site_taxonomies = sorted(site.taxonomy_names())

            with _open_plan(config, sheets_filter, ctx, confirm, filters, site) as plan:
                # Загрузка кэша таксономий
                logger.info("Загрузка кэша таксономий...")
                taxonomy_cache = TaxonomyCache()

                taxonomies_to_cache = _taxonomies_to_cache(plan.datasets, config)
                if taxonomies_to_cache:
                    await taxonomy_cache.load(wp_client, taxonomies_to_cache)

                # Создаём sync-объекты (параметризовано через cls)
                content_sync = content_sync_cls(wp_client, config, taxonomy_cache)
                media_sync = MediaSync(wp_client, config)
                taxonomy_sync = taxonomy_sync_cls(wp_client, config)

                plan.open_writers(taxonomy_cache)

                for dataset in plan.datasets:
                    logger.info(f"\n{'=' * 50}")
                    logger.info(f"Обработка набора: {dataset.name}")
                    logger.info(f"{'=' * 50}")

                    sheet_stats = await _process_dataset(
                        dataset=dataset,
                        content_sync=content_sync,
                        media_sync=media_sync,
                        taxonomy_sync=taxonomy_sync,
                        config=config,
                        test_mode=test_mode,
                        filters=filters,
                    )

                    stats["sheets"][dataset.name] = sheet_stats
                    stats["overall"]["total"] += sheet_stats.get("total", 0)
                    stats["overall"]["successful"] += (
                        sheet_stats.get("added", 0) + sheet_stats.get("updated", 0) + sheet_stats.get("deleted", 0)
                    )
                    stats["overall"]["errors"] += sheet_stats.get("errors", 0)

                plan.save()

        total_skipped = sum(sheet.get("skipped", 0) for sheet in stats["sheets"].values())
        processed = stats["overall"]["total"] - total_skipped
        if processed > 0:
            stats["overall"]["success_rate"] = (stats["overall"]["successful"] / processed) * 100
        elif stats["overall"]["total"] > 0:
            stats["overall"]["success_rate"] = 100.0

        finished_at = datetime.now()
        stats["finished_at"] = finished_at.isoformat()
        stats["duration_seconds"] = (finished_at - started_at).total_seconds()

        log_summary(stats, config.paths.logs_folder / f"{command_name}.log")
        return stats

    except WPBulkEditorError as e:
        # Ожидаемая ошибка (не тот таб, нет --sheets, сайт недоступен): её
        # сообщение самодостаточно, стектрейс только мешает его прочитать.
        logger.error(f"Ошибка {command_name}: {e}")
        raise
    except Exception:
        logger.exception(f"Ошибка {command_name}")
        raise


async def _process_dataset(
    dataset: _Dataset,
    content_sync,
    media_sync,
    taxonomy_sync,
    config: AppConfig,
    test_mode: bool = False,
    filters=None,
) -> dict:
    """Обработка одного набора данных (единая для sync/async и Excel/JSON)."""
    name = dataset.name
    try:
        fields = dataset.source.fields()
        records = list(dataset.source.read())
        logger.info(f"Всего записей: {len(records)}")

        # Отброшенные фильтрами записи считаются пропущенными: набор данных
        # в отчёте остаётся тем же, меняется только что из него синхронизировано.
        dropped = 0
        if filters:
            kept = [r for r in records if record_filters_mod.matches(r, filters)]
            dropped += len(records) - len(kept)
            logger.info(f"--filter: осталось {len(kept)} записей из {len(records)}")
            records = kept

        if test_mode:
            original_len = len(records)
            records = records[:3]
            logger.info(f"ТЕСТОВЫЙ РЕЖИМ: Обрабатываем только первые {len(records)} строк из {original_len}")

        if "action" not in fields:
            logger.warning(f"Набор '{name}' не содержит поле 'action', пропускаем")
            return _empty_stats(total=len(records) + dropped, skipped=len(records) + dropped)

        if dataset.kind == "media":
            if config.sync.skip_media:
                logger.info(f"--skip-media: пропускаем набор '{name}'")
                return _empty_stats(total=len(records) + dropped, skipped=len(records) + dropped)
        elif dataset.kind in ("content", "taxonomy", "mixed"):
            if config.sync.skip_tax:
                if dataset.kind == "taxonomy":
                    logger.info(f"--skip-tax: пропускаем набор '{name}'")
                    return _empty_stats(total=len(records) + dropped, skipped=len(records) + dropped)
                # Таб JSON разнороден: таксономии отсеиваются по самим записям.
                kept = [r for r in records if not r.is_taxonomy]
                dropped += len(records) - len(kept)
                records = kept
        else:
            logger.warning(f"Неизвестный тип набора: {name}")
            return _empty_stats(total=len(records) + dropped, skipped=len(records) + dropped)

        # Writer, владеющий своим файлом (JSON), грузится и сохраняется вокруг
        # набора: следующий таб должен видеть уже записанные изменения.
        writer = dataset.writer
        own_file = writer is not None and dataset.own_file
        if own_file:
            writer.load()

        progress = Progress(total=len(records))
        try:
            if dataset.kind == "media":
                progress.skipped(MediaSync._count_skipped(records))
                report = await media_sync.sync_media(records, writer, progress)
            else:
                report = await sync_record_groups(records, content_sync, taxonomy_sync, writer, progress)
        finally:
            # Сохраняем и на пути ошибки: у JSON writer свой на таб, и без
            # этого id уже созданных в WordPress записей пропали бы — повтор
            # прогона создал бы их заново.
            if own_file:
                writer.save()

        stats = report.to_stats()
        stats["total"] += dropped
        stats["skipped"] += dropped
        return stats

    except Exception:
        logger.exception(f"Ошибка обработки набора {name}")
        return _empty_stats(errors=1)


# ============================================================
# Sync command
# ============================================================


@app.command()
@output.guard
def sync(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к файлу данных .xlsx или .json (авто-определяется из --project)",
    ),
    domain: Optional[str] = typer.Option(None, "--domain", help="WordPress domain (например, https://example.com)"),
    username: Optional[str] = typer.Option(None, "--username", help="WordPress username"),
    password: Optional[str] = typer.Option(None, "--password", help="WordPress Application Password"),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Листы Excel / табы JSON через запятую (posts,pages). Для JSON обязателен",
    ),
    filters: Optional[List[str]] = typer.Option(
        None,
        "--filter",
        help="Фильтр записей field=value (повторяемый, условия по И): --filter type=rb_services",
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
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Режим dry-run (без изменений)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    concurrency: int = typer.Option(5, "--concurrency", help="Количество параллельных запросов"),
    skip_media: bool = typer.Option(False, "--skip-media", help="Пропустить загрузку медиа"),
    skip_tax: bool = typer.Option(False, "--skip-tax", help="Пропустить обновление таксономий (категории, теги)"),
    no_update_excel: bool = typer.Option(
        False, "--no-update-excel", help="Не записывать ID обратно в файл данных (Excel или JSON)"
    ),
    delete_media: bool = typer.Option(
        False, "--delete-media", help="Удалять featured media при удалении записей (action=delete)"
    ),
    skip_existing_media: bool = typer.Option(
        False, "--skip-existing-media", help="Не прикреплять существующие featured_media при update"
    ),
    test_mode: bool = typer.Option(
        False, "--test-mode", help="Тестовый режим: обрабатывает только первые 3 строки из одного листа"
    ),
    columns: Optional[str] = typer.Option(
        None, "--columns", help="Колонки для обновления через запятую (excerpt,seo_title)"
    ),
    skip_checks: bool = typer.Option(
        False,
        "--skip-checks",
        help="Не проверять данные перед отправкой (имена полей, таксономий, значения)",
    ),
    confirm: bool = output.ConfirmOption,
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Синхронизация файла данных -> WordPress.

    Источник выбирается по расширению --file: .xlsx — листы Excel, .json —
    табы JSON. Для JSON --sheets обязателен: в файле рядом с контентом лежат
    служебные табы (options, company), синхронизировать «всё» небезопасно.

    Что делать с записью, задаёт поле `action`:
      add    — создать (нужен title, полученный id пишется обратно в файл)
      update — обновить (нужен id)
      delete — удалить (нужен id)
      skip   — не трогать запись

    Обрабатываются наборы типов контента (posts, pages, CPT), таксономий
    (categories, tags, кастомные) и медиа (media, только Excel).

    `--filter field=value` (повторяемый, по И) сужает выборку до синхронизации:
    сравнение точное строковое по сырому значению поля, dot-нотация работает
    как обычное имя поля (--filter acf.f1=value).

    Перед отправкой данные проверяются так же, как в `validate` (имена полей
    и таксономий, id/status/slug, файлы в значениях): при ошибках прогон
    останавливается до подключения к сайту, включая --dry-run. Отключается
    --skip-checks.

    Exit-коды: 0 — успех, 2 — аргументы/структура файла/проверка перед
    отправкой, 3 — нет credentials, 5 — недоступен WordPress,
    1 — непредвиденная ошибка.

    Example: {cli} sync --file site.json --sheets site.ru --dry-run
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    file = require_data_file(ctx, file)
    record_filters = record_filters_mod.parse_filters(filters)

    opts = output.get_options(ctx)
    human_mode = not opts.json and not opts.quiet

    if human_mode:
        console.print(f"\n[bold blue]WordPress Bulk Editor - Sync[/bold blue]\n")  # noqa: F541 — как в HEAD: тело команды не менялось (хук docstring)
        console.print(f"Файл данных: [cyan]{file}[/cyan]\n")

    _validate_test_mode(test_mode, sheets)
    # Выбор наборов данных проверяется до credentials: список табов файла
    # агент должен получить и без доступа к сайту.
    _validate_dataset_selection(file, sheets)

    domain, username, password = load_and_print_credentials(
        env_file=env_file,
        domain=domain,
        username=username,
        password=password,
        ctx=ctx,
    )

    sync_config = _build_sync_config(
        dry_run=dry_run,
        concurrency=concurrency,
        skip_media=skip_media,
        skip_tax=skip_tax,
        no_update_excel=no_update_excel,
        delete_media=delete_media,
        skip_existing_media=skip_existing_media,
        columns=columns,
        skip_checks=skip_checks,
    )

    config = AppConfig.from_excel(
        excel_file=file,
        wp_url=domain,
        wp_username=username,
        wp_password=password,
        project_root=proj.root,
        sync=sync_config,
        logging=LoggingConfig(level=_console_log_level(verbose, opts)),
    )

    if config.sync.columns_filter and human_mode:
        console.print(f"[yellow]--columns: обновляются только {', '.join(config.sync.columns_filter)}[/yellow]\n")

    config.paths.ensure_dirs()
    log_file = setup_logger(
        logs_folder=config.paths.logs_folder,
        config=config.logging,
        command_name="sync",
        console_stream=output.log_stream(ctx),
    )

    stats = asyncio.run(
        _run_sync_engine(
            config,
            sheets,
            test_mode=test_mode,
            command_name="sync",
            ctx=ctx,
            confirm=confirm,
            filters=record_filters,
        )
    )

    def _render(payload: dict):
        console.print(f"\n[bold green]SUCCESS! Синхронизация завершена![/bold green]")  # noqa: F541 — как в HEAD: тело команды не менялось (хук docstring)
        console.print(f"Лог-файл: [cyan]{payload['log_file']}[/cyan]\n")

    output.emit_success(ctx, {**(stats or {}), "log_file": str(log_file)}, _render)


# ============================================================
# Async sync command
# ============================================================


@app.command(name="async")
@output.guard
def async_sync(
    ctx: typer.Context,
    file: Optional[Path] = typer.Option(
        None,
        "--file",
        "-f",
        help="Путь к файлу данных .xlsx или .json (авто-определяется из --project)",
    ),
    domain: Optional[str] = typer.Option(None, "--domain", help="WordPress domain (например, https://example.com)"),
    username: Optional[str] = typer.Option(None, "--username", help="WordPress username"),
    password: Optional[str] = typer.Option(None, "--password", help="WordPress Application Password"),
    sheets: Optional[str] = typer.Option(
        None,
        "--sheets",
        help="Листы Excel / табы JSON через запятую (posts,pages). Для JSON обязателен",
    ),
    filters: Optional[List[str]] = typer.Option(
        None,
        "--filter",
        help="Фильтр записей field=value (повторяемый, условия по И): --filter type=rb_services",
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
    dry_run: bool = typer.Option(False, "--dry-run", "-d", help="Режим dry-run (без изменений)"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Подробные логи"),
    concurrency: Optional[int] = typer.Option(
        None, "--concurrency", help="Количество параллельных запросов (default: 5)"
    ),
    api_timeout: Optional[int] = typer.Option(None, "--api-timeout", help="API timeout в секундах (default: 30)"),
    max_retries: Optional[int] = typer.Option(None, "--max-retries", help="Максимум повторных попыток (default: 3)"),
    enable_circuit_breaker: bool = typer.Option(
        True, "--circuit-breaker/--no-circuit-breaker", help="Включить circuit breaker"
    ),
    skip_media: bool = typer.Option(False, "--skip-media", help="Пропустить загрузку медиа"),
    skip_tax: bool = typer.Option(False, "--skip-tax", help="Пропустить обновление таксономий (категории, теги)"),
    no_update_excel: bool = typer.Option(
        False, "--no-update-excel", help="Не записывать ID обратно в файл данных (Excel или JSON)"
    ),
    delete_media: bool = typer.Option(
        False, "--delete-media", help="Удалять featured media при удалении записей (action=delete)"
    ),
    skip_existing_media: bool = typer.Option(
        False, "--skip-existing-media", help="Не прикреплять существующие featured_media при update"
    ),
    test_mode: bool = typer.Option(False, "--test-mode", help="Тестовый режим: обрабатывает только первые 3 строки"),
    columns: Optional[str] = typer.Option(
        None, "--columns", help="Колонки для обновления через запятую (excerpt,seo_title)"
    ),
    skip_checks: bool = typer.Option(
        False,
        "--skip-checks",
        help="Не проверять данные перед отправкой (имена полей, таксономий, значения)",
    ),
    confirm: bool = output.ConfirmOption,
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
):
    """
    Асинхронная синхронизация файла данных -> WordPress

    Источник (.xlsx или .json), --sheets, --filter и проверка перед
    отправкой (--skip-checks) — как у sync. Результат — тот же конверт,
    что у sync (--json: статистика по наборам, log_file, cli_version).

    Отличия от sync:
    - Параллельная обработка строк (Semaphore)
    - Retry с exponential backoff
    - Circuit breaker для устойчивости
    - Настраиваемые таймауты

    Exit-коды: 0 — успех, 2 — аргументы/структура файла/проверка перед
    отправкой, 3 — нет credentials, 5 — недоступен WordPress,
    1 — непредвиденная ошибка.

    Example: {cli} async --file site.json --sheets site.ru --json
    """
    proj = get_project(ctx)
    env_file = resolve_env_file(ctx, env_file)
    file = require_data_file(ctx, file)
    record_filters = record_filters_mod.parse_filters(filters)

    opts = output.get_options(ctx)
    human_mode = not opts.json and not opts.quiet

    if human_mode:
        console.print(f"\n[bold cyan] WordPress Bulk Editor - Async Sync[/bold cyan]\n")  # noqa: F541 — как в HEAD: тело команды не менялось (хук docstring)
        console.print(f"Файл данных: [cyan]{file}[/cyan]\n")

    _validate_test_mode(test_mode, sheets)
    _validate_dataset_selection(file, sheets)
    domain, username, password = load_and_print_credentials(
        env_file=env_file,
        domain=domain,
        username=username,
        password=password,
        ctx=ctx,
    )

    concurrency_val, retry_cfg, circuit_breaker_obj = load_async_config_from_env(
        env_file=env_file,
        cli_concurrency=concurrency,
        cli_max_retries=max_retries,
    )
    if not enable_circuit_breaker:
        circuit_breaker_obj = None

    sync_config = _build_sync_config(
        dry_run=dry_run,
        concurrency=concurrency_val,
        skip_media=skip_media,
        skip_tax=skip_tax,
        no_update_excel=no_update_excel,
        delete_media=delete_media,
        skip_existing_media=skip_existing_media,
        columns=columns,
        timeout=api_timeout or 30,
        skip_checks=skip_checks,
    )

    config = AppConfig.from_excel(
        excel_file=file,
        wp_url=domain,
        wp_username=username,
        wp_password=password,
        project_root=proj.root,
        sync=sync_config,
        logging=LoggingConfig(level=_console_log_level(verbose, opts)),
    )

    if config.sync.columns_filter and human_mode:
        console.print(f"[yellow]--columns: обновляются только {', '.join(config.sync.columns_filter)}[/yellow]\n")

    config.paths.ensure_dirs()
    log_file = setup_logger(
        logs_folder=config.paths.logs_folder,
        config=config.logging,
        command_name="async_sync",
        console_stream=output.log_stream(ctx),
    )

    stats = asyncio.run(
        _run_sync_engine(
            config,
            sheets,
            test_mode=test_mode,
            content_sync_cls=ContentSyncAsync,
            taxonomy_sync_cls=TaxonomySyncAsync,
            retry_config=retry_cfg,
            circuit_breaker=circuit_breaker_obj,
            command_name="async_sync",
            ctx=ctx,
            confirm=confirm,
            filters=record_filters,
        )
    )

    def _render(payload: dict):
        console.print(f"\n[bold green]SUCCESS! Асинхронная синхронизация завершена![/bold green]")  # noqa: F541 — как в HEAD: тело команды не менялось (хук docstring)
        console.print(f"Лог-файл: [cyan]{payload['log_file']}[/cyan]\n")

    output.emit_success(ctx, {**(stats or {}), "log_file": str(log_file)}, _render)
