"""
Конфигурация приложения с использованием Pydantic
"""

from pathlib import Path
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import (
    DEFAULT_TIMEOUT,
    DEFAULT_CONCURRENCY,
    DEFAULT_BATCH_SIZE,
    DEFAULT_MAX_RETRIES,
    DEFAULT_API_TIMEOUT,
    DEFAULT_MEDIA_TIMEOUT,
    DEFAULT_RETRY_DELAY,
    DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
    DEFAULT_CIRCUIT_BREAKER_RECOVERY,
)


class WordPressConfig(BaseModel):
    """Конфигурация WordPress API"""
    url: str = Field(..., description="URL сайта WordPress")
    username: Optional[str] = Field(default=None, description="Имя пользователя (опционально)")
    password: Optional[str] = Field(default=None, description="Application Password (опционально)")
    verify_ssl: bool = Field(default=True, description="Проверять SSL сертификаты")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Валидация и нормализация URL"""
        v = v.strip().rstrip("/")
        if not v.startswith("http://") and not v.startswith("https://"):
            v = f"https://{v}"
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: Optional[str]) -> Optional[str]:
        """Убираем пробелы из application password"""
        if v is None:
            return None
        return v.replace(" ", "")


class SyncConfig(BaseModel):
    """Конфигурация синхронизации"""
    batch_size: int = Field(
        default=DEFAULT_BATCH_SIZE,
        ge=1,
        le=100,
        description="Размер батча для обработки"
    )
    max_retries: int = Field(
        default=DEFAULT_MAX_RETRIES,
        ge=0,
        le=10,
        description="Максимум повторных попыток"
    )
    timeout: int = Field(
        default=DEFAULT_TIMEOUT,
        ge=5,
        le=300,
        description="Таймаут HTTP запроса (секунды)"
    )
    concurrency: int = Field(
        default=DEFAULT_CONCURRENCY,
        ge=1,
        le=20,
        description="Количество параллельных запросов"
    )
    dry_run: bool = Field(
        default=False,
        description="Режим dry-run (без изменений)"
    )
    skip_media: bool = Field(
        default=False,
        description="Пропустить загрузку медиа"
    )
    skip_tax: bool = Field(
        default=False,
        description="Пропустить обновление таксономий (категории, теги и т.д.)"
    )
    update_excel: bool = Field(
        default=True,
        description="Записывать ID обратно в Excel"
    )
    delete_featured_media: bool = Field(
        default=False,
        description="Удалять featured media при удалении записи"
    )
    skip_existing_media: bool = Field(
        default=False,
        description="Не прикреплять существующие featured_media при update (когда img_path пустой)"
    )


class PathsConfig(BaseModel):
    """Конфигурация путей"""
    excel_file: Path = Field(..., description="Путь к Excel файлу")
    media_folder: Path = Field(
        default=Path("data/media"),
        description="Папка с медиафайлами"
    )
    content_folder: Path = Field(
        default=Path("data/content"),
        description="Папка с файлами контента"
    )
    logs_folder: Path = Field(
        default=Path("logs"),
        description="Папка для логов"
    )

    @field_validator("excel_file")
    @classmethod
    def validate_excel_file(cls, v: Path) -> Path:
        """Проверка существования Excel файла"""
        if not v.exists():
            raise ValueError(f"Excel файл не найден: {v}")
        if v.suffix not in [".xlsx", ".xls"]:
            raise ValueError(f"Неверный формат файла: {v.suffix}. Ожидается .xlsx")
        return v

    def ensure_dirs(self):
        """Создать необходимые директории"""
        self.media_folder.mkdir(parents=True, exist_ok=True)
        self.content_folder.mkdir(parents=True, exist_ok=True)
        self.logs_folder.mkdir(parents=True, exist_ok=True)


class LoggingConfig(BaseModel):
    """Конфигурация логирования"""
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = Field(
        default="INFO",
        description="Уровень логирования"
    )
    rotation: str = Field(
        default="10 MB",
        description="Ротация лог-файлов"
    )
    retention: str = Field(
        default="30 days",
        description="Хранение логов"
    )


class AppConfig(BaseModel):
    """Основная конфигурация приложения"""
    wordpress: WordPressConfig
    sync: SyncConfig = Field(default_factory=SyncConfig)
    paths: PathsConfig
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def from_excel(
        cls,
        excel_file: Path,
        wp_url: str,
        wp_username: str,
        wp_password: str,
        **kwargs
    ) -> "AppConfig":
        """Создать конфигурацию из данных Excel"""
        return cls(
            wordpress=WordPressConfig(
                url=wp_url,
                username=wp_username,
                password=wp_password
            ),
            paths=PathsConfig(excel_file=excel_file),
            **kwargs
        )


class Settings(BaseSettings):
    """Настройки из переменных окружения (опционально)"""
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="WP_",
        case_sensitive=False,
        extra="ignore"  # Игнорировать дополнительные переменные из .env
    )

    domain: Optional[str] = None
    api_user: Optional[str] = None
    api_token: Optional[str] = None

    sync_concurrency: int = DEFAULT_CONCURRENCY
    sync_batch_size: int = DEFAULT_BATCH_SIZE
    sync_timeout: int = DEFAULT_TIMEOUT

    # NEW: Async sync configuration
    api_timeout: int = DEFAULT_API_TIMEOUT
    media_timeout: int = DEFAULT_MEDIA_TIMEOUT
    max_retries: int = DEFAULT_MAX_RETRIES
    retry_delay: float = DEFAULT_RETRY_DELAY
    circuit_breaker_threshold: int = DEFAULT_CIRCUIT_BREAKER_THRESHOLD
    circuit_breaker_recovery: int = DEFAULT_CIRCUIT_BREAKER_RECOVERY

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_retention: str = "30 days"
