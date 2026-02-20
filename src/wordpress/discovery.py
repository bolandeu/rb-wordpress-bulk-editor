"""
Обнаружение типов контента, таксономий и ACF полей через WordPress REST API
"""

from typing import List, Dict, Any, Optional
from loguru import logger

from .client import WordPressClient
from .posts import PostsEndpoint
from .taxonomies import TaxonomiesEndpoint, normalize_taxonomy_name
from ..constants import DEFAULT_SKIP_POST_TYPE_PATTERNS, DEFAULT_SKIP_TAXONOMY_PATTERNS


class WordPressDiscovery:
    """
    Сервис для обнаружения типов контента, таксономий и ACF полей.
    """

    def __init__(
        self,
        client: WordPressClient,
        skip_post_type_patterns: Optional[List[str]] = None,
        skip_taxonomy_patterns: Optional[List[str]] = None,
    ):
        """
        Args:
            client: WordPress клиент
            skip_post_type_patterns: Паттерны для пропуска типов записей (из .env)
            skip_taxonomy_patterns: Паттерны для пропуска таксономий (из .env)
        """
        self.client = client
        self.skip_post_type_patterns = skip_post_type_patterns or DEFAULT_SKIP_POST_TYPE_PATTERNS
        self.skip_taxonomy_patterns = skip_taxonomy_patterns or DEFAULT_SKIP_TAXONOMY_PATTERNS

    async def discover_post_types(self) -> List[str]:
        """
        Обнаружение всех типов записей через /wp-json/wp/v2/types

        Returns:
            Список названий post types (например: ["posts", "pages", "product"])
        """
        logger.info("Обнаружение доступных типов записей...")

        try:
            # WordPress REST API endpoint для получения типов контента
            response = await self.client.get("/wp-json/wp/v2/types")

            post_types = []
            for type_key, type_data in response.items():
                # Пропускаем служебные типы по паттернам
                should_skip = any(pattern in type_key for pattern in self.skip_post_type_patterns)

                if should_skip:
                    logger.debug(f"Пропускаем тип записи: {type_key} (служебный тип)")
                else:
                    # Нормализуем названия стандартных типов
                    if type_key == "post":
                        type_key = "posts"
                    elif type_key == "page":
                        type_key = "pages"

                    post_types.append(type_key)
                    logger.debug(f"Найден тип записи: {type_key} - {type_data.get('name', 'N/A')}")

            logger.info(f"Обнаружено {len(post_types)} типов записей: {', '.join(post_types)}")
            return post_types

        except Exception as e:
            logger.error(f"Ошибка обнаружения типов записей: {e}")
            # Fallback к стандартным типам
            logger.warning("Используем fallback к стандартным типам: posts, pages")
            return ["posts", "pages"]

    async def discover_taxonomies(self) -> List[str]:
        """
        Обнаружение всех таксономий через /wp-json/wp/v2/taxonomies

        Returns:
            Список названий таксономий (например: ["category", "post_tag", "product_cat"])
        """
        logger.info("Обнаружение доступных таксономий...")

        try:
            # WordPress REST API endpoint для получения таксономий
            response = await self.client.get("/wp-json/wp/v2/taxonomies")

            taxonomies = []
            for tax_key, tax_data in response.items():
                # Пропускаем служебные таксономии по паттернам
                should_skip = any(pattern in tax_key for pattern in self.skip_taxonomy_patterns)

                if should_skip:
                    logger.debug(f"Пропускаем таксономию: {tax_key} (служебная таксономия)")
                else:
                    normalized = normalize_taxonomy_name(tax_key)
                    if normalized not in taxonomies:
                        taxonomies.append(normalized)
                    logger.debug(f"Найдена таксономия: {tax_key} -> {normalized} - {tax_data.get('name', 'N/A')}")

            logger.info(f"Обнаружено {len(taxonomies)} таксономий: {', '.join(taxonomies)}")
            return taxonomies

        except Exception as e:
            logger.error(f"Ошибка обнаружения таксономий: {e}")
            # Fallback к стандартным таксономиям
            logger.warning("Используем fallback к стандартным таксономиям: categories, tags")
            return ["categories", "tags"]

    async def discover_acf_fields_for_post_type(self, post_type: str) -> List[str]:
        """
        Обнаружение ACF полей для типа записи.

        Пробует несколько источников:
        1. Получить один пост типа и прочитать acf ключ из ответа
        2. Fallback: пустой список (ACF не установлен или не настроен)

        Args:
            post_type: Тип записи (posts, pages, product и т.д.)

        Returns:
            Список названий ACF полей (например: ["field_name_1", "field_name_2"])
        """
        logger.debug(f"Обнаружение ACF полей для типа '{post_type}'...")

        try:
            # Получаем один пост для анализа структуры ACF полей
            endpoint = PostsEndpoint(self.client, post_type)
            posts = await endpoint.get_all(per_page=1)

            if not posts:
                logger.debug(f"Нет постов типа '{post_type}', ACF поля не обнаружены")
                return []

            # Проверяем наличие ACF полей в первом посте
            first_post = posts[0]
            acf_data = first_post.get("acf", {})

            if isinstance(acf_data, dict) and acf_data:
                acf_fields = list(acf_data.keys())
                logger.info(f"Обнаружено {len(acf_fields)} ACF полей для '{post_type}': {', '.join(acf_fields)}")
                return acf_fields
            else:
                logger.debug(f"ACF поля не найдены для типа '{post_type}'")
                return []

        except Exception as e:
            logger.debug(f"ACF недоступен для типа '{post_type}': {e}")
            return []

    async def discover_acf_fields_for_taxonomy(self, taxonomy: str) -> List[str]:
        """
        Обнаружение ACF полей для таксономии.

        Аналогично discover_acf_fields_for_post_type но для термов.

        Args:
            taxonomy: Название таксономии (category, post_tag и т.д.)

        Returns:
            Список названий ACF полей
        """
        logger.debug(f"Обнаружение ACF полей для таксономии '{taxonomy}'...")

        try:
            # Нормализуем имя таксономии для API
            normalized_taxonomy = normalize_taxonomy_name(taxonomy)

            # Получаем один терм для анализа структуры ACF полей
            endpoint = TaxonomiesEndpoint(self.client, normalized_taxonomy)
            terms = await endpoint.get_all(per_page=1)

            if not terms:
                logger.debug(f"Нет терминов таксономии '{taxonomy}', ACF поля не обнаружены")
                return []

            # Проверяем наличие ACF полей в первом терме
            first_term = terms[0]
            acf_data = first_term.get("acf", {})

            if isinstance(acf_data, dict) and acf_data:
                acf_fields = list(acf_data.keys())
                logger.info(f"Обнаружено {len(acf_fields)} ACF полей для таксономии '{taxonomy}': {', '.join(acf_fields)}")
                return acf_fields
            else:
                logger.debug(f"ACF поля не найдены для таксономии '{taxonomy}'")
                return []

        except Exception as e:
            logger.debug(f"ACF недоступен для таксономии '{taxonomy}': {e}")
            return []

    async def discover_taxonomies_for_post_type(self, post_type: str) -> List[str]:
        """
        Обнаружение таксономий, связанных с типом записи.

        Через /wp-json/wp/v2/types/{post_type} получаем список таксономий.
        Возвращаем только кастомные таксономии (исключаем стандартные category и tags).

        Args:
            post_type: Тип записи

        Returns:
            Список названий кастомных таксономий (например: ["product_cat", "product_tag"])
        """
        logger.debug(f"Обнаружение таксономий для типа '{post_type}'...")

        try:
            # Нормализуем название типа для API
            api_post_type = post_type
            if post_type == "posts":
                api_post_type = "post"
            elif post_type == "pages":
                api_post_type = "page"

            # Получаем информацию о типе записи
            response = await self.client.get(f"/wp-json/wp/v2/types/{api_post_type}")

            # Извлекаем список таксономий
            all_taxonomies = response.get("taxonomies", [])

            # Фильтруем стандартные таксономии
            standard_taxonomies = {"category", "post_tag"}
            custom_taxonomies = [tax for tax in all_taxonomies if tax not in standard_taxonomies]

            if custom_taxonomies:
                logger.info(f"Обнаружено {len(custom_taxonomies)} кастомных таксономий для '{post_type}': {', '.join(custom_taxonomies)}")
            else:
                logger.debug(f"Кастомные таксономии не найдены для типа '{post_type}'")

            return custom_taxonomies

        except Exception as e:
            logger.debug(f"Не удалось обнаружить таксономии для типа '{post_type}': {e}")
            return []

    async def analyze_fields_from_data(self, data: List[Dict], data_type: str) -> List[str]:
        """
        Анализ полей из реальных данных.
        Собирает уникальный набор всех полей из всех записей/терминов.

        Args:
            data: Список записей или терминов
            data_type: "content" или "taxonomy"

        Returns:
            Список названий полей в правильном порядке
        """
        if not data:
            logger.debug("Нет данных для анализа полей")
            return []

        # Обязательные поля для типов записей
        REQUIRED_POST_FIELDS = ["action", "id", "date", "modified", "title", "type", "slug", "link", "parent", "status"]

        # Обязательные поля для таксономий
        REQUIRED_TAXONOMY_FIELDS = ["action", "id", "name", "slug", "link"]

        # Собираем все уникальные поля из всех записей
        all_fields = set()

        for item in data:
            # Добавляем все ключи верхнего уровня
            for key in item.keys():
                if key == "acf" and isinstance(item[key], dict):
                    # ACF поля добавляем с префиксом
                    for acf_key in item[key].keys():
                        all_fields.add(f"acf_{acf_key}")
                else:
                    all_fields.add(key)

        # Убираем служебные поля API
        skip_fields = {"_links", "guid", "author", "comment_status", "ping_status",
                      "sticky", "template", "format", "meta", "menu_order", "acf"}
        all_fields -= skip_fields

        # Определяем обязательные поля
        if data_type == "content":
            required = REQUIRED_POST_FIELDS
        else:
            required = REQUIRED_TAXONOMY_FIELDS

        # Формируем финальный список: обязательные + остальные
        final_fields = []

        # Добавляем обязательные в нужном порядке
        for field in required:
            if field == "action":
                final_fields.append(field)
            elif field in all_fields:
                final_fields.append(field)

        # Добавляем остальные поля (сначала обычные, потом ACF)
        other_fields = sorted([f for f in all_fields if f not in required and not f.startswith("acf_")])
        acf_fields = sorted([f for f in all_fields if f.startswith("acf_")])

        final_fields.extend(other_fields)
        final_fields.extend(acf_fields)

        logger.debug(f"Проанализировано {len(data)} записей, найдено {len(final_fields)} полей")
        return final_fields
