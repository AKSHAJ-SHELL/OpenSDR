"""Deterministic mock LLM for tests and offline dev.

Queue exact responses with `enqueue(schema, obj)`, or register a factory with
`respond_with(schema, fn)` where fn(system, user) -> BaseModel.
"""

from collections import defaultdict, deque
from typing import Callable, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class MockLLM:
    def __init__(self):
        self._queues: dict[type, deque] = defaultdict(deque)
        self._factories: dict[type, Callable] = {}
        self.calls: list[dict] = []

    def enqueue(self, obj: BaseModel) -> None:
        self._queues[type(obj)].append(obj)

    def respond_with(self, schema: type[T], fn: Callable[[str, str], T]) -> None:
        self._factories[schema] = fn

    async def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        max_tokens: int = 1024,
        temperature: float = 0.2,
    ) -> T:
        self.calls.append({"system": system, "user": user, "schema": schema.__name__})
        if self._queues[schema]:
            return self._queues[schema].popleft()
        if schema in self._factories:
            return self._factories[schema](system, user)
        raise RuntimeError(f"MockLLM has no queued response or factory for {schema.__name__}")
