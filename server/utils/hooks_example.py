from __future__ import annotations

from typing import List


def get_full_data() -> List[str]:
    return [
        "示例文本：请在 config.yaml 中将 hooks 指向你自己的实现。",
    ]


def get_incremental_data(since_version: str) -> List[str]:
    _ = since_version
    return [
        "示例增量文本：请在 config.yaml 中将 hooks 指向你自己的实现。",
    ]

