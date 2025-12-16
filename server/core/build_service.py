from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

from itext2kg.atom import Atom
from itext2kg.atom.models.schemas import AtomicFact

from ..storage import StateStore, TaskConflictError, VersionedGraphStore
from ..utils import AppConfig, Hooks


logger = logging.getLogger(__name__)


def generate_version_ms() -> str:
    return str(int(time.time() * 1000))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class TriggerResult:
    task_id: str
    status: str
    version: str
    base_version: Optional[str] = None


class BuildService:
    def __init__(
        self,
        cfg: AppConfig,
        state_store: StateStore,
        graph_store: VersionedGraphStore,
        hooks: Hooks,
        atom: Atom,
        parser: Any,
    ):
        self.cfg = cfg
        self.state_store = state_store
        self.graph_store = graph_store
        self.hooks = hooks
        self.atom = atom
        self.parser = parser

    async def trigger_full_build(self) -> TriggerResult:
        version = generate_version_ms()
        task = self.state_store.try_start_task(task_type="full_build", version=version, base_version=None)
        asyncio.create_task(self._run_full_build(task_id=task.task_id, version=version))
        return TriggerResult(task_id=task.task_id, status="BUILDING", version=version)

    async def trigger_incremental_update(self, latest_ready_version: str) -> TriggerResult:
        version = generate_version_ms()
        task = self.state_store.try_start_task(
            task_type="incremental_update",
            version=version,
            base_version=latest_ready_version,
        )
        asyncio.create_task(
            self._run_incremental_update(task_id=task.task_id, version=version, base_version=latest_ready_version)
        )
        return TriggerResult(task_id=task.task_id, status="UPDATING", version=version, base_version=latest_ready_version)

    async def _extract_atomic_facts(self, texts: List[str], obs_timestamp: str) -> List[str]:
        contexts = [f"observation_date: {obs_timestamp}\n\nparagraph:\n{t.strip()}" for t in texts if t.strip()]
        if not contexts:
            return []

        output_cfg = self.cfg.raw.get("output") or {}
        output_language = str(output_cfg.get("language", "zh"))
        entity_name_mode = str(output_cfg.get("entity_name_mode", "source"))
        system_query = None
        if output_language.lower().startswith("zh") and entity_name_mode == "source":
            system_query = f"""
你是一个“原子事实（atomic facts）”抽取器。
请基于给定的 paragraph 与 observation_date 抽取事实列表，遵守以下要求：
- 输出语言使用中文。
- 涉及到的人名/机构名/术语等专有名词，必须与原文一致：不要翻译、不要拼音化、不要改写。
- 不要添加原文未明确提及的信息；不要输出解释，只输出结构化结果需要的内容。
- 时间表达如出现相对时间（如“去年/明年/上周/本月”），请结合 observation_date 转换为绝对日期。

observation_date: {obs_timestamp}
"""

        blocks = (
            await self.parser.extract_information_as_json_for_context(AtomicFact, contexts, system_query=system_query)
            if system_query
            else await self.parser.extract_information_as_json_for_context(AtomicFact, contexts)
        )

        facts: List[str] = []
        for b in blocks:
            if not b:
                continue
            for f in getattr(b, "atomic_fact", []) or []:
                s = str(f).strip()
                if s:
                    facts.append(s)
        return facts

    async def _run_full_build(self, task_id: str, version: str) -> None:
        try:
            self.state_store.update_task_progress(task_id, 1, "开始全量构建")
            texts = await asyncio.to_thread(self.hooks.get_full_data)
            if not isinstance(texts, list) or not all(isinstance(x, str) for x in texts):
                raise TypeError("hook.get_full_data() 必须返回 List[str]")
            if not texts:
                raise RuntimeError("hook.get_full_data() 返回了空数据，无法继续构建。请检查数据源是否有可用数据。")
            self.state_store.update_task_progress(task_id, 10, f"获取到 {len(texts)} 段文本")

            obs_timestamp = _now_iso()
            atomic_facts = await self._extract_atomic_facts(texts, obs_timestamp=obs_timestamp)
            if not atomic_facts:
                raise RuntimeError("未能抽取到原子事实，无法继续构图")
            self.state_store.update_task_progress(task_id, 35, f"抽取到 {len(atomic_facts)} 条原子事实")

            atom_cfg = self.cfg.raw.get("atom") or {}
            output_cfg = self.cfg.raw.get("output") or {}

            ent_threshold = float(atom_cfg.get("ent_threshold", 0.8))
            rel_threshold = float(atom_cfg.get("rel_threshold", 0.7))
            entity_name_weight = float(atom_cfg.get("entity_name_weight", 0.8))
            entity_label_weight = float(atom_cfg.get("entity_label_weight", 0.2))
            max_workers = int(atom_cfg.get("max_workers", 8))
            matching_cfg = atom_cfg.get("matching") or {}
            entity_name_mode = str(output_cfg.get("entity_name_mode", "source"))
            relation_name_mode = str(output_cfg.get("relation_name_mode", "source"))
            require_same_entity_label = bool(matching_cfg.get("require_same_entity_label", entity_name_mode == "source"))
            rename_relationship_by_embedding = bool(
                matching_cfg.get("rename_relationship_by_embedding", relation_name_mode != "source")
            )
            ontology_cfg = self.cfg.raw.get("ontology") or {}
            entity_label_cfg = (ontology_cfg.get("entity_label") or {}) if isinstance(ontology_cfg, dict) else {}
            entity_label_allowlist = entity_label_cfg.get("allowlist")
            entity_label_aliases = entity_label_cfg.get("aliases") or {}
            unknown_entity_label = str(entity_label_cfg.get("unknown_label", "unknown"))
            drop_unknown_entity_label = bool(entity_label_cfg.get("drop_unknown", False))
            debug_cfg = atom_cfg.get("debug") or {}
            debug_log_empty_relation_name = bool(debug_cfg.get("log_empty_relation_name", False))
            debug_relation_name_sample_size = int(debug_cfg.get("relation_name_sample_size", 5))
            relation_fallback_name = str(output_cfg.get("relation_fallback_name", "related_to"))

            self.state_store.update_task_progress(task_id, 45, "开始构建知识图谱")
            kg = await self.atom.build_graph(
                atomic_facts=atomic_facts,
                obs_timestamp=obs_timestamp,
                existing_knowledge_graph=None,
                ent_threshold=ent_threshold,
                rel_threshold=rel_threshold,
                entity_name_weight=entity_name_weight,
                entity_label_weight=entity_label_weight,
                max_workers=max_workers,
                output_language=str(output_cfg.get("language", "zh")),
                entity_name_mode=entity_name_mode,
                relation_name_mode=relation_name_mode,
                require_same_entity_label=require_same_entity_label,
                rename_relationship_by_embedding=rename_relationship_by_embedding,
                entity_label_allowlist=entity_label_allowlist if isinstance(entity_label_allowlist, list) else None,
                entity_label_aliases=entity_label_aliases if isinstance(entity_label_aliases, dict) else None,
                unknown_entity_label=unknown_entity_label,
                drop_unknown_entity_label=drop_unknown_entity_label,
                debug_log_empty_relation_name=debug_log_empty_relation_name,
                debug_relation_name_sample_size=debug_relation_name_sample_size,
                relation_fallback_name=relation_fallback_name,
            )
            self.state_store.update_task_progress(task_id, 75, f"构建完成：{len(kg.entities)} 节点，{len(kg.relationships)} 边")

            self.state_store.update_task_progress(task_id, 85, "写入 Neo4j")
            await asyncio.to_thread(self.graph_store.write_knowledge_graph, version, kg)

            self.state_store.update_task_progress(task_id, 95, "更新状态并清理旧版本")
            self.state_store.mark_task_success(task_id, version)
            await asyncio.to_thread(self.graph_store.cleanup_old_versions, self.cfg.retention)
            logger.info("全量构建完成 version=%s", version)
        except Exception as e:
            logger.exception("全量构建失败 version=%s", version)
            self.state_store.mark_task_failed(task_id, str(e))

    async def _run_incremental_update(self, task_id: str, version: str, base_version: str) -> None:
        try:
            self.state_store.update_task_progress(task_id, 1, "开始增量更新")
            texts = await asyncio.to_thread(self.hooks.get_incremental_data, base_version)
            if not isinstance(texts, list) or not all(isinstance(x, str) for x in texts):
                raise TypeError("hook.get_incremental_data(since_version) 必须返回 List[str]")
            if not texts:
                raise RuntimeError(f"hook.get_incremental_data(since_version={base_version}) 返回了空数据，无法继续更新。请检查自版本 {base_version} 以来是否有新的数据。")
            self.state_store.update_task_progress(task_id, 10, f"获取到 {len(texts)} 段增量文本")

            self.state_store.update_task_progress(task_id, 20, "加载基线版本图谱")
            base_kg = await asyncio.to_thread(self.graph_store.load_knowledge_graph, base_version)

            obs_timestamp = _now_iso()
            atomic_facts = await self._extract_atomic_facts(texts, obs_timestamp=obs_timestamp)
            if not atomic_facts:
                raise RuntimeError("未能抽取到原子事实，无法继续构图")
            self.state_store.update_task_progress(task_id, 45, f"抽取到 {len(atomic_facts)} 条原子事实")

            atom_cfg = self.cfg.raw.get("atom") or {}
            output_cfg = self.cfg.raw.get("output") or {}

            ent_threshold = float(atom_cfg.get("ent_threshold", 0.8))
            rel_threshold = float(atom_cfg.get("rel_threshold", 0.7))
            entity_name_weight = float(atom_cfg.get("entity_name_weight", 0.8))
            entity_label_weight = float(atom_cfg.get("entity_label_weight", 0.2))
            max_workers = int(atom_cfg.get("max_workers", 8))
            matching_cfg = atom_cfg.get("matching") or {}
            entity_name_mode = str(output_cfg.get("entity_name_mode", "source"))
            relation_name_mode = str(output_cfg.get("relation_name_mode", "source"))
            require_same_entity_label = bool(matching_cfg.get("require_same_entity_label", entity_name_mode == "source"))
            rename_relationship_by_embedding = bool(
                matching_cfg.get("rename_relationship_by_embedding", relation_name_mode != "source")
            )
            ontology_cfg = self.cfg.raw.get("ontology") or {}
            entity_label_cfg = (ontology_cfg.get("entity_label") or {}) if isinstance(ontology_cfg, dict) else {}
            entity_label_allowlist = entity_label_cfg.get("allowlist")
            entity_label_aliases = entity_label_cfg.get("aliases") or {}
            unknown_entity_label = str(entity_label_cfg.get("unknown_label", "unknown"))
            drop_unknown_entity_label = bool(entity_label_cfg.get("drop_unknown", False))
            debug_cfg = atom_cfg.get("debug") or {}
            debug_log_empty_relation_name = bool(debug_cfg.get("log_empty_relation_name", False))
            debug_relation_name_sample_size = int(debug_cfg.get("relation_name_sample_size", 5))
            relation_fallback_name = str(output_cfg.get("relation_fallback_name", "related_to"))

            self.state_store.update_task_progress(task_id, 55, "开始构建新版本图谱")
            kg = await self.atom.build_graph(
                atomic_facts=atomic_facts,
                obs_timestamp=obs_timestamp,
                existing_knowledge_graph=base_kg,
                ent_threshold=ent_threshold,
                rel_threshold=rel_threshold,
                entity_name_weight=entity_name_weight,
                entity_label_weight=entity_label_weight,
                max_workers=max_workers,
                output_language=str(output_cfg.get("language", "zh")),
                entity_name_mode=entity_name_mode,
                relation_name_mode=relation_name_mode,
                require_same_entity_label=require_same_entity_label,
                rename_relationship_by_embedding=rename_relationship_by_embedding,
                entity_label_allowlist=entity_label_allowlist if isinstance(entity_label_allowlist, list) else None,
                entity_label_aliases=entity_label_aliases if isinstance(entity_label_aliases, dict) else None,
                unknown_entity_label=unknown_entity_label,
                drop_unknown_entity_label=drop_unknown_entity_label,
                debug_log_empty_relation_name=debug_log_empty_relation_name,
                debug_relation_name_sample_size=debug_relation_name_sample_size,
                relation_fallback_name=relation_fallback_name,
            )
            self.state_store.update_task_progress(task_id, 78, f"增量构建完成：{len(kg.entities)} 节点，{len(kg.relationships)} 边")

            self.state_store.update_task_progress(task_id, 88, "写入 Neo4j")
            await asyncio.to_thread(self.graph_store.write_knowledge_graph, version, kg)

            self.state_store.update_task_progress(task_id, 95, "更新状态并清理旧版本")
            self.state_store.mark_task_success(task_id, version)
            await asyncio.to_thread(self.graph_store.cleanup_old_versions, self.cfg.retention)
            logger.info("增量更新完成 base=%s version=%s", base_version, version)
        except Exception as e:
            logger.exception("增量更新失败 base=%s version=%s", base_version, version)
            self.state_store.mark_task_failed(task_id, str(e))
