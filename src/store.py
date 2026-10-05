"""事件存储：按 event_id 幂等去重，跨院重发不产生重复效果。"""

from __future__ import annotations

import json

from .timeutil import parse


class EventConflictError(ValueError):
    """同一 event_id 携带不同内容，违反去重约定。"""


def _canonical(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False, sort_keys=True)


class EventStore:
    """最小事件存储：保证去重、冲突检测与稳定读取顺序。"""

    def __init__(self) -> None:
        self._by_id: dict[str, dict] = {}

    def receive(self, event: dict) -> bool:
        """接收事件。返回 True 表示新事件；重复事件幂等忽略并返回 False。"""
        event_id = event["event_id"]
        existing = self._by_id.get(event_id)
        if existing is not None:
            if _canonical(existing) != _canonical(event):
                raise EventConflictError(f"event_id 冲突：{event_id}")
            return False
        self._by_id[event_id] = event
        return True

    def get(self, event_id: str) -> dict | None:
        return self._by_id.get(event_id)

    def events(self, aggregate_type: str | None = None, aggregate_id: str | None = None) -> list[dict]:
        """按 (occurred_at, version, event_id) 稳定排序返回。

        同一秒到达的事件以聚合内 version 还原因果顺序（如采集先于转送、
        合并先于撤销），保证各院投影一致。
        """
        selected = [
            event
            for event in self._by_id.values()
            if (aggregate_type is None or event["aggregate_type"] == aggregate_type)
            and (aggregate_id is None or event["aggregate_id"] == aggregate_id)
        ]
        return sorted(selected, key=lambda e: (parse(e["occurred_at"]), e.get("version", 0), e["event_id"]))

    def next_version(self, aggregate_type: str, aggregate_id: str) -> int:
        return len(self.events(aggregate_type, aggregate_id)) + 1

    def __len__(self) -> int:
        return len(self._by_id)
