"""
Асинхронная синхронизация таксономий с параллельной обработкой строк.
"""

from functools import partial
import asyncio
from typing import Dict, Any, Optional
import pandas as pd
from loguru import logger

from ..wordpress.client import WordPressClient
from ..wordpress.taxonomies import TaxonomiesEndpoint, normalize_taxonomy_name
from ..excel.writer import ExcelWriter
from ..config import AppConfig
from ..utils.async_helpers import AsyncBatchProcessor, ProcessResult
from .base_sync import BaseSync


class TaxonomySyncAsync(BaseSync):
    """
    Асинхронная версия TaxonomySync с параллельной обработкой строк.

    Отличия от TaxonomySync:
    - Строки обрабатываются параллельно с ограничением Semaphore
    - Используется AsyncBatchProcessor для управления concurrency
    - Результаты собираются через asyncio.gather()
    """

    def __init__(
        self,
        wp_client: WordPressClient,
        config: AppConfig
    ):
        super().__init__(wp_client, config)
        self.batch_processor = AsyncBatchProcessor(config.sync.concurrency)

    async def sync_sheet(
        self,
        sheet_name: str,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None
    ) -> Dict[str, int]:
        """
        Асинхронная синхронизация листа таксономии с параллельной обработкой строк.

        Args:
            sheet_name: Название листа (= название таксономии)
            df: DataFrame с данными листа
            excel_writer: Writer для обратной записи (если нужна)

        Returns:
            Статистика: {added: N, updated: N, deleted: N, errors: N, skipped: N, total: N}
        """
        stats = self._init_stats()

        # Нормализуем имя таксономии для API
        normalized_taxonomy = normalize_taxonomy_name(sheet_name)

        # Создаем endpoint
        taxonomy_endpoint = TaxonomiesEndpoint(self.wp_client, normalized_taxonomy)

        # Обрабатываем в порядке: delete → update → add
        for action in ["delete", "update", "add"]:
            # Фильтруем строки по action
            action_rows = df[df["action"].str.lower().str.strip() == action]

            if len(action_rows) == 0:
                continue

            logger.info(f"Processing {action}: {len(action_rows)} rows (PARALLEL)")

            # ✨ Создаем задачи для параллельной обработки
            tasks = []
            for idx, row in action_rows.iterrows():
                # Функция обработки (partial для передачи в process_row)
                if action == "delete":
                    process_func = partial(
                        self._delete_term,
                        taxonomy_endpoint,
                        row
                    )
                elif action == "update":
                    process_func = partial(
                        self._update_term,
                        taxonomy_endpoint,
                        row
                    )
                else:  # add
                    process_func = partial(
                        self._add_term,
                        taxonomy_endpoint,
                        row
                    )

                # Создаем задачу через AsyncBatchProcessor
                task = self.batch_processor.process_row(
                    action, idx, row, process_func
                )
                tasks.append(task)

            # ✨ Выполняем ВСЕ задачи параллельно
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Собираем статистику
                for result in results:
                    if isinstance(result, Exception):
                        logger.error(f"Unexpected error during gather: {result}")
                        stats["errors"] += 1
                        continue

                    if not isinstance(result, ProcessResult):
                        logger.error(f"Unexpected result type: {type(result)}")
                        stats["errors"] += 1
                        continue

                    if result.success:
                        # Обновляем счетчики
                        if action == "add":
                            stats["added"] += 1
                        elif action == "update":
                            stats["updated"] += 1
                        elif action == "delete":
                            stats["deleted"] += 1

                        # ✨ Записываем в Excel через ASYNC метод
                        if excel_writer and result.api_response and self.config.sync.update_excel:
                            await excel_writer.update_from_api_response_async(
                                sheet_name,
                                result.row_index,
                                result.api_response,
                                action
                            )
                    else:
                        stats["errors"] += 1
                        # Ошибка уже залогирована в process_row

        # Подсчитываем пропущенные
        valid_actions = {"add", "update", "delete"}
        skipped = df[~df["action"].str.lower().str.strip().isin(valid_actions)]
        stats["skipped"] = len(skipped)
        stats["total"] = len(df)

        return stats

    # ============================================
    # Методы обработки (копия из taxonomy_sync.py)
    # ============================================

    async def _add_term(
        self,
        taxonomy_endpoint: TaxonomiesEndpoint,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Создать новый термин."""
        # Создаем payload
        payload = self._build_payload(row)

        # Проверяем дубликаты по slug (если slug заполнен)
        slug = payload.get("slug")
        if slug and slug.strip():
            existing_term = await taxonomy_endpoint.find_by_slug(slug.strip())
            if existing_term:
                # Термин с таким slug уже существует - делаем update вместо add
                term_id = existing_term["id"]
                logger.warning(
                    f"Term with slug='{slug}' already exists (ID={term_id}). "
                    f"Converting action from 'add' to 'update'."
                )
                result = await taxonomy_endpoint.update(term_id, payload)
                logger.info(f"Updated existing term ID={term_id} (found by slug='{slug}')")
                return result

        # Slug свободен или не заполнен - создаем новый термин
        logger.debug(f"Creating term: {payload.get('name', 'No name')}")
        result = await taxonomy_endpoint.create(payload)
        return result

    async def _update_term(
        self,
        taxonomy_endpoint: TaxonomiesEndpoint,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Обновить существующий термин."""
        term_id = int(row["id"])

        # Создаем payload
        payload = self._build_payload(row)

        # Обновляем термин
        logger.info(f"Updating term ID={term_id} with payload: {payload}")
        result = await taxonomy_endpoint.update(term_id, payload)
        logger.info(f"Updated term ID={term_id}, result link: {result.get('link', 'N/A')}")

        return result

    async def _delete_term(
        self,
        taxonomy_endpoint: TaxonomiesEndpoint,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Удалить термин (в корзину)."""
        term_id = int(row["id"])
        logger.debug(f"Deleting term ID={term_id}")

        # Удаляем в корзину (force=False)
        result = await taxonomy_endpoint.delete(term_id, force=False)
        return result

    def _build_payload(self, row: pd.Series) -> Dict[str, Any]:
        """
        Построить payload для API из строки Excel.

        Args:
            row: Строка из DataFrame

        Returns:
            Payload для WordPress API
        """
        payload = {}

        # Обязательные поля
        if "name" in row.index and pd.notna(row["name"]):
            payload["name"] = str(row["name"]).strip()

        # Текстовые поля - отправляем всегда (даже пустые для очистки)
        if "slug" in row.index:
            if pd.notna(row["slug"]):
                payload["slug"] = str(row["slug"]).strip()
            else:
                payload["slug"] = ""

        if "description" in row.index:
            if pd.notna(row["description"]):
                payload["description"] = str(row["description"]).strip()
            else:
                payload["description"] = ""

        # Parent (для иерархических таксономий)
        if "parent" in row.index and pd.notna(row["parent"]) and str(row["parent"]).strip() != "":
            try:
                parent_id = int(row["parent"])
                if parent_id > 0:
                    payload["parent"] = parent_id
            except (ValueError, TypeError):
                logger.warning(f"Invalid parent ID: {row['parent']}")

        # Кастомные мета-поля (все остальные поля из Excel)
        # Пропускаем системные поля
        system_fields = {"id", "action", "name", "slug", "description", "parent", "link", "count"}

        for field_name in row.index:
            # Пропускаем уже обработанные и системные поля
            if field_name in system_fields or field_name in payload:
                continue

            # Отправляем все остальные поля (кастомные мета-поля)
            value = row[field_name]
            if pd.notna(value):
                payload[field_name] = str(value).strip()
            else:
                # Отправляем пустое значение для очистки
                payload[field_name] = ""

            logger.debug(f"Custom field '{field_name}': {repr(payload[field_name])}")

        return payload
