from __future__ import annotations

import importlib
import inspect
from dataclasses import dataclass
from typing import Any, Callable, List, Optional

from .config import HooksConfig


FullHook = Callable[[], List[str]]
IncrementalHook = Callable[[str], List[str]]


@dataclass(frozen=True)
class Hooks:
    get_full_data: FullHook
    get_incremental_data: IncrementalHook


def _load_attr(module_path: str, attr: str) -> Any:
    mod = importlib.import_module(module_path)
    return getattr(mod, attr)


def load_hooks(cfg: HooksConfig) -> Hooks:
    mod = importlib.import_module(cfg.module)
    
    # 如果模块有 init_hooks 函数，调用它来初始化配置
    if hasattr(mod, "init_hooks") and callable(getattr(mod, "init_hooks")):
        init_fn = getattr(mod, "init_hooks")
        init_sig = inspect.signature(init_fn)
        if len(init_sig.parameters) == 1:
            init_fn(cfg)
    
    full_fn = _load_attr(cfg.module, cfg.full)
    inc_fn = _load_attr(cfg.module, cfg.incremental)

    if not callable(full_fn):
        raise TypeError(f"hooks.full 不是可调用对象: {cfg.module}:{cfg.full}")
    if not callable(inc_fn):
        raise TypeError(f"hooks.incremental 不是可调用对象: {cfg.module}:{cfg.incremental}")

    full_sig = inspect.signature(full_fn)
    if len(full_sig.parameters) != 0:
        raise TypeError("get_full_data() 必须是无参函数，返回 List[str]")

    inc_sig = inspect.signature(inc_fn)
    if len(inc_sig.parameters) != 1:
        raise TypeError("get_incremental_data(since_version: str) 必须接收 1 个参数，返回 List[str]")

    return Hooks(get_full_data=full_fn, get_incremental_data=inc_fn)

