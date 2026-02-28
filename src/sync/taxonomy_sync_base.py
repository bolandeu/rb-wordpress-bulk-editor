"""
Базовый класс с общей логикой для TaxonomySync и TaxonomySyncAsync.

Содержит методы обработки терминов, которые идентичны в обоих вариантах:
_add_term, _update_term, _delete_term, _build_payload
"""

from abc import abstractmethod
from typing import Dict, Any
import pandas as pd
from loguru import logger

from ..wordpress.taxonomies import TaxonomiesEndpoint
from ..constants import ACF_PREFIX
from .base_sync import BaseSync


class TaxonomySyncBase(BaseSync):
    """
    Базовый класс для синхронизации таксономий.

    Содержит общую логику обработки терминов.
    Наследники (TaxonomySync, TaxonomySyncAsync) реализуют только sync_sheet().
    """

    @abstractmethod
    async def sync_sheet(self, sheet_name: str, df, excel_writer=None):
        """Синхронизировать один лист таксономии."""

    def _make_taxonomy_endpoint(self, taxonomy: str) -> TaxonomiesEndpoint:
        """Фабричный метод для TaxonomiesEndpoint (можно переопределить в тестах)."""
        return TaxonomiesEndpoint(self.wp_client, taxonomy)

    async def _add_term(
        self,
        taxonomy_endpoint: TaxonomiesEndpoint,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Создать новый термин."""
        # Создаем payload
        payload = self._build_payload(row)

        # Dry-run: только логируем
        if self.config.sync.dry_run:
            logger.info(f"[DRY-RUN] Would CREATE term: {payload.get('name', 'No name')}")
            return {"id": 0, "name": payload.get("name", ""), "dry_run": True}

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

        # Dry-run: только логируем
        if self.config.sync.dry_run:
            logger.info(f"[DRY-RUN] Would UPDATE term ID={term_id}: {payload.get('name', 'No name')}")
            return {"id": term_id, "name": payload.get("name", ""), "dry_run": True}

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

        # Dry-run: только логируем
        if self.config.sync.dry_run:
            logger.info(f"[DRY-RUN] Would DELETE term ID={term_id}")
            return {"id": term_id, "deleted": True, "dry_run": True}

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
        acf_data = {}

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

            value = row[field_name]

            # ACF поля - группируем в отдельный объект
            if field_name.startswith(ACF_PREFIX):
                acf_key = field_name[len(ACF_PREFIX):]  # Убираем префикс acf_
                if pd.notna(value) and str(value).strip() != "":
                    acf_data[acf_key] = str(value).strip()
                continue

            # Остальные кастомные поля отправляем на верхнем уровне
            if pd.notna(value):
                payload[field_name] = str(value).strip()
            else:
                # Отправляем пустое значение для очистки
                payload[field_name] = ""

            logger.debug(f"Custom field '{field_name}': {repr(payload[field_name])}")

        # Добавляем ACF данные если есть
        if acf_data:
            payload["acf"] = acf_data
            logger.debug(f"ACF fields: {list(acf_data.keys())}")

        return payload
