"""
Работа с таксономиями (categories, tags, custom taxonomies)
"""

from typing import Dict, List, Optional, Any
from loguru import logger

from .client import WordPressClient
from ..constants import TAXONOMY_DELIMITER


def normalize_taxonomy_name(taxonomy: str) -> str:
    """
    Нормализует имя таксономии для использования в WordPress API endpoints.

    WordPress API использует множественное число для стандартных таксономий:
    - category -> categories
    - tag -> tags

    Args:
        taxonomy: Название таксономии (из Excel или других источников)

    Returns:
        Нормализованное название таксономии для API endpoint
    """
    # Маппинг единственного числа на множественное для стандартных таксономий
    normalization_map = {
        "category": "categories",
        "tag": "tags",
        "post_tag": "tags"  # WordPress API использует post_tag, но endpoint - tags
    }

    return normalization_map.get(taxonomy, taxonomy)


class TaxonomiesEndpoint:
    """Endpoint для работы с таксономиями"""

    def __init__(self, client: WordPressClient, taxonomy: str = "categories"):
        """
        Args:
            client: WordPress HTTP клиент
            taxonomy: Название таксономии (categories, tags, custom-cat)
        """
        self.client = client
        self.taxonomy = taxonomy
        self.endpoint = f"/wp-json/wp/v2/{taxonomy}"

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Создать новый термин таксономии.

        Args:
            data: Данные (name, slug, description, etc.)

        Returns:
            Созданный термин с ID
        """
        logger.debug(f"Creating {self.taxonomy} term: {data.get('name', 'No name')}")
        result = await self.client.post(self.endpoint, data)
        logger.info(f"Created {self.taxonomy} ID={result['id']}: {result['name']}")
        return result

    async def update(self, term_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """Обновить термин."""
        logger.debug(f"Updating {self.taxonomy} ID={term_id}")
        endpoint = f"{self.endpoint}/{term_id}"
        result = await self.client.put(endpoint, data)
        logger.info(f"Updated {self.taxonomy} ID={term_id}")
        return result

    async def delete(self, term_id: int, force: bool = False) -> Dict[str, Any]:
        """Удалить термин."""
        logger.debug(f"Deleting {self.taxonomy} ID={term_id}")
        endpoint = f"{self.endpoint}/{term_id}"
        params = {"force": "true" if force else "false"}
        result = await self.client.delete(endpoint, params)
        logger.info(f"Deleted {self.taxonomy} ID={term_id}")
        return result

    async def get(self, term_id: int) -> Dict[str, Any]:
        """Получить термин по ID."""
        endpoint = f"{self.endpoint}/{term_id}"
        return await self.client.get(endpoint)

    async def list(
        self,
        per_page: int = 100,
        page: int = 1,
        **params
    ) -> List[Dict[str, Any]]:
        """
        Получить список терминов.

        Returns:
            Список терминов
        """
        query_params = {
            "per_page": min(per_page, 100),
            "page": page,
            **params
        }

        result = await self.client.get(self.endpoint, query_params)

        if isinstance(result, list):
            return result
        return [result]

    async def get_all(self) -> List[Dict[str, Any]]:
        """
        Получить все термины таксономии.

        Returns:
            Список всех терминов
        """
        all_terms = []
        page = 1
        per_page = 100

        while True:
            terms = await self.list(per_page=per_page, page=page)

            if not terms:
                break

            all_terms.extend(terms)

            if len(terms) < per_page:
                break

            page += 1

        logger.info(f"Fetched {len(all_terms)} {self.taxonomy} terms")
        return all_terms

    async def find_by_name(self, name: str) -> Optional[Dict[str, Any]]:
        """
        Найти термин по имени.

        Args:
            name: Название термина

        Returns:
            Термин или None если не найден
        """
        # Поиск по имени через API
        terms = await self.list(search=name)

        # Точное совпадение
        for term in terms:
            if term.get("name", "").lower() == name.lower():
                return term

        return None

    async def find_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """
        Найти термин по slug.

        Args:
            slug: Slug термина

        Returns:
            Термин или None если не найден
        """
        # Поиск по slug через API
        terms = await self.list(slug=slug)

        # Точное совпадение
        for term in terms:
            if term.get("slug", "") == slug:
                return term

        return None

    async def get_or_create(self, name: str, **extra_data) -> Dict[str, Any]:
        """
        Получить термин по имени или создать если не существует.

        Args:
            name: Название термина
            **extra_data: Дополнительные данные для создания

        Returns:
            Термин (существующий или новый)
        """
        # Пытаемся найти
        existing = await self.find_by_name(name)
        if existing:
            logger.debug(f"Found existing {self.taxonomy}: {name} (ID={existing['id']})")
            return existing

        # Создаем новый
        data = {"name": name, **extra_data}
        return await self.create(data)

    async def process_taxonomy_string(
        self,
        taxonomy_string: str,
        taxonomy_cache: Optional["TaxonomyCache"] = None
    ) -> List[int]:
        """
        Обработать строку с таксономиями из Excel.

        Формат: "Термин1; Термин2; Термин3"

        Args:
            taxonomy_string: Строка с терминами через разделитель
            taxonomy_cache: Опциональный кэш для обновления при создании новых терминов

        Returns:
            Список ID терминов
        """
        if not taxonomy_string or taxonomy_string.strip() == "":
            return []

        # Разбиваем по разделителю
        term_names = [name.strip() for name in taxonomy_string.split(TAXONOMY_DELIMITER)]
        term_names = [name for name in term_names if name]  # Убираем пустые

        if not term_names:
            return []

        logger.debug(f"Processing {self.taxonomy}: {term_names}")

        # Получаем или создаем каждый термин
        term_ids = []
        for name in term_names:
            try:
                # Используем get_or_create_cached если есть кэш (thread-safe)
                if taxonomy_cache:
                    term = await self.get_or_create_cached(name, taxonomy_cache)
                else:
                    term = await self.get_or_create(name)
                term_ids.append(term["id"])
            except Exception as e:
                logger.error(f"Failed to process {self.taxonomy} term '{name}': {e}")

        logger.info(f"Processed {self.taxonomy}: {len(term_ids)} terms")
        return term_ids

    async def get_or_create_cached(
        self,
        name: str,
        taxonomy_cache: "TaxonomyCache",
        **extra_data
    ) -> Dict[str, Any]:
        """
        Получить термин по имени или создать если не существует.
        Thread-safe версия с использованием кэша и lock.

        Args:
            name: Название термина
            taxonomy_cache: Кэш таксономий для проверки и обновления
            **extra_data: Дополнительные данные для создания

        Returns:
            Термин (существующий или новый)
        """
        # Блокируем для предотвращения race condition при создании
        async with taxonomy_cache._get_lock():
            # Сначала проверяем в кэше
            cached = taxonomy_cache.find(self.taxonomy, name)
            if cached:
                logger.debug(f"Found {self.taxonomy} in cache: {name} (ID={cached['id']})")
                return cached

            # Пытаемся найти через API
            existing = await self.find_by_name(name)
            if existing:
                logger.debug(f"Found existing {self.taxonomy}: {name} (ID={existing['id']})")
                # Добавляем в кэш (внутри lock)
                taxonomy_cache.add(self.taxonomy, existing)
                return existing

            # Создаем новый
            data = {"name": name, **extra_data}
            new_term = await self.create(data)
            # Добавляем в кэш (внутри lock)
            taxonomy_cache.add(self.taxonomy, new_term)
            return new_term


class TaxonomyCache:
    """
    Кэш таксономий для быстрого поиска.
    Загружает все термины в память при старте.

    Thread-safe для async операций через asyncio.Lock.
    """

    def __init__(self):
        # {taxonomy_name: {term_name: term_data}}
        self.cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._lock: Optional["asyncio.Lock"] = None

    def _get_lock(self) -> "asyncio.Lock":
        """Lazy initialization of lock (для поддержки использования вне async контекста)."""
        import asyncio
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def load(self, client: WordPressClient, taxonomies: List[str]):
        """
        Загрузить таксономии в кэш.

        Args:
            client: WordPress клиент
            taxonomies: Список названий таксономий для загрузки
        """
        logger.info(f"Loading taxonomies cache: {taxonomies}")

        for taxonomy in taxonomies:
            # Нормализуем имя таксономии для API endpoint
            normalized_taxonomy = normalize_taxonomy_name(taxonomy)
            endpoint = TaxonomiesEndpoint(client, normalized_taxonomy)
            terms = await endpoint.get_all()

            # Индексируем по имени (lowercase для быстрого поиска)
            # Сохраняем в кэше под оригинальным именем для обратной совместимости
            self.cache[taxonomy] = {
                term["name"].lower(): term
                for term in terms
            }

            logger.info(f"Cached {len(terms)} terms for {taxonomy}")

    def find(self, taxonomy: str, term_name: str) -> Optional[Dict[str, Any]]:
        """
        Найти термин в кэше.

        Args:
            taxonomy: Название таксономии
            term_name: Название термина

        Returns:
            Термин или None
        """
        if taxonomy not in self.cache:
            return None

        return self.cache[taxonomy].get(term_name.lower())

    def add(self, taxonomy: str, term: Dict[str, Any]):
        """
        Добавить термин в кэш (sync версия).

        Args:
            taxonomy: Название таксономии
            term: Данные термина
        """
        if taxonomy not in self.cache:
            self.cache[taxonomy] = {}

        self.cache[taxonomy][term["name"].lower()] = term

    async def add_async(self, taxonomy: str, term: Dict[str, Any]):
        """
        Добавить термин в кэш (async thread-safe версия).

        Args:
            taxonomy: Название таксономии
            term: Данные термина
        """
        async with self._get_lock():
            self.add(taxonomy, term)

    def get_id_to_name_mapping(self) -> Dict[str, Dict[int, str]]:
        """
        Получить маппинг ID -> Name для всех таксономий.

        Returns:
            Словарь {taxonomy_name: {term_id: term_name}}
        """
        result = {}
        for taxonomy, terms in self.cache.items():
            result[taxonomy] = {
                term_data["id"]: term_data["name"]
                for term_data in terms.values()
            }
        return result
