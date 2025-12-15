from __future__ import annotations

import sys
from pathlib import Path


class FakeRelationship:
    def __init__(self) -> None:
        self.type = "REL"
        self._properties = {"predicate": "related_to", "kg_version": "v1", "atomic_facts": ["a"]}

    def __iter__(self):
        return iter(self._properties.keys())

    def __getitem__(self, key: str):
        return self._properties[key]


def main() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(repo_root / "kg-api-server"))

    from server.neo4j_props import props_dict

    r = FakeRelationship()
    try:
        dict(r)
        raise SystemExit("预期 dict(r) 失败，但却成功了（请检查 Python/实现差异）")
    except Exception:
        pass

    props = props_dict(r)
    assert props["predicate"] == "related_to"
    assert props["kg_version"] == "v1"
    assert props["atomic_facts"] == ["a"]
    print("OK")


if __name__ == "__main__":
    main()
