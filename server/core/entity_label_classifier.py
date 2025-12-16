from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pydantic import BaseModel, Field

from itext2kg.atom.models import Entity, KnowledgeGraph, Relationship


logger = logging.getLogger(__name__)


class EntityLabelPrediction(BaseModel):
    label: str = Field(description="实体类型（中文通用粗粒度类别，如 人物/组织/公司/团队/事件/概念/领域/模型 等）")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="置信度，0~1")


def normalize_entity_label(raw: str, *, unknown_label: str) -> str:
    s = str(raw or "").strip()
    if not s:
        return unknown_label
    # 统一去掉空白与常见分隔符，避免同一类型出现多个写法（如“人 物 / 人物 / 人-物”）
    s = re.sub(r"\s+", "", s)
    s = s.replace("-", "").replace("_", "")
    # 去掉常见标点（保留中文/字母/数字等）
    s = re.sub(r"""[()（）\[\]{}【】<>《》"'“”‘’.,，。;；:：!?！？/\\|·•]""", "", s)
    s = s.strip()
    if not s:
        return unknown_label
    return s[:32]


def _chunks(items: List[Any], size: int) -> Iterable[List[Any]]:
    if size <= 0:
        yield items
        return
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _collect_facts_by_entity_key(
    kg: KnowledgeGraph, *, max_facts_per_entity: int
) -> Dict[Tuple[str, str], List[str]]:
    facts_by_key: Dict[Tuple[str, str], List[str]] = defaultdict(list)

    for rel in kg.relationships:
        rf = list(getattr(getattr(rel, "properties", None), "atomic_facts", []) or [])
        if not rf:
            continue

        for ent in (rel.startEntity, rel.endEntity):
            key = (str(ent.name or ""), str(ent.label or ""))
            bucket = facts_by_key[key]
            if len(bucket) >= max_facts_per_entity:
                continue
            for f in rf:
                if len(bucket) >= max_facts_per_entity:
                    break
                fs = str(f).strip()
                if fs and fs not in bucket:
                    bucket.append(fs)

    return facts_by_key


def _build_system_query(*, hints: List[str], allow_new_labels: bool, unknown_label: str) -> str:
    hinted = ", ".join([h for h in hints if h]) if hints else ""
    extra_hint = f"常见类型参考：{hinted}。" if hinted else ""
    allow = "可以" if allow_new_labels else "不可以"
    return f"""
你是“实体类型归类器”。
目标：为每个实体输出一个类型 label，用于知识图谱的 Entity.entity_label。

要求：
- {allow}自由发明新类型，但 label 必须是中文、通用的粗粒度类别（如 人物/组织/公司/团队/事件/概念/领域/模型/方法/设备 等），尽量简短明确。
- {extra_hint}
- 遇到“团队/研究团队/项目组/课题组/某某团队/他的团队/他们的团队/该团队”等优先归为 团队。
- 如果信息不足无法判断，返回 {unknown_label}。

只输出结构化结果。
""".strip()


async def auto_classify_entity_labels(
    *,
    kg: KnowledgeGraph,
    parser: Any,
    enabled: bool,
    allow_new_labels: bool,
    unknown_label: str,
    hints: Optional[List[str]] = None,
    max_facts_per_entity: int = 6,
    batch_size: int = 80,
    drop_unknown: bool = False,
) -> None:
    if not enabled:
        return

    hints_list = [str(x).strip() for x in (hints or []) if str(x).strip()]
    if "团队" not in set(hints_list):
        hints_list.append("团队")

    facts_by_key = _collect_facts_by_entity_key(kg, max_facts_per_entity=max(0, int(max_facts_per_entity)))

    entities_by_key: Dict[Tuple[str, str], List[Entity]] = defaultdict(list)
    for e in kg.entities:
        entities_by_key[(str(e.name or ""), str(e.label or ""))].append(e)
    for r in kg.relationships:
        entities_by_key[(str(r.startEntity.name or ""), str(r.startEntity.label or ""))].append(r.startEntity)
        entities_by_key[(str(r.endEntity.name or ""), str(r.endEntity.label or ""))].append(r.endEntity)

    keys = list(entities_by_key.keys())
    if not keys:
        return

    system_query = _build_system_query(hints=hints_list, allow_new_labels=allow_new_labels, unknown_label=unknown_label)

    key_order: List[Tuple[str, str]] = []
    contexts: List[str] = []
    for name, cur_label in keys:
        fs = facts_by_key.get((name, cur_label), [])
        facts_block = "\n".join([f"- {x}" for x in fs]) if fs else "- （无）"
        contexts.append(f"实体名：{name}\n当前类型：{cur_label}\n相关事实：\n{facts_block}\n")
        key_order.append((name, cur_label))

    label_by_key: Dict[Tuple[str, str], str] = {}
    for batch_keys, batch_contexts in zip(_chunks(key_order, int(batch_size)), _chunks(contexts, int(batch_size))):
        outputs = await parser.extract_information_as_json_for_context(
            EntityLabelPrediction,
            batch_contexts,
            system_query=system_query,
        )
        if not isinstance(outputs, list) or len(outputs) != len(batch_keys):
            raise RuntimeError("实体类型归类返回数量异常，无法对齐实体列表")

        for k, out in zip(batch_keys, outputs):
            raw_label = getattr(out, "label", None) if out is not None else None
            label_by_key[k] = normalize_entity_label(str(raw_label or ""), unknown_label=unknown_label)

    for k, ents in entities_by_key.items():
        new_label = label_by_key.get(k, unknown_label)
        for e in ents:
            e.label = new_label

    existing = {e.__hash__() for e in kg.entities}
    for r in kg.relationships:
        for e in (r.startEntity, r.endEntity):
            h = e.__hash__()
            if h not in existing:
                kg.entities.append(e)
                existing.add(h)

    kg.remove_duplicates_entities()

    if drop_unknown:
        before = len(kg.relationships)
        kg.relationships = [
            r
            for r in kg.relationships
            if str(r.startEntity.label or "") != unknown_label and str(r.endEntity.label or "") != unknown_label
        ]
        logger.info("drop_unknown=true: relationships %s -> %s", before, len(kg.relationships))

    dist = Counter([str(e.label or "") for e in kg.entities])
    logger.info("实体类型归类完成：labels=%s", dict(dist.most_common(20)))
