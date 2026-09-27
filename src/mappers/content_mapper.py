"""
Маппинг записей источника (Excel/JSON) в WordPress API payload
"""

from typing import Callable, Dict, Any, List, Mapping, Set, Optional
from loguru import logger

from ..records.base import is_blank, is_missing
from .field_detector import FieldDetector
from .value_converter import convert_acf_value, parse_bool_default_true
from ..utils.file_values import FileValueResolver
from ..constants import (
    ACF_PREFIX,
    BOOL_FIELDS_DEFAULT_TRUE,
    build_nested_payload,
    read_nested_value,
)


class ContentMapper:
    """
    Преобразует строки Excel в payload для WordPress REST API.
    """

    def __init__(
        self,
        columns: List[str],
        allowed_columns: Optional[List[str]] = None,
        known_taxonomies: Optional[List[str]] = None,
        file_resolver: Optional[FileValueResolver] = None,
    ):
        """
        Args:
            columns: Список колонок из Excel
            allowed_columns: Если задан — отправлять в API только эти поля
            known_taxonomies: Явный список таксономий проекта (из .env или WP discovery)
            file_resolver: Подстановка содержимого файла вместо значения-пути
                (`acf.more = "content/1.html"`). None — значения уходят как есть.
        """
        self.columns = columns
        self.detector = FieldDetector(columns, known_taxonomies=known_taxonomies)
        self.analysis = self.detector.analyze()
        self.allowed_columns: Optional[Set[str]] = set(allowed_columns) if allowed_columns else None
        self.file_resolver = file_resolver

    def _file_value(self, field: str, value: Any) -> Any:
        """Значение-путь → содержимое файла (если подключён file_resolver)."""
        if self.file_resolver is None:
            return value
        return self.file_resolver.resolve(field, value)

    def map_row(self, row: Mapping[str, Any], taxonomy_ids: Optional[Dict[str, List[int]]] = None) -> Dict[str, Any]:
        """
        Преобразовать запись источника в WordPress API payload.

        Args:
            row: Запись (Mapping поле → значение)
            taxonomy_ids: Словарь {taxonomy_name: [term_ids]} для таксономий

        Returns:
            Dict готовый для отправки в WordPress API
        """
        payload = {}

        # Логируем информацию о строке для отладки
        row_id = row.get("id", "N/A")
        row_title = row.get("title", "N/A")
        logger.debug(f"Mapping row id={row_id}, title='{row_title}'")

        # Полная структура строки для диагностики
        logger.debug("Row structure (all fields):")
        for field_name in row.keys():
            value = row[field_name]
            logger.debug(f"  {field_name}: {repr(value)} (type={type(value).__name__}, isna={is_blank(value)})")

        # 1. Обычные поля контента
        logger.debug(f"Processing content fields: {self.analysis['content']}")
        for field in self.analysis["content"]:
            if field in row:
                value = self._file_value(field, row[field])

                # Поля, которые нужно отправлять всегда (включая пустые значения для обнуления)
                send_empty_fields = {
                    # Основные текстовые поля
                    "title",
                    "slug",
                    "content",
                    "excerpt",
                    # SEO поля
                    "seo_title",
                    "seo_description",
                    "seo_meta_keywords",
                    "seo_noindex",
                    "seo_nofollow",
                    "custom_permalink",
                    # Другие текстовые поля
                    "description",
                    "caption",
                    "alt_text",
                }

                # Определяем должно ли поле отправляться всегда
                should_send_empty = (
                    field in send_empty_fields or field.startswith("seo_") or field.startswith("custom_")
                )

                # Детальное логирование для SEO полей
                if field.startswith("seo_"):
                    logger.debug(
                        f"SEO field '{field}': raw_value={repr(value)}, "
                        f"type={type(value).__name__}, "
                        f"isna={is_blank(value)}"
                    )

                # Для обычных полей пропускаем пустые значения
                # Для полей из send_empty_fields отправляем пустые значения
                if not is_blank(value):
                    converted = self._convert_value(value)
                    payload[field] = converted

                    # Детальное логирование для SEO полей
                    if field.startswith("seo_"):
                        logger.debug(
                            f"SEO field '{field}': added to payload as {repr(converted)}, "
                            f"type={type(converted).__name__}"
                        )
                elif should_send_empty:
                    # Отправляем пустое значение для обнуления на сайте
                    payload[field] = ""
                    logger.debug(f"Field '{field}': sending empty value to reset on server")

        # 2a. Dot-notation поля — строим вложенный payload по путям a.b.c
        nested_cols = self.analysis.get("nested", [])
        if nested_cols:
            for col in nested_cols:
                if col not in row:
                    continue
                value = row[col]
                # bool-поля с дефолтом true: пустое → True, парсим всегда
                if col in BOOL_FIELDS_DEFAULT_TRUE:
                    build_nested_payload(col, parse_bool_default_true(value), payload)
                    continue
                # Обычные nested-поля: пустые значения пропускаем
                if is_blank(value):
                    continue
                value = self._file_value(col, value)
                build_nested_payload(col, self._convert_value(value), payload)
            logger.debug(f"Nested fields (dot-notation): {nested_cols}")

        # 2b. ACF поля (legacy acf_* колонки)
        acf_data = self._extract_acf_from_row(row, self._file_value)
        if acf_data:
            # Если dot-нотация уже положила что-то в payload["acf"], мержим
            existing_acf = payload.get("acf")
            if isinstance(existing_acf, dict):
                existing_acf.update(acf_data)
            else:
                payload["acf"] = acf_data
            logger.debug(f"ACF fields (legacy prefix): {list(acf_data.keys())}")

        # 3. Таксономии (уже преобразованные в term IDs)
        if taxonomy_ids:
            for taxonomy, term_ids in taxonomy_ids.items():
                if term_ids:
                    payload[taxonomy] = term_ids
            logger.debug(f"Taxonomies: {list(taxonomy_ids.keys())}")

        # 4. Featured media (если есть)
        if "featured_media" in row and not is_blank(row["featured_media"]):
            value = row["featured_media"]
            if value != "":
                try:
                    payload["featured_media"] = int(value)
                except (ValueError, TypeError):
                    logger.warning(f"Invalid featured_media value: {value}")

        # --- Фильтрация по allowed_columns ---
        if self.allowed_columns:
            filtered: Dict[str, Any] = {}
            for col in self.allowed_columns:
                # Dot-notation: читаем по пути, пишем по пути
                if "." in col:
                    val = read_nested_value(col, payload)
                    if val is not None:
                        build_nested_payload(col, val, filtered)
                    continue
                # Legacy ACF prefix
                if col.startswith(ACF_PREFIX):
                    acf_key = col[len(ACF_PREFIX) :]
                    acf_src = payload.get("acf")
                    if isinstance(acf_src, dict) and acf_key in acf_src:
                        filtered.setdefault("acf", {})[acf_key] = acf_src[acf_key]
                    continue
                # Плоский ключ
                if col in payload:
                    filtered[col] = payload[col]
            payload = filtered
            logger.debug(f"Columns filter active: {self.allowed_columns} -> payload keys: {list(payload.keys())}")

        # Итоговый лог с payload
        logger.debug(f"Mapped row id={row_id} to payload keys: {list(payload.keys())}")

        # Детальный лог для SEO полей в финальном payload
        seo_fields_in_payload = {k: v for k, v in payload.items() if k.startswith("seo_")}
        if seo_fields_in_payload:
            logger.debug(f"SEO fields in final payload: {seo_fields_in_payload}")

        return payload

    def _convert_value(self, value: Any) -> Any:
        """
        Конвертировать значение в подходящий тип для API.

        Args:
            value: Значение из Excel

        Returns:
            Сконвертированное значение
        """
        # Пропуск (None / NaN / NaT)
        if is_missing(value):
            return None

        # Строка
        if isinstance(value, str):
            return value.strip()

        # Числа
        if isinstance(value, (int, float)):
            # Если это целое число в виде float
            if isinstance(value, float) and value.is_integer():
                return int(value)
            return value

        # Булевы значения
        if isinstance(value, bool):
            return value

        # Остальное преобразуем в строку
        return str(value)

    @staticmethod
    def _extract_acf_from_row(
        row: Mapping[str, Any],
        file_value: Optional[Callable[[str, Any], Any]] = None,
    ) -> Dict[str, Any]:
        """Извлечь ACF поля из записи и конвертировать значения."""
        acf_data = {}
        for col in row.keys():
            if col.startswith(ACF_PREFIX):
                field_name = col[len(ACF_PREFIX) :]
                value = row[col]
                if not is_blank(value):
                    if file_value is not None:
                        value = file_value(col, value)
                    acf_data[field_name] = convert_acf_value(value)
        return acf_data

    def get_required_fields(self, action: str) -> Set[str]:
        """
        Получить список обязательных полей для действия.

        Args:
            action: Тип действия (add, update, delete)

        Returns:
            Множество обязательных полей
        """
        if action == "add":
            return {"title"}  # Для создания обязателен title

        if action in ("update", "delete"):
            return {"id"}  # Для update/delete обязателен ID

        return set()

    def validate_row(self, row: Mapping[str, Any], action: str) -> tuple[bool, Optional[str]]:
        """
        Валидация записи перед обработкой.

        Args:
            row: Запись (Mapping поле → значение)
            action: Тип действия

        Returns:
            (is_valid, error_message)
        """
        required = self.get_required_fields(action)

        for field in required:
            if field not in row:
                return False, f"Отсутствует обязательное поле: {field}"

            value = row[field]
            if is_blank(value):
                return False, f"Поле '{field}' не может быть пустым"

        return True, None

    def extract_media_path(self, row: Mapping[str, Any]) -> Optional[str]:
        """
        Извлечь путь к медиафайлу из поля 'img_path'.

        Args:
            row: Запись (Mapping поле → значение)

        Returns:
            Путь к медиа или None
        """
        if "img_path" not in row:
            return None

        value = row["img_path"]
        if not is_blank(value):
            return str(value).strip()

        return None

    def should_upload_media(self, row: Mapping[str, Any]) -> bool:
        """
        Проверить нужно ли загружать медиа для этой записи.

        Args:
            row: Запись (Mapping поле → значение)

        Returns:
            True если нужно загрузить медиа
        """
        media_path = self.extract_media_path(row)
        return media_path is not None and media_path != ""
