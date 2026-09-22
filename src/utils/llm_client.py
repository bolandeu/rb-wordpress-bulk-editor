"""
LLM Client: текст — через общую библиотеку llmkit, изображения — свой
OpenAI-compatible вызов (в llmkit генерации картинок нет).
"""

import re
import asyncio
import base64
from typing import Dict, Any, List, Optional, Tuple
import requests
from loguru import logger
import markdown
from bs4 import BeautifulSoup
from llmkit import LlmKit


class LLMError(Exception):
    """Ошибка при работе с LLM API"""
    pass


_PLACEHOLDER_RE = re.compile(r'\[([^\]]+)\]')


class LLMClient:
    """
    Клиент для генерации контента.

    Текст (`generate`) уходит в llmkit: цепочки моделей с фолбэком, ретраи по
    типу ошибки, кэш и учёт стоимости — всё это настроено в конфиге llmkit
    (`~/Library/Application Support/llmkit/config.toml`), ключи провайдеров
    берутся оттуда же / из связки ключей ОС, а не из .env этого инструмента.

    Изображения (`generate_image`) идут старым путём: endpoint + token +
    fallback между моделями вручную.
    """

    def __init__(
        self,
        endpoint: Optional[str] = None,
        token: Optional[str] = None,
        image_timeout: int = 300,
        connect_timeout: int = 10,
        tags: Optional[Dict[str, str]] = None,
        kit: Optional[LlmKit] = None,
    ):
        """
        Args:
            endpoint: URL LLM API endpoint (только для генерации изображений)
            token: API token (только для генерации изображений)
            image_timeout: Read timeout для генерации изображений (секунды)
            connect_timeout: Connect timeout (секунды)
            tags: Метки для учёта расходов в llmkit (`llm cost --by tool`)
            kit: Готовый экземпляр LlmKit (для тестов); иначе создаётся лениво
        """
        self.endpoint = endpoint
        self.token = token
        self.image_timeout = image_timeout
        self.connect_timeout = connect_timeout
        self.tags = tags or {"tool": "wp-bulk-editor"}
        self._kit = kit

    @property
    def kit(self) -> LlmKit:
        """Экземпляр llmkit (создаётся при первом обращении)."""
        if self._kit is None:
            self._kit = LlmKit(tags=self.tags)
        return self._kit

    async def generate(
        self,
        prompt: str,
        models: Optional[List[str]] = None,
        level: Optional[str] = None,
    ) -> str:
        """
        Генерировать текст через llmkit.

        Args:
            prompt: Промпт для генерации
            models: Явная цепочка моделей "провайдер/модель" (перекрывает level)
            level: Именованный уровень из конфига llmkit (cheap/judge/strong/...)

        Если не задано ни models, ни level — llmkit берёт свой уровень по
        умолчанию (`[defaults] level`). Ретраи и фолбэк — внутри llmkit,
        по его секции `[retry]`.

        Returns:
            Сгенерированный текст

        Raises:
            LLMError: Если ответа нет (все модели цепочки упали)
        """
        task_kw: Dict[str, Any] = {}
        if models:
            task_kw["chain"] = list(models)
        if level:
            task_kw["level"] = level

        target = ", ".join(models) if models else (level or "уровень по умолчанию llmkit")
        logger.info(f"Генерация текста через llmkit ({target})")

        result = await self.kit.generate(prompt, **task_kw)

        if result is None or not result.strip():
            raise LLMError(
                f"llmkit не вернул текст ({target}): все модели цепочки упали "
                f"или ответ пуст. Подробности — в логах llmkit."
            )

        logger.info(f"✓ llmkit сгенерировал текст ({len(result)} символов)")
        return result.strip()

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

                except (KeyboardInterrupt, SystemExit):
                    raise
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
                timeout=(self.connect_timeout, self.image_timeout)
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
        placeholders = _PLACEHOLDER_RE.findall(template)

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
