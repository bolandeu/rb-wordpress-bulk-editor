"""
LLM Client для массовой генерации контента через OpenAI-compatible API
"""

import re
import asyncio
import base64
from typing import Dict, Any, List, Optional, Tuple
import requests
from loguru import logger
import markdown
from bs4 import BeautifulSoup


class LLMError(Exception):
    """Ошибка при работе с LLM API"""
    pass


class LLMClient:
    """
    Клиент для работы с LLM API (OpenAI-compatible).

    Поддерживает:
    - Streaming ответы
    - Fallback между моделями
    - Retry с exponential backoff
    - Гибкий парсинг ответа
    """

    def __init__(
        self,
        endpoint: str,
        token: str,
        timeout: int = 120
    ):
        """
        Args:
            endpoint: URL LLM API endpoint
            token: API token
            timeout: Timeout для запросов (секунды)
        """
        self.endpoint = endpoint
        self.token = token
        self.timeout = timeout

    async def generate(
        self,
        prompt: str,
        models: List[str],
        max_retries: int = 3
    ) -> str:
        """
        Генерировать текст с fallback между моделями.

        Args:
            prompt: Промпт для генерации
            models: Список моделей (будут пробоваться по очереди)
            max_retries: Количество попыток для каждой модели

        Returns:
            Сгенерированный текст

        Raises:
            LLMError: Если все модели и попытки исчерпаны
        """
        last_error = None

        for model_idx, model in enumerate(models):
            logger.info(f"Пробуем модель {model} ({model_idx + 1}/{len(models)})")

            for attempt in range(max_retries):
                try:
                    logger.debug(f"Попытка {attempt + 1}/{max_retries} для модели {model}")

                    result = await self._call_api(prompt, model)

                    # Успех!
                    logger.info(f"✓ Модель {model} успешно сгенерировала текст (попытка {attempt + 1})")
                    return result

                except Exception as e:
                    last_error = e
                    logger.warning(f"✗ Модель {model}, попытка {attempt + 1}/{max_retries}: {e}")

                    # Если это не последняя попытка, ждем перед retry
                    if attempt < max_retries - 1:
                        delay = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                        logger.debug(f"Ждем {delay}s перед повтором...")
                        await asyncio.sleep(delay)

            # Все попытки для этой модели исчерпаны
            logger.error(f"✗ Модель {model} не смогла сгенерировать текст после {max_retries} попыток")

        # Все модели исчерпаны
        raise LLMError(
            f"Все модели ({', '.join(models)}) не смогли сгенерировать текст. "
            f"Последняя ошибка: {last_error}"
        )

    async def generate_image(
        self,
        prompt: str,
        models: List[str],
        max_retries: int = 3
    ) -> Tuple[bytes, str]:
        """
        Генерировать изображение с fallback между моделями.

        Args:
            prompt: Промпт для генерации изображения
            models: Список моделей (будут пробоваться по очереди)
            max_retries: Количество попыток для каждой модели

        Returns:
            Tuple (image_bytes, extension) - байты изображения и расширение (png/jpg)

        Raises:
            LLMError: Если все модели и попытки исчерпаны
        """
        last_error = None

        for model_idx, model in enumerate(models):
            logger.info(f"[IMG] Пробуем модель {model} ({model_idx + 1}/{len(models)})")

            for attempt in range(max_retries):
                try:
                    logger.debug(f"[IMG] Попытка {attempt + 1}/{max_retries} для модели {model}")

                    result = await asyncio.to_thread(self._call_image_api_sync, prompt, model)

                    logger.info(f"[IMG] Модель {model} успешно сгенерировала изображение (попытка {attempt + 1})")
                    return result

                except Exception as e:
                    last_error = e
                    logger.warning(f"[IMG] Модель {model}, попытка {attempt + 1}/{max_retries}: {e}")

                    if attempt < max_retries - 1:
                        delay = 2 ** attempt
                        logger.debug(f"[IMG] Ждем {delay}s перед повтором...")
                        await asyncio.sleep(delay)

            logger.error(f"[IMG] Модель {model} не смогла сгенерировать изображение после {max_retries} попыток")

        raise LLMError(
            f"Все модели ({', '.join(models)}) не смогли сгенерировать изображение. "
            f"Последняя ошибка: {last_error}"
        )

    def _call_image_api_sync(self, prompt: str, model: str) -> Tuple[bytes, str]:
        """
        Синхронный вызов LLM API для генерации изображения.

        Формат ответа: choices[0].message.images[0].image_url.url
        содержит data:image/png;base64,...

        Returns:
            Tuple (image_bytes, extension)
        """
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False
        }

        try:
            session = requests.Session()
            session.trust_env = False

            response = session.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=(10, 300)  # Генерация изображений может занять больше времени
            )

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"[IMG] API returned error {response.status_code}: {error_text}")
                raise LLMError(f"API error {response.status_code}: {error_text[:500]}")

            result = response.json()

            # Парсим ответ: choices[0].message.images[0].image_url.url
            if "choices" not in result or len(result["choices"]) == 0:
                raise LLMError(f"[IMG] Неожиданный формат ответа: {str(result)[:200]}")

            message = result["choices"][0].get("message", {})

            # Проверяем наличие images
            if "images" in message and len(message["images"]) > 0:
                image_obj = message["images"][0]

                if "image_url" in image_obj and "url" in image_obj["image_url"]:
                    data_url = image_obj["image_url"]["url"]

                    if not data_url.startswith("data:image/"):
                        raise LLMError(f"[IMG] Неизвестный формат URL: {data_url[:60]}")

                    # Определяем расширение из MIME типа
                    # data:image/png;base64,... или data:image/jpeg;base64,...
                    mime_part = data_url.split(";")[0]  # data:image/png
                    img_format = mime_part.split("/")[-1]  # png
                    ext = "jpg" if img_format == "jpeg" else img_format

                    if ";base64," not in data_url:
                        raise LLMError("[IMG] Не найден маркер ;base64, в URL")

                    _, b64_data = data_url.split(";base64,", 1)
                    image_bytes = base64.b64decode(b64_data)

                    logger.info(f"[IMG] Декодировано изображение: {len(image_bytes)} bytes, формат: {ext}")
                    return image_bytes, ext

            raise LLMError(f"[IMG] В ответе нет изображения. Ключи message: {list(message.keys())}")

        except requests.exceptions.Timeout as e:
            raise LLMError(f"[IMG] Timeout при запросе: {str(e)}")
        except requests.exceptions.RequestException as e:
            raise LLMError(f"[IMG] Network error: {str(e)}")
        except LLMError:
            raise
        except Exception as e:
            logger.exception("[IMG] Full traceback:")
            raise LLMError(f"[IMG] Unexpected error: {type(e).__name__} - {str(e)}")

    async def _call_api(self, prompt: str, model: str) -> str:
        """
        Вызвать LLM API и получить ответ (используя requests через asyncio.to_thread).

        Args:
            prompt: Промпт
            model: Название модели

        Returns:
            Сгенерированный текст

        Raises:
            LLMError: При ошибке API
        """
        # Запускаем sync запрос в отдельном потоке для async совместимости
        return await asyncio.to_thread(self._call_api_sync, prompt, model)

    def _call_api_sync(self, prompt: str, model: str) -> str:
        """
        Синхронный вызов LLM API через requests (решает проблемы с httpx+asyncio на Windows).

        Args:
            prompt: Промпт
            model: Название модели

        Returns:
            Сгенерированный текст

        Raises:
            LLMError: При ошибке API
        """
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": model,
            "messages": [
                {"role": "user", "content": prompt}
            ],
            "stream": False  # Без streaming
        }

        try:
            # Отключаем системный прокси, который вызывает ошибки
            session = requests.Session()
            session.trust_env = False  # Игнорируем системные прокси

            response = session.post(
                self.endpoint,
                headers=headers,
                json=payload,
                timeout=(10, self.timeout)  # (connect_timeout, read_timeout)
            )

            if response.status_code != 200:
                error_text = response.text
                logger.error(f"API returned error {response.status_code}: {error_text}")
                raise LLMError(
                    f"API error {response.status_code}: {error_text[:500]}"
                )

            # Парсим JSON ответ
            result = response.json()

            # Извлекаем контент из ответа (OpenAI формат)
            if "choices" in result and len(result["choices"]) > 0:
                message = result["choices"][0].get("message", {})
                content = message.get("content", "")

                if not content.strip():
                    raise LLMError("API вернул пустой ответ")

                return content.strip()
            else:
                raise LLMError(f"Неожиданный формат ответа: {str(result)[:200]}")

        except requests.exceptions.Timeout as e:
            logger.error(f"Timeout exception details: {type(e).__name__}: {str(e)}")
            raise LLMError(f"Timeout при запросе к {self.endpoint}: {str(e)}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Network error details: {type(e).__name__}: {str(e)}")
            raise LLMError(f"Network error при подключении к {self.endpoint}: {str(e)}")
        except LLMError:
            raise
        except Exception as e:
            logger.error(f"Unexpected error details: {type(e).__name__}: {str(e)}")
            logger.exception("Full traceback:")
            raise LLMError(f"Unexpected error при запросе к {self.endpoint}: {type(e).__name__} - {str(e)}")

    def _extract_content_from_chunk(self, chunk: Dict[str, Any]) -> Optional[str]:
        """
        Извлечь текст из chunk ответа.
        Поддерживает разные форматы ответа.

        Args:
            chunk: JSON chunk от API

        Returns:
            Текст или None
        """
        # Стандартный OpenAI формат (streaming)
        if "choices" in chunk:
            choices = chunk["choices"]
            if choices and len(choices) > 0:
                delta = choices[0].get("delta", {})
                if "content" in delta:
                    return delta["content"]

        # Альтернативные форматы
        # Формат 1: прямо в корне
        if "content" in chunk:
            return chunk["content"]

        # Формат 2: в message
        if "message" in chunk and isinstance(chunk["message"], dict):
            if "content" in chunk["message"]:
                return chunk["message"]["content"]

        # Формат 3: в text
        if "text" in chunk:
            return chunk["text"]

        return None

    @staticmethod
    def replace_placeholders(template: str, row_data: Dict[str, Any]) -> str:
        """
        Заменить плейсхолдеры [column_name] на значения из строки.

        Args:
            template: Шаблон с плейсхолдерами типа [title], [content] и т.д.
            row_data: Данные строки {column_name: value}

        Returns:
            Строка с заменёнными плейсхолдерами

        Example:
            >>> template = "Напиши описание для [title]"
            >>> row_data = {"title": "Услуга A"}
            >>> replace_placeholders(template, row_data)
            "Напиши описание для Услуга A"
        """
        result = template

        # Находим все плейсхолдеры [column_name]
        placeholders = re.findall(r'\[([^\]]+)\]', template)

        for placeholder in placeholders:
            # Ищем значение в row_data
            value = row_data.get(placeholder, "")

            # Конвертируем в строку и обрабатываем None/NaN
            if value is None or (isinstance(value, float) and str(value) == 'nan'):
                value = ""
            else:
                value = str(value)

            # Заменяем плейсхолдер
            result = result.replace(f"[{placeholder}]", value)

        return result

    @staticmethod
    def strip_html_tags(text: str) -> str:
        """
        Удалить HTML теги из текста.

        Args:
            text: Текст с HTML тегами

        Returns:
            Чистый текст без тегов
        """
        soup = BeautifulSoup(text, "html.parser")
        return soup.get_text()

    @staticmethod
    def markdown_to_html(text: str) -> str:
        """
        Конвертировать Markdown в HTML.

        Args:
            text: Markdown текст

        Returns:
            HTML текст
        """
        return markdown.markdown(text)

    @staticmethod
    def html_to_markdown(text: str) -> str:
        """
        Конвертировать HTML в Markdown (упрощенная версия).

        Args:
            text: HTML текст

        Returns:
            Markdown текст
        """
        # Простая конвертация через BeautifulSoup
        soup = BeautifulSoup(text, "html.parser")

        # Базовая конвертация (можно расширить)
        result = text

        # <p> -> перенос строки
        for p in soup.find_all('p'):
            p_text = p.get_text()
            result = result.replace(str(p), f"\n{p_text}\n")

        # <br> -> \n
        result = result.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')

        # <strong> и <b> -> **text**
        for tag in soup.find_all(['strong', 'b']):
            result = result.replace(str(tag), f"**{tag.get_text()}**")

        # <em> и <i> -> *text*
        for tag in soup.find_all(['em', 'i']):
            result = result.replace(str(tag), f"*{tag.get_text()}*")

        # Удаляем оставшиеся теги
        result = re.sub(r'<[^>]+>', '', result)

        return result.strip()
