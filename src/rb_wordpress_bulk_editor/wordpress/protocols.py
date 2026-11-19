"""
Protocol-интерфейсы для WordPress клиента (для тестирования и DI)
"""

from typing import Any, Dict, Optional, Protocol


class WPClientProtocol(Protocol):
    """Протокол WordPress HTTP-клиента. Позволяет заменять реализацию в тестах."""

    async def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Any: ...
    async def post(self, endpoint: str, data: Optional[Dict[str, Any]] = None, **kwargs) -> Dict[str, Any]: ...
    async def put(self, endpoint: str, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: ...
    async def delete(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]: ...
