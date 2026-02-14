"""
Кастомные исключения для WordPress Bulk Editor
"""


class WPBulkEditorError(Exception):
    """Базовое исключение для всех ошибок приложения"""
    pass


class ConfigurationError(WPBulkEditorError):
    """Ошибка конфигурации"""
    pass


class ExcelError(WPBulkEditorError):
    """Базовая ошибка работы с Excel"""
    pass


class ExcelStructureError(ExcelError):
    """Ошибка структуры Excel файла"""
    pass


class ExcelValidationError(ExcelError):
    """Ошибка валидации данных Excel"""
    pass


class ProfileSheetMissingError(ExcelError):
    """Отсутствует лист profile"""
    pass


class WordPressError(WPBulkEditorError):
    """Базовая ошибка WordPress API"""
    pass


class AuthenticationError(WordPressError):
    """Ошибка аутентификации"""
    pass


class NotFoundError(WordPressError):
    """Запись не найдена"""
    pass


class ServerError(WordPressError):
    """Ошибка сервера WordPress"""
    pass


class NetworkError(WordPressError):
    """Сетевая ошибка"""
    pass


class ValidationError(WPBulkEditorError):
    """Ошибка валидации данных"""
    pass


class MediaUploadError(WordPressError):
    """Ошибка загрузки медиафайла"""
    pass


class ImageProcessingError(WPBulkEditorError):
    """Ошибка обработки изображения"""
    pass
