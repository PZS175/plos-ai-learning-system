"""Singleton metaclass and decorator."""

from __future__ import annotations

from functools import wraps
from typing import Any, Dict, Type, TypeVar

T = TypeVar("T")


class SingletonMeta(type):
    """Metaclass that ensures only one instance of a class exists."""

    _instances: Dict[Type[Any], Any] = {}

    def __call__(cls: Type[T], *args: Any, **kwargs: Any) -> T:
        if cls not in cls._instances:  # type: ignore[attr-defined]
            instance = super().__call__(*args, **kwargs)
            cls._instances[cls] = instance  # type: ignore[attr-defined]
        return cls._instances[cls]  # type: ignore[attr-defined]


def singleton(cls: Type[T]) -> Type[T]:
    """Class decorator implementing the singleton pattern.

    Usage::

        @singleton
        class MyClass:
            pass
    """

    @wraps(cls)
    def wrapper(*args: Any, **kwargs: Any) -> T:
        if cls not in wrapper._instances:  # type: ignore[attr-defined]
            wrapper._instances[cls] = cls(*args, **kwargs)  # type: ignore[attr-defined]
        return wrapper._instances[cls]  # type: ignore[attr-defined]

    wrapper._instances: Dict[Type[Any], Any] = {}  # type: ignore[attr-defined]
    wrapper.__wrapped__ = cls  # type: ignore[attr-defined]
    return wrapper  # type: ignore[return-value]


__all__ = ["SingletonMeta", "singleton"]
