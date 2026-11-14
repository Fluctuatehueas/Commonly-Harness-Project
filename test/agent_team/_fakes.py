"""AgentTeam 单测用的内存 Store 替身（不依赖 PostgreSQL）。"""

from __future__ import annotations

from typing import Any


class FakeItem:
    def __init__(self, key: str, value: dict[str, Any]) -> None:
        self.key = key
        self.value = value


class FakeStore:
    """只实现 AgentProfileStore / AgentGroupStore 用到的最小接口。"""

    def __init__(self) -> None:
        self._data: dict[tuple[str, ...], dict[str, dict[str, Any]]] = {}

    async def aget(self, namespace: tuple[str, ...], key: str) -> FakeItem | None:
        value = self._data.get(tuple(namespace), {}).get(key)
        return FakeItem(key, dict(value)) if value is not None else None

    async def aput(
        self,
        namespace: tuple[str, ...],
        key: str,
        value: dict[str, Any],
        index: bool = False,
    ) -> None:
        self._data.setdefault(tuple(namespace), {})[key] = dict(value)

    async def adelete(self, namespace: tuple[str, ...], key: str) -> None:
        self._data.get(tuple(namespace), {}).pop(key, None)

    async def asearch(
        self,
        namespace: tuple[str, ...],
        limit: int = 10,
        offset: int = 0,
        filter: dict[str, Any] | None = None,
    ) -> list[FakeItem]:
        items = list(self._data.get(tuple(namespace), {}).items())
        return [FakeItem(key, dict(value)) for key, value in items[offset : offset + limit]]
