"""
Protocol-интерфейсы для WordPress клиента (для тестирования и DI)
"""

from typing import Any, Dict, Protocol


class WPClientProtocol(Protocol):
    """Протокол WordPress HTTP-клиента. Позволяет заменять реализацию в тестах."""

    async def get(self, endpoint: str, params: Dict[str, Any] = None) -> Any: ...
    async def post(self, endpoint: str, data: Dict[str, Any] = None, **kwargs) -> Dict[str, Any]: ...
    async def put(self, endpoint: str, data: Dict[str, Any] = None) -> Dict[str, Any]: ...
    async def delete(self, endpoint: str, params: Dict[str, Any] = None) -> Dict[str, Any]: ...
