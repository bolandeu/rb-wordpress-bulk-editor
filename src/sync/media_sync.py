"""
Синхронизация медиафайлов
"""

from pathlib import Path
from typing import Dict, Any, List, Optional
import pandas as pd
from loguru import logger

from ..wordpress.media import MediaEndpoint
from ..excel.writer import ExcelWriter
from ..config import AppConfig
from ..models import SyncReport, SyncResult
from .base_sync import BaseSync


class MediaSync(BaseSync):
    """Синхронизация медиафайлов между Excel и WordPress"""

    def _make_media_endpoint(self) -> MediaEndpoint:
        """Фабричный метод для создания MediaEndpoint (можно переопределить в тестах)."""
        return MediaEndpoint(self.wp_client)

    async def sync_media(
        self,
        df: pd.DataFrame,
        excel_writer: Optional[ExcelWriter] = None
    ) -> SyncReport:
        """
        Синхронизировать лист media.

        Args:
            df: DataFrame с данными media
            excel_writer: Writer для обратной записи

        Returns:
            SyncReport со статистикой и списком ошибочных строк
        """
        stats = self._init_stats()
        error_results: List[SyncResult] = []

        # Создаем endpoint
        media_endpoint = self._make_media_endpoint()

        # Обрабатываем в порядке: delete → update → add
        for action in ["delete", "update", "add"]:
            # Фильтруем строки по action
            action_rows = df[df["action"].str.lower().str.strip() == action]

            if len(action_rows) == 0:
                continue

            logger.info(f"Processing media {action}: {len(action_rows)} rows")

            for idx, row in action_rows.iterrows():
                try:
                    # Валидация
                    is_valid, error_msg = self._validate_row(row, action)
                    if not is_valid:
                        logger.warning(f"Row {idx}: Validation failed: {error_msg}")
                        stats["errors"] += 1
                        continue

                    # Обработка в зависимости от action
                    if action == "delete":
                        result = await self._delete_media(media_endpoint, row)
                    elif action == "update":
                        result = await self._update_media(media_endpoint, row)
                    else:  # add
                        result = await self._add_media(media_endpoint, row)

                    # Обновляем статистику
                    if action == "add":
                        stats["added"] += 1
                    elif action == "update":
                        stats["updated"] += 1
                    elif action == "delete":
                        stats["deleted"] += 1

                    # Записываем обратно в Excel
                    if excel_writer and result and self.config.sync.update_excel:
                        excel_writer.update_from_api_response(
                            "media",
                            idx,
                            result,
                            action
                        )

                except Exception as e:
                    logger.error(f"Row {idx}: Error during {action}: {e}")
                    stats["errors"] += 1
                    error_results.append(SyncResult(
                        row_index=idx,
                        action=action,
                        post_id=None,
                        success=False,
                        error=str(e),
                    ))

        # Подсчитываем пропущенные
        valid_actions = {"add", "update", "delete"}
        skipped = df[~df["action"].str.lower().str.strip().isin(valid_actions)]
        stats["skipped"] = len(skipped)
        stats["total"] = len(df)

        return SyncReport.from_stats(stats, error_results)

    def _validate_row(self, row: pd.Series, action: str) -> tuple[bool, Optional[str]]:
        """
        Валидация строки перед обработкой.

        Args:
            row: pandas Series
            action: Тип действия

        Returns:
            (is_valid, error_message)
        """
        if action == "add":
            # Для add обязателен img_path
            if "img_path" not in row.index:
                return False, "Отсутствует обязательное поле: img_path"

            value = row["img_path"]
            if pd.isna(value) or value == "":
                return False, "Поле 'img_path' не может быть пустым для action=add"

        elif action in ("update", "delete"):
            # Для update/delete обязателен ID
            if "id" not in row.index:
                return False, "Отсутствует обязательное поле: id"

            value = row["id"]
            if pd.isna(value) or value == "":
                return False, "Поле 'id' не может быть пустым"

        return True, None

    async def _add_media(
        self,
        media_endpoint: MediaEndpoint,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Загрузить новый медиафайл."""
        media_path = str(row["img_path"]).strip()

        # Получаем метаданные
        title = row.get("title", "") if "title" in row.index and pd.notna(row.get("title")) else None
        alt_text = row.get("alt_text", "") if "alt_text" in row.index and pd.notna(row.get("alt_text")) else None
        caption = row.get("caption", "") if "caption" in row.index and pd.notna(row.get("caption")) else None

        # Определяем это URL или локальный файл
        if media_path.startswith("http://") or media_path.startswith("https://"):
            # Загрузка по URL
            logger.debug(f"Uploading media from URL: {media_path}")
            result = await media_endpoint.upload_from_url(
                media_path,
                title=title,
                alt_text=alt_text
            )
        else:
            # Локальный файл
            from ..utils.path_utils import resolve_media_file
            file_path = resolve_media_file(media_path, self.config.paths.media_folder)

            if not file_path.exists():
                raise FileNotFoundError(f"Media file not found: {media_path}")

            logger.debug(f"Uploading media file: {file_path}")
            result = await media_endpoint.upload_file(
                file_path,
                title=title,
                alt_text=alt_text,
                caption=caption
            )

        logger.info(f"Uploaded media: {media_path} -> ID={result['id']}")
        return result

    async def _update_media(
        self,
        media_endpoint: MediaEndpoint,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Обновить метаданные медиафайла."""
        media_id = int(row["id"])

        # Собираем метаданные для обновления
        title = row.get("title") if "title" in row.index and pd.notna(row.get("title")) else None
        alt_text = row.get("alt_text") if "alt_text" in row.index and pd.notna(row.get("alt_text")) else None
        caption = row.get("caption") if "caption" in row.index and pd.notna(row.get("caption")) else None
        description = row.get("description") if "description" in row.index and pd.notna(row.get("description")) else None

        logger.debug(f"Updating media ID={media_id}")
        result = await media_endpoint.update(
            media_id,
            title=title,
            alt_text=alt_text,
            caption=caption,
            description=description
        )

        logger.info(f"Updated media ID={media_id}")
        return result

    async def _delete_media(
        self,
        media_endpoint: MediaEndpoint,
        row: pd.Series
    ) -> Dict[str, Any]:
        """Удалить медиафайл (полностью с всеми размерами)."""
        media_id = int(row["id"])

        logger.debug(f"Deleting media ID={media_id} (force=True)")
        result = await media_endpoint.delete(media_id, force=True)

        logger.info(f"Deleted media ID={media_id}")
        return result
