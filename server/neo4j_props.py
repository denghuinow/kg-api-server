from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def props_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj

    props = getattr(obj, "_properties", None)
    if isinstance(props, dict):
        return dict(props)

    if isinstance(obj, Mapping):
        try:
            return dict(obj.items())
        except Exception:
            return {}

    items = getattr(obj, "items", None)
    if callable(items):
        try:
            return dict(items())
        except Exception:
            return {}

    try:
        return {k: obj[k] for k in obj}
    except Exception:
        return {}

