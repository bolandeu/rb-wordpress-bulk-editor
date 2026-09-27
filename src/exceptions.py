"""
Кастомные исключения для WordPress Bulk Editor.

Каждое исключение несёт контракт cli-builder: стабильный `code`, exit-код
процесса и опциональный `action` (что сделать, чтобы разблокироваться).
Рендерит это `src/output/` — человеку в stderr, агенту в JSON-конверт.

Таблица кодов (conventions.md, не менять произвольно):

| code                         | exit |
|------------------------------|------|
| UNEXPECTED / ABORTED / CREDENTIAL_STORE_UNAVAILABLE | 1 |
| INVALID_ARGUMENTS            | 2    |
| AUTH_REQUIRED                | 3    |
| PROJECT_NOT_INITIALIZED      | 4    |
| PROVIDER_ERROR               | 5    |
| CLI_TOO_OLD                  | 6    |
"""

from typing import Any, Dict, Optional


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
