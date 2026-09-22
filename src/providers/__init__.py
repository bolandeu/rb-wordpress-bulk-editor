"""
Провайдеры внешних сервисов.

`HttpProvider` — общая база для всего, что говорит по HTTP (ретраи,
circuit breaker, маппинг статусов в иерархию исключений проекта).
`WordPressHttpProvider` — реализация для WordPress REST API.
"""

from .http import HttpProvider
from .wordpress import WordPressHttpProvider

__all__ = ["HttpProvider", "WordPressHttpProvider"]
