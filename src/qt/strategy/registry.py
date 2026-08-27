"""Strategie-Registry: Name -> Klasse.

Existiert, damit die CLI Strategien ueber einen String ansprechen kann und
Phase 5 generierte Kandidaten registrieren kann, ohne die CLI anzufassen.
"""

from __future__ import annotations

from qt.strategy.base import Strategy

_REGISTRY: dict[str, type[Strategy]] = {}


def register(cls: type[Strategy]) -> type[Strategy]:
    """Als Dekorator auf eine Strategieklasse anwenden."""
    _REGISTRY[cls.name] = cls
    return cls


def get(name: str) -> type[Strategy]:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(
            f"Unbekannte Strategie {name!r}. Verfuegbar: {sorted(_REGISTRY)}"
        ) from None


def names() -> list[str]:
    return sorted(_REGISTRY)


def load_library() -> None:
    """Mitgelieferte Strategien importieren, damit sie sich registrieren."""
    from qt.strategy.library import (  # noqa: F401
        elliott,
        meanrev,
        orderflow,
        timesfm_strategy,
        trend,
    )
