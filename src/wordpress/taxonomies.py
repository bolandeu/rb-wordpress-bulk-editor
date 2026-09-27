"""
Работа с таксономиями (categories, tags, custom taxonomies)
"""

import asyncio
from typing import Dict, List, Optional, Any, Sequence, Tuple, cast
from loguru import logger

from .client import WordPressClient
from ..constants import TAXONOMY_DELIMITER, TAXONOMY_HIERARCHY_DELIMITER
from ..exceptions import ValidationError

# Префикс мета-поля Yoast SEO для основного (primary) термина таксономии
PRIMARY_TERM_META_PREFIX = "_yoast_wpseo_primary_"


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
        "post_tag": "tags",  # WordPress API использует post_tag, но endpoint - tags
    }

    return normalization_map.get(taxonomy, taxonomy)


def primary_term_meta_key(taxonomy: str) -> str:
    """
    Ключ мета-поля Yoast SEO для основного термина таксономии.

    Yoast хранит primary term в мета-поле поста под внутренним slug-ом таксономии
    (не под именем REST endpoint), поэтому `categories` -> `category`,
    `tags` -> `post_tag`.

    Args:
        taxonomy: Название таксономии (как в Excel/JSON или как endpoint)

    Returns:
        Ключ мета-поля, например `_yoast_wpseo_primary_category`
    """
    slug_map = {
        "categories": "category",
        "category": "category",
        "tags": "post_tag",
        "tag": "post_tag",
    }
    slug = slug_map.get(taxonomy.lower(), taxonomy)
    return f"{PRIMARY_TERM_META_PREFIX}{slug}"


def parse_term_specs(taxonomy_string: str) -> List[List[str]]:
    """
    Разобрать строку таксономии в список спецификаций терминов.

    Формат: термины разделены TAXONOMY_DELIMITER ("; "), внутри одного термина
    уровни иерархии разделены TAXONOMY_HIERARCHY_DELIMITER (">").

    Пример:
        "Термин1; Термин2>Термин2.1"
        -> [["Термин1"], ["Термин2", "Термин2.1"]]

    Все элементы кроме последнего — предки, последний — термин, который реально
    присваивается записи.

    Args:
        taxonomy_string: Строка с терминами

    Returns:
        Список цепочек имён (каждая непустая)

    Raises:
        ValidationError: если в цепочке иерархии есть пустой уровень
            ("a>", ">b", "a>>b")
    """
    if not taxonomy_string or not taxonomy_string.strip():
        return []

    specs: List[List[str]] = []
    for raw_spec in taxonomy_string.split(TAXONOMY_DELIMITER):
        spec = raw_spec.strip()
        if not spec:
            continue

        if TAXONOMY_HIERARCHY_DELIMITER not in spec:
            specs.append([spec])
            continue

        names = [part.strip() for part in spec.split(TAXONOMY_HIERARCHY_DELIMITER)]
        if not all(names):
            raise ValidationError(
                f"Некорректная иерархия термина: '{spec}' (пустой уровень вокруг '{TAXONOMY_HIERARCHY_DELIMITER}')"
            )
        specs.append(names)

    return specs


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

    async def list(self, per_page: int = 100, page: int = 1, **params) -> List[Dict[str, Any]]:
        """
        Получить список терминов.

        Returns:
            Список терминов
        """
        query_params = {"per_page": min(per_page, 100), "page": page, **params}

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

    async def find_by_name(self, name: str, parent: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """
        Найти термин по имени.

        Args:
            name: Название термина
            parent: ID родителя для поиска внутри иерархии (0 — корневой уровень).
                None — искать без ограничения по родителю (плоский поиск).

        Returns:
            Термин или None если не найден
        """
        # Поиск по имени через API (для иерархии сужаем выборку по родителю)
        params: Dict[str, Any] = {"search": name}
        if parent is not None:
            params["parent"] = parent
        terms = await self.list(**params)

        # Точное совпадение
        for term in terms:
            if term.get("name", "").lower() != name.lower():
                continue
            # WP может проигнорировать parent для неиерархических таксономий —
            # проверяем родителя ещё раз по данным термина
            if parent is not None and int(term.get("parent", 0) or 0) != parent:
                continue
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

    async def get_or_create(
        self, name: str, parent: Optional[int] = None, dry_run: bool = False, **extra_data
    ) -> Dict[str, Any]:
        """
        Получить термин по имени или создать если не существует.

        Args:
            name: Название термина
            parent: ID родителя (0 — корневой уровень). Если задан — поиск
                существующего термина тоже ограничен этим родителем, чтобы
                не склеить одноимённые термины из разных веток.
            dry_run: если True — при отсутствии термина не создавать его на
                сайте, а вернуть плейсхолдер с ID=0 ("термин будет создан").
                Поиск существующего термина всё равно выполняется.
            **extra_data: Дополнительные данные для создания

        Returns:
            Термин (существующий или новый/плейсхолдер при dry_run)
        """
        # Пытаемся найти
        existing = await self.find_by_name(name, parent=parent)
        if existing:
            logger.debug(f"Found existing {self.taxonomy}: {name} (ID={existing['id']})")
            return existing

        if dry_run:
            logger.info(f"[DRY-RUN] Would create {self.taxonomy} term: {name}")
            return {"id": 0, "name": name, "parent": parent or 0}

        # Создаем новый
        data = {"name": name, **extra_data}
        if parent is not None:
            data["parent"] = parent
        return await self.create(data)

    async def resolve_term_spec(
        self, names: Sequence[str], taxonomy_cache: Optional["TaxonomyCache"] = None, dry_run: bool = False
    ) -> int:
        """
        Разрешить одну спецификацию термина (возможно, иерархическую) в ID.

        Предки создаются/находятся по порядку, слева направо: ID предыдущего
        уровня передаётся следующему как `parent`.

        Args:
            names: Цепочка имён, например ["Термин2", "Термин2.1"]
            taxonomy_cache: Опциональный кэш терминов
            dry_run: если True — термины не создаются на сайте (см.
                get_or_create/get_or_create_cached). Как только в цепочке
                встретился ещё не существующий предок (ID=0), дальнейшие
                уровни не имеет смысла искать по API под несуществующим
                parent — они тоже считаются "будущими" термина-ми.

        Returns:
            ID последнего термина цепочки (он и присваивается записи;
            0 — если термин(ы) ещё не существуют и dry_run их не создал)

        Raises:
            ValidationError: таксономия неиерархическая — WordPress молча
                игнорирует `parent`, и термин создаётся на верхнем уровне
        """
        parent_id: Optional[int] = None
        pending_ancestor = False  # dry-run: предок ещё не существует на сайте

        for depth, name in enumerate(names):
            # Плоская спецификация (один уровень) — поиск без ограничения по
            # родителю, полностью как до появления иерархии.
            # Корень иерархической цепочки ищем на верхнем уровне (parent=0).
            if len(names) == 1:
                parent: Optional[int] = None
            elif depth == 0:
                parent = 0
            else:
                parent = parent_id

            if pending_ancestor:
                # Родитель сам ещё не существует — искать под ним нечего,
                # термин тоже будет создан
                term = {"id": 0, "name": name, "parent": parent or 0}
            elif taxonomy_cache:
                term = await self.get_or_create_cached(name, taxonomy_cache, parent=parent, dry_run=dry_run)
            else:
                term = await self.get_or_create(name, parent=parent, dry_run=dry_run)

            # Для неиерархической таксономии WordPress игнорирует `parent` при
            # создании: термин уезжает на верхний уровень, и на следующем запуске
            # поиск по родителю его не найдёт — так копятся дубликаты.
            # Признака hierarchical до запроса у нас нет, поэтому ловим факт.
            if parent is not None and int(term.get("parent", 0) or 0) != parent:
                spec_label = TAXONOMY_HIERARCHY_DELIMITER.join(names)
                raise ValidationError(
                    f"Таксономия '{self.taxonomy}' неиерархическая: WordPress создал "
                    f"термин '{name}' на верхнем уровне вместо parent={parent}. "
                    f"Уберите иерархию '{TAXONOMY_HIERARCHY_DELIMITER}' из '{spec_label}'"
                )

            if dry_run and term.get("id") == 0:
                pending_ancestor = True

            parent_id = int(term["id"])

        return cast(int, parent_id)  # names не пуст: цикл присвоил id хотя бы раз

    async def process_taxonomy_string(
        self, taxonomy_string: str, taxonomy_cache: Optional["TaxonomyCache"] = None, dry_run: bool = False
    ) -> Tuple[List[int], Optional[int]]:
        """
        Обработать строку с таксономиями из Excel/JSON.

        Формат: "Термин1; Термин2; Термин3", где любой термин может быть
        иерархическим: "Родитель>Ребёнок>Внук".

        Первая спецификация в списке считается основным (primary) термином.

        Args:
            taxonomy_string: Строка с терминами через разделитель
            taxonomy_cache: Опциональный кэш для обновления при создании новых терминов
            dry_run: если True — термины только ищутся, реальных create-запросов
                к API не будет; отсутствующий термин возвращается с ID=0
                (см. resolve_term_spec)

        Returns:
            (список ID терминов, ID основного термина или None)

        Raises:
            ValidationError: некорректная иерархия в строке или иерархия для
                неиерархической таксономии. Ошибки API по отдельному термину
                (в отличие от них) только логируются.
        """
        term_specs = parse_term_specs(taxonomy_string)
        if not term_specs:
            return [], None

        logger.debug(f"Processing {self.taxonomy}: {term_specs}")

        # Получаем или создаем каждый термин
        term_ids: List[int] = []
        primary_id: Optional[int] = None

        for index, names in enumerate(term_specs):
            try:
                term_id = await self.resolve_term_spec(names, taxonomy_cache, dry_run=dry_run)
            except ValidationError:
                # Ошибка описания таксономии в данных — не глушим, пусть запись
                # упадёт с понятной причиной
                raise
            except Exception as e:
                spec_label = TAXONOMY_HIERARCHY_DELIMITER.join(names)
                logger.error(f"Failed to process {self.taxonomy} term '{spec_label}': {e}")
                continue

            term_ids.append(term_id)
            # Первая спецификация в строке — основной термин
            if index == 0:
                primary_id = term_id

        logger.info(f"Processed {self.taxonomy}: {len(term_ids)} terms (primary={primary_id})")
        return term_ids, primary_id

    async def get_or_create_cached(
        self,
        name: str,
        taxonomy_cache: "TaxonomyCache",
        parent: Optional[int] = None,
        dry_run: bool = False,
        **extra_data,
    ) -> Dict[str, Any]:
        """
        Получить термин по имени или создать если не существует.
        Thread-safe версия с использованием кэша и lock.

        Args:
            name: Название термина
            taxonomy_cache: Кэш таксономий для проверки и обновления
            parent: ID родителя (0 — корневой уровень). Если задан — используется
                отдельный индекс кэша по (parent, имя), чтобы одноимённые термины
                из разных веток не склеивались.
            dry_run: если True — при отсутствии термина не создавать его на
                сайте, а вернуть плейсхолдер с ID=0 ("термин будет создан").
                Плейсхолдер в кэш не кладётся — иначе следующая запись того же
                запуска приняла бы фиктивный ID за уже существующий термин.
            **extra_data: Дополнительные данные для создания

        Returns:
            Термин (существующий или новый/плейсхолдер при dry_run)
        """
        # Блокируем для предотвращения race condition при создании
        async with taxonomy_cache._get_lock():
            # Сначала проверяем в кэше
            if parent is None:
                cached = taxonomy_cache.find(self.taxonomy, name)
            else:
                cached = taxonomy_cache.find_child(self.taxonomy, parent, name)
            if cached:
                logger.debug(f"Found {self.taxonomy} in cache: {name} (ID={cached['id']})")
                return cached

            # Пытаемся найти через API
            existing = await self.find_by_name(name, parent=parent)
            if existing:
                logger.debug(f"Found existing {self.taxonomy}: {name} (ID={existing['id']})")
                # Добавляем в кэш (внутри lock)
                self._cache_term(taxonomy_cache, existing, parent)
                return existing

            if dry_run:
                logger.info(f"[DRY-RUN] Would create {self.taxonomy} term: {name}")
                return {"id": 0, "name": name, "parent": parent or 0}

            # Создаем новый
            data = {"name": name, **extra_data}
            if parent is not None:
                data["parent"] = parent
            new_term = await self.create(data)
            # Добавляем в кэш (внутри lock)
            self._cache_term(taxonomy_cache, new_term, parent)
            return new_term

    def _cache_term(self, taxonomy_cache: "TaxonomyCache", term: Dict[str, Any], parent: Optional[int]) -> None:
        """Положить термин в подходящий индекс кэша (плоский или по родителю)."""
        if parent is None:
            taxonomy_cache.add(self.taxonomy, term)
        else:
            taxonomy_cache.add_child(self.taxonomy, parent, term)


class TaxonomyCache:
    """
    Кэш таксономий для быстрого поиска.
    Загружает все термины в память при старте.

    Thread-safe для async операций через asyncio.Lock.
    """

    def __init__(self):
        # {taxonomy_name: {term_name: term_data}}
        self.cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
        # Отдельный индекс для иерархии: {taxonomy_name: {(parent_id, term_name): term_data}}
        # Держится отдельно от self.cache, чтобы дочерние термины не перекрывали
        # одноимённые корневые при плоском поиске.
        self.children: Dict[str, Dict[Tuple[int, str], Dict[str, Any]]] = {}
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        """Lazy initialization of lock (для поддержки использования вне async контекста)."""
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
            self.cache[taxonomy] = {term["name"].lower(): term for term in terms}

            # Параллельно индексируем по (parent, имя) — для иерархических спецификаций
            self.children[taxonomy] = {(int(term.get("parent", 0) or 0), term["name"].lower()): term for term in terms}

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

    def find_child(self, taxonomy: str, parent_id: int, term_name: str) -> Optional[Dict[str, Any]]:
        """
        Найти термин в кэше по родителю и имени.

        Args:
            taxonomy: Название таксономии
            parent_id: ID родителя (0 — корневой уровень)
            term_name: Название термина

        Returns:
            Термин или None
        """
        if taxonomy not in self.children:
            return None

        return self.children[taxonomy].get((parent_id, term_name.lower()))

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

    def add_child(self, taxonomy: str, parent_id: int, term: Dict[str, Any]):
        """
        Добавить термин в иерархический индекс кэша.

        Args:
            taxonomy: Название таксономии
            parent_id: ID родителя (0 — корневой уровень)
            term: Данные термина
        """
        if taxonomy not in self.children:
            self.children[taxonomy] = {}

        self.children[taxonomy][(parent_id, term["name"].lower())] = term

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
            result[taxonomy] = {term_data["id"]: term_data["name"] for term_data in terms.values()}
        return result
