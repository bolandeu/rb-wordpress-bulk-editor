"""
Конфигурация приложения с использованием Pydantic
"""

from pathlib import Path
from typing import List, Literal, Optional
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
    DEFAULT_SKIP_POST_TYPE_PATTERNS,
    DEFAULT_SKIP_TAXONOMY_PATTERNS,
)


class WordPressConfig(BaseModel):
    """Конфигурация WordPress API"""
    url: str = Field(..., description="URL сайта WordPress")
    username: Optional[str] = Field(default=None, description="Имя пользователя (опционально)")
    password: Optional[str] = Field(default=None, description="Application Password (опционально)")
    verify_ssl: bool = Field(default=True, description="Проверять SSL сертификаты")

    @classmethod
    def from_credentials(cls, url: str, username: str, password: str) -> "WordPressConfig":
        """Собрать config из credentials, забирая verify_ssl из .env (WP_VERIFY_SSL)."""
        return cls(
            url=url,
            username=username,
            password=password,
            verify_ssl=Settings().verify_ssl,
        )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        """Валидация и нормализация URL"""
        v = v.strip().rstrip("/")
        if not v.startswith("http://") and not v.startswith("https://"):
            v = f"https://{v}"
        # Пользователи часто вставляют полный endpoint вместо базового URL сайта
        if "/wp-json/wp/v2" in v:
            raise ValueError(
                "URL не должен содержать '/wp-json/wp/v2'. "
                "Укажите только базовый URL сайта, например: https://example.com"
            )
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
    columns_filter: Optional[List[str]] = Field(
        default=None,
        description="Фильтр колонок для отправки в API (None = все)"
    )
    skip_checks: bool = Field(
        default=False,
        description="Не проверять данные перед отправкой (preflight)"
    )


class PathsConfig(BaseModel):
    """Конфигурация путей"""
    excel_file: Path = Field(..., description="Путь к файлу данных (.xlsx или .json)")
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
    project_root: Optional[Path] = Field(
        default=None,
        description="Корень проекта (при использовании --project)"
    )

    @field_validator("excel_file")
    @classmethod
    def validate_excel_file(cls, v: Path) -> Path:
        """Проверка существования файла данных (Excel или JSON)"""
        if not v.exists():
            raise ValueError(f"Файл данных не найден: {v}")
        if v.suffix not in [".xlsx", ".xls", ".json"]:
            raise ValueError(f"Неверный формат файла: {v.suffix}. Ожидается .xlsx или .json")
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
        project_root: Optional[Path] = None,
        **kwargs
    ) -> "AppConfig":
        """Создать конфигурацию из данных Excel"""
        paths_kwargs = {"excel_file": excel_file}
        if project_root:
            paths_kwargs["media_folder"] = project_root / "media"
            paths_kwargs["content_folder"] = project_root / "content"
            paths_kwargs["logs_folder"] = project_root / "logs"
            paths_kwargs["project_root"] = project_root

        return cls(
            wordpress=WordPressConfig.from_credentials(
                url=wp_url,
                username=wp_username,
                password=wp_password,
            ),
            paths=PathsConfig(**paths_kwargs),
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

    # Паттерны для фильтрации служебных типов (через запятую в .env)
    skip_post_type_patterns: str = ""
    skip_taxonomy_patterns: str = ""

    # Override списка таксономий проекта (через запятую).
    # Если пусто — работает авто-discovery + STANDARD_TAXONOMIES/`-cat` fallback.
    taxonomies: str = ""

    # Список SEO-полей для этого проекта/плагина.
    # Dot-путь = вложенность в WP payload: `seo.title`, `meta._yoast_wpseo_title` и т.д.
    seo_fields: str = ""

    # Свои поля проекта, которые проверка перед отправкой должна считать
    # известными (через запятую). acf.*/acf_*/meta.* проверкой не трогаются.
    extra_fields: str = ""

    # Проверять SSL-сертификат WP. Для локального Docker с самоподписанным сертом: false.
    verify_ssl: bool = True

    @staticmethod
    def _parse_csv(value: str) -> List[str]:
        """Разобрать CSV-строку в список, отбрасывая пробелы и пустые элементы."""
        if not value:
            return []
        return [s.strip() for s in value.split(",") if s.strip()]

    def get_skip_post_type_patterns(self) -> List[str]:
        """Паттерны для пропуска типов записей (с fallback на DEFAULT_*)."""
        return self._parse_csv(self.skip_post_type_patterns) or list(DEFAULT_SKIP_POST_TYPE_PATTERNS)

    def get_skip_taxonomy_patterns(self) -> List[str]:
        """Паттерны для пропуска таксономий (с fallback на DEFAULT_*)."""
        return self._parse_csv(self.skip_taxonomy_patterns) or list(DEFAULT_SKIP_TAXONOMY_PATTERNS)

    def get_taxonomies(self) -> Optional[List[str]]:
        """Явный override списка таксономий. None если не задан — сигнал для fallback."""
        return self._parse_csv(self.taxonomies) or None

    def get_seo_fields(self) -> Optional[List[str]]:
        """Список SEO dot-путей. None если не задан."""
        return self._parse_csv(self.seo_fields) or None

    def get_extra_fields(self) -> List[str]:
        """Свои известные поля проекта для проверки перед отправкой."""
        return self._parse_csv(self.extra_fields)
