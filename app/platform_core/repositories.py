from __future__ import annotations

from typing import Protocol, TypeVar, Generic, Iterable


T = TypeVar("T")


class Repository(Protocol, Generic[T]):
    """Minimal storage contract so domain code is not coupled to one database."""

    def get(self, resource_id: str) -> T | None: ...
    def add(self, value: T) -> None: ...
    def list_for_subject(self, subject_id: str) -> Iterable[T]: ...


class AppendOnlyRepository(Protocol, Generic[T]):
    """Contract for provenance-sensitive records that must never be overwritten."""

    def append(self, value: T) -> None: ...
    def list_for_subject(self, subject_id: str) -> Iterable[T]: ...
