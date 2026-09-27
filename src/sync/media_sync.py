"""
Синхронизация медиафайлов
"""

from typing import Any, Dict, List, Mapping, Optional, Sequence
from loguru import logger

from ..wordpress.media import MediaEndpoint
from ..records.base import Record, RecordWriter, is_blank, is_missing
from ..models import SyncReport, SyncResult
from .progress import Progress
from .base_sync import BaseSync


class MediaSync(BaseSync):
    """Синхронизация медиафайлов между Excel и WordPress"""

    def _make_media_endpoint(self) -> MediaEndpoint:
        """Фабричный метод для создания MediaEndpoint (можно переопределить в тестах)."""
        return MediaEndpoint(self.wp_client)

    async def sync_media(
        self,
        records: Sequence[Record],
        writer: Optional[RecordWriter] = None,
        progress: Optional[Progress] = None,
    ) -> SyncReport:
        """
        Синхронизировать медиа-записи.

        Args:
            records: Записи медиафайлов
            writer: Writer для обратной записи

        Returns:
            SyncReport со статистикой и списком ошибочных записей
        """
        stats = self._init_stats()
        error_results: List[SyncResult] = []

        records = list(records)

        # Создаем endpoint
        media_endpoint = self._make_media_endpoint()

        # Обрабатываем в порядке: delete → update → add
        by_action = self._group_by_action(records)
        for action in self.ACTION_ORDER:
            action_records = by_action[action]

            if not action_records:
                continue

            logger.info(f"Processing media {action}: {len(action_records)} rows")

            for record in action_records:
                idx = record.write_key
                row = record.data
                try:
                    # Валидация
                    is_valid, error_msg = self._validate_row(row, action)
                    if not is_valid:
                        logger.warning(f"Row {idx}: Validation failed: {error_msg}")
                        stats["errors"] += 1
                        if progress:
                            progress.record_done(record, action, error=True)
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

                    # Записываем обратно в источник
                    if writer and result and self.config.sync.update_excel:
                        writer.update_from_result(idx, result, action)

                    if progress:
                        progress.record_done(record, action, result)

                except Exception as e:
                    logger.error(f"Row {idx}: Error during {action}: {e}")
                    stats["errors"] += 1
                    if progress:
                        progress.record_done(record, action, error=True)
                    error_results.append(
                        SyncResult(
                            row_index=idx,
                            action=action,
                            post_id=None,
                            success=False,
                            error=str(e),
                        )
                    )

        # Подсчитываем пропущенные
        stats["skipped"] = self._count_skipped(records)
        stats["total"] = len(records)

        return SyncReport.from_stats(stats, error_results)

    def _validate_row(self, row: Mapping[str, Any], action: str) -> tuple[bool, Optional[str]]:
        """
        Валидация записи перед обработкой.

        Args:
            row: Запись (Mapping поле → значение)
            action: Тип действия

        Returns:
            (is_valid, error_message)
        """
        if action == "add":
            # Для add обязателен img_path
            if "img_path" not in row:
                return False, "Отсутствует обязательное поле: img_path"

            value = row["img_path"]
            if is_blank(value):
                return False, "Поле 'img_path' не может быть пустым для action=add"

        elif action in ("update", "delete"):
            # Для update/delete обязателен ID
            if "id" not in row:
                return False, "Отсутствует обязательное поле: id"

            value = row["id"]
            if is_blank(value):
                return False, "Поле 'id' не может быть пустым"

        return True, None

    async def _add_media(self, media_endpoint: MediaEndpoint, row: Mapping[str, Any]) -> Dict[str, Any]:
        """Загрузить новый медиафайл."""
        media_path = str(row["img_path"]).strip()

        # Получаем метаданные
        title = row.get("title", "") if "title" in row and not is_missing(row.get("title")) else None
        alt_text = row.get("alt_text", "") if "alt_text" in row and not is_missing(row.get("alt_text")) else None
        caption = row.get("caption", "") if "caption" in row and not is_missing(row.get("caption")) else None

        # Определяем это URL или локальный файл
        if media_path.startswith("http://") or media_path.startswith("https://"):
            # Загрузка по URL
            logger.debug(f"Uploading media from URL: {media_path}")
            result = await media_endpoint.upload_from_url(media_path, title=title, alt_text=alt_text)
        else:
            # Локальный файл
            from ..utils.path_utils import resolve_media_file

            file_path = resolve_media_file(media_path, self.config.paths.media_folder)

            if not file_path.exists():
                raise FileNotFoundError(f"Media file not found: {media_path}")

            logger.debug(f"Uploading media file: {file_path}")
            result = await media_endpoint.upload_file(file_path, title=title, alt_text=alt_text, caption=caption)

        logger.info(f"Uploaded media: {media_path} -> ID={result['id']}")
        return result

    async def _update_media(self, media_endpoint: MediaEndpoint, row: Mapping[str, Any]) -> Dict[str, Any]:
        """Обновить метаданные медиафайла."""
        media_id = int(row["id"])

        # Собираем метаданные для обновления
        title = row.get("title") if "title" in row and not is_missing(row.get("title")) else None
        alt_text = row.get("alt_text") if "alt_text" in row and not is_missing(row.get("alt_text")) else None
        caption = row.get("caption") if "caption" in row and not is_missing(row.get("caption")) else None
        description = (
            row.get("description") if "description" in row and not is_missing(row.get("description")) else None
        )

        logger.debug(f"Updating media ID={media_id}")
        result = await media_endpoint.update(
            media_id, title=title, alt_text=alt_text, caption=caption, description=description
        )

        logger.info(f"Updated media ID={media_id}")
        return result

    async def _delete_media(self, media_endpoint: MediaEndpoint, row: Mapping[str, Any]) -> Dict[str, Any]:
        """Удалить медиафайл (полностью с всеми размерами)."""
        media_id = int(row["id"])

        logger.debug(f"Deleting media ID={media_id} (force=True)")
        result = await media_endpoint.delete(media_id, force=True)

        logger.info(f"Deleted media ID={media_id}")
        return result
