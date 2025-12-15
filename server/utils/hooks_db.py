from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import List, Optional

import psycopg2
from psycopg2 import sql
from psycopg2.extras import RealDictCursor

from .config import HooksConfig

logger = logging.getLogger(__name__)

# 模块级配置变量
_hooks_config: Optional[HooksConfig] = None


def init_hooks(cfg: HooksConfig) -> None:
    """初始化 hooks 配置"""
    global _hooks_config
    _hooks_config = cfg
    
    if not cfg.connection_string:
        raise ValueError("hooks.connection_string 配置缺失")
    if not cfg.table_name:
        raise ValueError("hooks.table_name 配置缺失")


def _get_connection():
    """获取数据库连接"""
    if not _hooks_config or not _hooks_config.connection_string:
        raise RuntimeError("hooks 配置未初始化，请确保 hooks.connection_string 和 hooks.table_name 已配置")
    
    return psycopg2.connect(_hooks_config.connection_string)


def _ms_timestamp_to_datetime(ms_timestamp_str: str) -> datetime:
    """将毫秒时间戳字符串转换为 datetime 对象"""
    try:
        ms_timestamp = int(ms_timestamp_str)
        return datetime.fromtimestamp(ms_timestamp / 1000.0, tz=UTC)
    except (ValueError, TypeError) as e:
        raise ValueError(f"无效的时间戳格式: {ms_timestamp_str}") from e


def get_full_data() -> List[str]:
    """查询所有未删除的数据，返回 content 字段列表"""
    if not _hooks_config:
        raise RuntimeError("hooks 配置未初始化")
    
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = sql.SQL("SELECT content FROM {} WHERE is_delete = false ORDER BY created_at").format(
                sql.Identifier(_hooks_config.table_name)
            )
            cur.execute(query)
            rows = cur.fetchall()
            return [row["content"] for row in rows if row["content"]]
    except Exception as e:
        logger.exception("查询全量数据失败")
        raise RuntimeError(f"查询全量数据失败: {e}") from e
    finally:
        if conn:
            conn.close()


def get_incremental_data(since_version: str) -> List[str]:
    """查询自指定版本以来的增量数据，返回 content 字段列表
    
    Args:
        since_version: UTC 毫秒时间戳字符串（如 "1704067200000"）
    
    Returns:
        content 字段列表
    """
    if not _hooks_config:
        raise RuntimeError("hooks 配置未初始化")
    
    try:
        since_datetime = _ms_timestamp_to_datetime(since_version)
    except ValueError as e:
        raise ValueError(f"无效的版本号格式: {since_version}") from e
    
    conn = None
    try:
        conn = _get_connection()
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            query = sql.SQL("SELECT content FROM {} WHERE is_delete = false AND created_at > %s ORDER BY created_at").format(
                sql.Identifier(_hooks_config.table_name)
            )
            cur.execute(query, (since_datetime,))
            rows = cur.fetchall()
            return [row["content"] for row in rows if row["content"]]
    except Exception as e:
        logger.exception("查询增量数据失败")
        raise RuntimeError(f"查询增量数据失败: {e}") from e
    finally:
        if conn:
            conn.close()
