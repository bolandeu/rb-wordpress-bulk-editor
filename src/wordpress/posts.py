"""
Работа с постами, страницами и кастомными типами через WordPress REST API
"""

from typing import Dict, List, Optional, Any
from loguru import logger

from .client import WordPressClient
from ..exceptions import NotFoundError, WordPressError


FETCH_ALL_STATUSES = ("publish", "draft", "pending", "private", "future")


class PostsEndpoint:
    """Endpoint для работы с контентом (posts, pages, custom types)"""

    def __init__(self, client: WordPressClient, post_type: str = "posts"):
        """
        Args:
            client: WordPress HTTP клиент
            post_type: Тип контента (posts, pages, или кастомный тип)
        """
        self.client = client
        self.post_type = post_type
        self.endpoint = f"/wp-json/wp/v2/{post_type}"

    async def create(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Создать новую запись.

        Args:
            data: Данные для создания (title, content, status, etc.)

        Returns:
            Созданная запись с ID
        """
        logger.debug(f"Creating {self.post_type}: {data.get('title', 'No title')}")
        result = await self.client.post(self.endpoint, data)
        logger.info(f"Created {self.post_type} ID={result['id']}: {result.get('title', {}).get('rendered', 'N/A')}")
        return result

    async def update(self, post_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Обновить существующую запись.

        Args:
            post_id: ID записи
            data: Данные для обновления

        Returns:
            Обновленная запись
        """
        logger.debug(f"Updating {self.post_type} ID={post_id}")
        endpoint = f"{self.endpoint}/{post_id}"
        result = await self.client.put(endpoint, data)
        logger.info(f"Updated {self.post_type} ID={post_id}")
        return result

    async def delete(self, post_id: int, force: bool = False) -> Dict[str, Any]:
        """
        Удалить запись.

        Args:
            post_id: ID записи
            force: True - удалить навсегда, False - в корзину

        Returns:
            Результат удаления
        """
        logger.debug(f"Deleting {self.post_type} ID={post_id} (force={force})")
        endpoint = f"{self.endpoint}/{post_id}"
        params = {"force": "true" if force else "false"}
        result = await self.client.delete(endpoint, params)
        logger.info(f"Deleted {self.post_type} ID={post_id} (to trash: {not force})")
        return result

    async def get(self, post_id: int) -> Dict[str, Any]:
        """
        Получить запись по ID.

        Args:
            post_id: ID записи

        Returns:
            Данные записи
        """
        endpoint = f"{self.endpoint}/{post_id}"
        return await self.client.get(endpoint)

    async def list(
        self,
        per_page: int = 100,
        page: int = 1,
        status: Optional[str] = None,
        suppress_non_retryable_log: bool = False,
        **params,
    ) -> List[Dict[str, Any]]:
        """
        Получить список записей.

        Args:
            per_page: Количество на странице (max 100)
            page: Номер страницы
            status: Статус (publish, draft, etc.). None/any = не передавать status.
            **params: Дополнительные параметры

        Returns:
            Список записей
        """
        query_params = {"per_page": min(per_page, 100), "page": page, **params}

        # WordPress REST API не везде принимает status=any, хотя это привычное
        # значение из WP_Query. Для одиночного списка просто используем дефолт API.
        if status and status != "any":
            query_params["status"] = status

        result = await self.client.get(
            self.endpoint,
            query_params,
            suppress_non_retryable_log=suppress_non_retryable_log,
        )

        # WordPress API может вернуть dict или list
        if isinstance(result, list):
            return result
        return [result]

    async def exists(self, post_id: int) -> bool:
        """
        Проверить существование записи.

        Args:
            post_id: ID записи

        Returns:
            True если существует
        """
        try:
            await self.get(post_id)
            return True
        except NotFoundError:
            return False

    async def get_all(self, status: str = "any") -> List[Dict[str, Any]]:
        """
        Получить все записи (с пагинацией).

        Args:
            status: Статус записей

        Returns:
            Список всех записей
        """
        if status in ("any", "", None):
            posts_by_id: Dict[int, Dict[str, Any]] = {}

            for current_status in FETCH_ALL_STATUSES:
                try:
                    status_posts = await self._get_all_for_status(
                        current_status,
                        suppress_non_retryable_log=current_status != "publish",
                    )
                except WordPressError as e:
                    if "status" in str(e).lower():
                        logger.warning(
                            f"Статус '{current_status}' не поддерживается для {self.post_type}, пропускаем: {e}"
                        )
                        continue
                    raise

                for post in status_posts:
                    post_id = post.get("id")
                    if post_id is not None:
                        posts_by_id[post_id] = post

            all_posts = list(posts_by_id.values())
            logger.info(f"Fetched {len(all_posts)} {self.post_type} (all statuses: {', '.join(FETCH_ALL_STATUSES)})")
            return all_posts

        return await self._get_all_for_status(status)

    async def _get_all_for_status(
        self,
        status: str,
        context: Optional[str] = None,
        suppress_non_retryable_log: bool = False,
    ) -> List[Dict[str, Any]]:
        """Получить все записи одного REST-совместимого статуса."""
        all_posts = []
        page = 1
        per_page = 100

        while True:
            params = {"context": context} if context else {}
            try:
                posts = await self.list(
                    per_page=per_page,
                    page=page,
                    status=status,
                    suppress_non_retryable_log=suppress_non_retryable_log,
                    **params,
                )
            except WordPressError as e:
                if context is None and "status" in str(e).lower():
                    logger.debug(f"Retrying {self.post_type} status='{status}' with context=edit")
                    return await self._get_all_for_status(
                        status,
                        context="edit",
                        suppress_non_retryable_log=True,
                    )
                raise

            if not posts:
                break

            all_posts.extend(posts)

            # Если получили меньше чем per_page, значит это последняя страница
            if len(posts) < per_page:
                break

            page += 1

        logger.info(f"Fetched {len(all_posts)} {self.post_type} (status={status})")
        return all_posts

    async def find_by_slug(self, slug: str, status: str = "any") -> Optional[Dict[str, Any]]:
        """
        Найти запись по slug.

        Args:
            slug: URL slug записи
            status: Статус записей для поиска (any, publish, draft, etc.)

        Returns:
            Данные записи или None если не найдена
        """
        logger.debug(f"Searching {self.post_type} by slug: {slug}")

        try:
            # WordPress API поддерживает фильтр по slug
            result = await self.list(per_page=1, page=1, status=status, slug=slug)

            if result and len(result) > 0:
                post = result[0]
                logger.info(f"Found {self.post_type} ID={post['id']} with slug='{slug}'")
                return post
            else:
                logger.debug(f"No {self.post_type} found with slug='{slug}'")
                return None

        except Exception as e:
            logger.warning(f"Error searching by slug '{slug}': {e}")
            return None
