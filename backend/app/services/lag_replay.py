"""重放派工登记服务：恢复编排实际登记的可重放段。

一个延迟分段在一次恢复代次内最多派工一次：重复提交直接读回已登记的
派工记录，不会新建第二条。
"""
from __future__ import annotations

from typing import Any

from app.store import store


class LagReplayService:
    MODULE = "lag_replay"

    def _next_id(self) -> int:
        rows = store.rows(self.MODULE)
        return max((int(row.get("id", 0)) for row in rows), default=0) + 1

    def find_dispatch(self, segment_id: int, generation: int) -> dict[str, Any] | None:
        for row in store.rows(self.MODULE):
            if int(row.get("分段ID", -1)) == segment_id and int(row.get("恢复代次", -1)) == generation:
                return row
        return None

    def dispatch(
        self,
        *,
        segment: dict[str, Any],
        recovery: dict[str, Any],
        replay_lower: int,
        replay_upper: int,
    ) -> tuple[dict[str, Any], bool]:
        """登记可重放段。返回 (派工记录, 是否本次新建)；重送时复用原记录。"""
        segment_id = int(segment["id"])
        generation = int(recovery["恢复代次"])
        existing = self.find_dispatch(segment_id, generation)
        if existing is not None:
            return existing, False

        rows = store.rows(self.MODULE)
        new_id = self._next_id()
        record = {
            "id": new_id,
            "status": "已派工",
            "pending": True,
            "abnormal": False,
            "派工编号": f"LRP-{new_id:04d}",
            "编排编号": recovery["编排编号"],
            "分段编号": segment["分段编号"],
            "分段ID": segment_id,
            "数据流": segment["数据流"],
            "恢复代次": generation,
            "检查点编号": segment["检查点编号"],
            "重放下界": replay_lower,
            "重放上界": replay_upper,
            "登记位点": f"{replay_lower}-{replay_upper}",
            "采样时间": segment["采样时间"],
            "核对结论": segment.get("核对结论", ""),
        }
        rows.append(record)

        segment["派工编号"] = record["派工编号"]
        segment["登记位点"] = record["登记位点"]
        segment["status"] = "已登记"
        segment["pending"] = False
        segment["abnormal"] = False
        return record, True

    def list_dispatches(self, *, data_stream: str | None = None,
                        generation: int | None = None) -> list[dict[str, Any]]:
        rows = store.rows(self.MODULE)
        matched = []
        for row in rows:
            if data_stream and str(row.get("数据流", "")) != data_stream:
                continue
            if generation is not None and int(row.get("恢复代次", -1)) != generation:
                continue
            matched.append(dict(row))
        return matched
