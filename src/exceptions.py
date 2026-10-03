"""
Кастомные исключения для WordPress Bulk Editor.

Каждое исключение несёт контракт cli-builder: стабильный `code`, exit-код
процесса и опциональный `action` (что сделать, чтобы разблокироваться).
Рендерит это `src/output/` — человеку в stderr, агенту в JSON-конверт.

Таблица кодов (conventions.md, не менять произвольно):

| code                         | exit |
|------------------------------|------|
| UNEXPECTED / ABORTED / CREDENTIAL_STORE_UNAVAILABLE | 1 |
| NOT_FOUND / CONFLICT / SEO_NOT_SUPPORTED (команды `post`, `media attach`) | 1 |
| INVALID_ARGUMENTS            | 2    |
| AUTH_REQUIRED / AUTH_MISSING / AUTH_FAILED | 3 |
| PROJECT_NOT_INITIALIZED      | 4    |
| PROVIDER_ERROR               | 5    |
| CLI_TOO_OLD                  | 6    |
"""

from typing import Any, Dict, List, Optional

# Все exit-коды CLI и их смысл — `exitCodes` в `describe --json`.
# tests/test_describe.py падает, если исключение ниже использует код не отсюда.
EXIT_CODES: Dict[int, str] = {
    0: "Успех",
    1: "Общая ошибка или ошибка данных (UNEXPECTED, ABORTED, NOT_FOUND, CONFLICT, SEO_NOT_SUPPORTED)",
    2: "Неверные аргументы или вход (INVALID_ARGUMENTS)",
    3: "Доступы: нет, неверны или недостаточно прав (AUTH_REQUIRED, AUTH_MISSING, AUTH_FAILED)",
    4: "Проект не инициализирован",
    5: "Ошибка сайта WordPress: сеть, таймаут, 5xx, 429, неразбираемый ответ (PROVIDER_ERROR)",
    6: "CLI старее, чем требует проект (CLI_TOO_OLD)",
}


class WPBulkEditorError(Exception):
    """
    Базовое исключение для всех ожидаемых ошибок приложения.

    Подклассы переопределяют `code` / `exit_code` / `default_action`.
    Всё, что не отображено сюда, `output.guard` превращает в UNEXPECTED (exit 1).
    """

    code: str = "UNEXPECTED"
    exit_code: int = 1
    default_action: Optional[str] = None

    def __init__(self, *args: Any, action: Optional[str] = None) -> None:
        super().__init__(*args)
        self.action = action or self.default_action

    @property
    def message(self) -> str:
        return str(self)

    def to_dict(self) -> Dict[str, Any]:
        """Тело поля `error` в JSON-конверте."""
        payload: Dict[str, Any] = {"code": self.code, "message": self.message}
        if self.action:
            payload["action"] = self.action
        return payload


# Протокольное имя из cli-builder: в коде команд/вывода удобнее говорить CliError.
CliError = WPBulkEditorError


class ConfigurationError(WPBulkEditorError):
    """Ошибка конфигурации"""

    code = "INVALID_ARGUMENTS"
    exit_code = 2


class ExcelError(WPBulkEditorError):
    """Базовая ошибка работы с Excel"""

    code = "INVALID_ARGUMENTS"
    exit_code = 2


class ExcelStructureError(ExcelError):
    """Ошибка структуры Excel файла"""

    pass


class ExcelValidationError(ExcelError):
    """Ошибка валидации данных Excel"""

    pass


class ProfileSheetMissingError(ExcelError):
    """Отсутствует лист profile"""

    pass


class RecordSourceError(WPBulkEditorError):
    """Ошибка источника записей (структура файла, нераспознаваемая запись)"""

    code = "INVALID_ARGUMENTS"
    exit_code = 2


class WordPressError(WPBulkEditorError):
    """Базовая ошибка WordPress API"""

    code = "PROVIDER_ERROR"
    exit_code = 5
    default_action = "Повторите позже; если ошибка повторяется — проверьте состояние сайта."

    def __init__(
        self,
        *args: Any,
        action: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[int] = None,
    ) -> None:
        super().__init__(*args, action=action)
        self.provider = provider
        self.status = status
        # Пауза из Retry-After (429/5xx) — её подхватывает async_retry.
        self.retry_after: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        payload = super().to_dict()
        if self.provider:
            payload["provider"] = self.provider
        if self.status is not None:
            payload["status"] = self.status
        return payload


class AuthenticationError(WordPressError):
    """Ошибка аутентификации"""

    code = "AUTH_REQUIRED"
    exit_code = 3
    default_action = "Укажите credentials: --username/--password или переменные окружения WP_API_USER / WP_API_TOKEN."


class NotFoundError(WordPressError):
    """Запись не найдена"""

    pass


class ServerError(WordPressError):
    """Ошибка сервера WordPress"""

    pass


class NetworkError(WordPressError):
    """Сетевая ошибка"""

    default_action = "Проверьте сеть и доступность сайта, затем повторите."


class ValidationError(WPBulkEditorError):
    """Ошибка валидации данных"""

    code = "INVALID_ARGUMENTS"
    exit_code = 2


class MediaUploadError(WordPressError):
    """Ошибка загрузки медиафайла"""

    pass


class ImageProcessingError(WPBulkEditorError):
    """Ошибка обработки изображения"""

    pass


class Aborted(WPBulkEditorError):
    """Пользователь отказался подтверждать опасную операцию"""

    code = "ABORTED"
    exit_code = 1


class CliTooOld(WPBulkEditorError):
    """Установленная версия CLI старее, чем требует проект (WP_BULK_MIN_VERSION)"""

    code = "CLI_TOO_OLD"
    exit_code = 6
    default_action = "Обновите CLI: uv tool upgrade rb-wordpress-bulk-editor"


class CredentialStoreUnavailable(WPBulkEditorError):
    """OS keychain недоступен для записи/удаления credentials"""

    code = "CREDENTIAL_STORE_UNAVAILABLE"
    exit_code = 1
    default_action = "Передайте credentials через переменные окружения (WP_API_USER / WP_API_TOKEN) вместо keychain."


class CredentialsUnhealthy(WPBulkEditorError):
    """`auth status`: обязательный доступ не в порядке или любой неверен/просрочен (стандарт §8)"""

    code = "CREDENTIALS_UNHEALTHY"
    exit_code = 3

    def __init__(self, problems: Dict[str, str]) -> None:
        listed = ", ".join(f"{provider} ({status})" for provider, status in problems.items())
        super().__init__(
            f"Доступы требуют внимания ({len(problems)}): {listed}.",
            action="Смотрите `message` каждой записи; сохранить доступ сайта — "
            "'wp-bulk auth login --provider wp:<домен>'.",
        )


# ── Команды WP-адаптера (`post upsert/get`, `media attach`) ────────────────


class AuthMissing(WPBulkEditorError):
    """Нет доступов в окружении: адаптер берёт их только из переменных окружения"""

    code = "AUTH_MISSING"
    exit_code = 3

    def __init__(self, missing: List[str], names: List[str]) -> None:
        super().__init__(
            f"Не заданы переменные окружения: {', '.join(missing)}.",
            action=f"Задайте {' и '.join(names)} (логин и Application Password сайта). "
            "Флаги, keychain и .env эти команды не читают.",
        )


class AuthFailed(AuthenticationError):
    """Сайт отверг доступы (401/403): неверный пароль или не хватает прав"""

    code = "AUTH_FAILED"
    exit_code = 3
    default_action = (
        "Проверьте WP_API_USER / WP_API_TOKEN: Application Password действующий, у пользователя роль не ниже editor."
    )


class RecordNotFound(WPBulkEditorError):
    """Запись, медиа или локальный файл, названные во входе, не существуют"""

    code = "NOT_FOUND"
    exit_code = 1


class Conflict(WPBulkEditorError):
    """Запись с таким slug уже есть и не черновик: адаптер её не трогает"""

    code = "CONFLICT"
    exit_code = 1

    def __init__(self, *args: Any, post_id: int, action: Optional[str] = None) -> None:
        super().__init__(*args, action=action)
        self.post_id = post_id

    def to_dict(self) -> Dict[str, Any]:
        return {**super().to_dict(), "post_id": self.post_id}


class SeoNotSupported(WPBulkEditorError):
    """У типа записи нет REST-поля `seo` (rb-seo выключен для типа): WordPress молча проигнорировал бы его"""

    code = "SEO_NOT_SUPPORTED"
    exit_code = 1
