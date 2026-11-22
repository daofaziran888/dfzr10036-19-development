"""延迟分段明细服务：值班员圈定的待核对回放区间。

分段按 (数据流, 恢复代次) 切分，保留圈定时的检查点、代次和采样时间。
核对结论持久化后可在中断后读回；退回待核时保留当时观测定位，值班员能
回到筛选结果继续处理。只有仍处于有效恢复范围内、且本代观测与核对依据
齐备的分段才允许进入回填；同一段重送在派工登记处做幂等，不会多派一次。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.base import BaseModuleService
from app.services.lag_observation import LagObservationService
from app.store import store


class LagSegmentService(BaseModuleService):
    MODULE = "lag_segment"
    ENTITY = "延迟分段"
    OPERATION_TERM = "核对"
    LIST_FIELDS = [
        "分段编号", "数据流", "恢复代次", "检查点编号", "检查点位点",
        "日志下界", "日志上界", "采样时间", "观测完整性", "缺失位点",
        "核对结论", "核对时间", "核对观测", "派工编号", "登记位点",
    ]
    REQUIRED_FIELDS = ["分段编号", "数据流", "恢复代次", "日志下界", "日志上界", "采样时间"]
    STATUS_ORDER = ["待核对", "已核对", "退回待核", "已登记", "已失效"]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS = []

    def __init__(self) -> None:
        self.observations = LagObservationService()

    # ---- 筛选：按数据流、采样时间、日志位点（区间）定位，代次显式切分 ----
    def list_segments(
        self,
        *,
        data_stream: str | None = None,
        sample_time: str | None = None,
        pos_from: int | None = None,
        pos_to: int | None = None,
        generation: int | None = None,
    ) -> list[dict[str, Any]]:
        rows = store.rows(self.MODULE)
        matched: list[dict[str, Any]] = []
        for row in rows:
            if data_stream and str(row.get("数据流", "")) != data_stream:
                continue
            if generation is not None and int(row.get("恢复代次", -1)) != generation:
                continue
            if sample_time and str(row.get("采样时间", "")) != sample_time:
                continue
            if pos_from is not None and int(row.get("日志上界", 0)) < pos_from:
                continue
            if pos_to is not None and int(row.get("日志下界", 0)) > pos_to:
                continue
            matched.append(dict(row))
        matched.sort(key=lambda item: (str(item.get("数据流", "")), int(item.get("恢复代次", 0)),
                                       int(item.get("日志下界", 0))))
        return matched

    def segment_observations(self, segment: dict[str, Any]) -> list[dict[str, Any]]:
        """读取分段当时（同代次、同采样时间、同位点区间）的历史观测。"""
        return self.observations.find_observations(
            data_stream=str(segment["数据流"]),
            sample_time=str(segment["采样时间"]),
            pos_from=int(segment["日志下界"]),
            pos_to=int(segment["日志上界"]),
            generation=int(segment["恢复代次"]),
        )

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # ---- 核对：结论与当时观测一起固化，中断后可直接读回 ----
    def confirm_review(self, entry_id: int, conclusion: str) -> tuple[dict[str, Any] | None, str]:
        segment = self.get_entry(entry_id)
        if segment is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if segment.get("status") == "已登记":
            return None, "分段已登记重放，核对结论不再变更"
        if segment.get("status") == "已失效":
            return None, "分段已超出有效恢复范围，不能核对"
        if segment.get("status") == "已核对":
            return None, "核对结论已固化，如需变更须先退回待核"
        if not conclusion.strip():
            return None, "核对结论不能为空"

        observations = self.segment_observations(segment)
        if segment.get("观测完整性") == "缺样" or any(
            str(item.get("观测完整性", "")) == "缺样" for item in observations
        ):
            return None, "分段存在缺采样，不能用其他代次数据填平，需退回待核补采"

        segment["核对结论"] = conclusion.strip()
        segment["核对时间"] = self._now()
        segment["核对观测"] = ",".join(str(item["观测编号"]) for item in observations)
        segment["status"] = "已核对"
        segment["pending"] = True
        return segment, "核对结论已固化，中断后可按分段读回"

    # ---- 退回待核：保留当时观测定位，值班员可回到筛选结果继续处理 ----
    def return_for_recheck(self, entry_id: int, reason: str) -> tuple[dict[str, Any] | None, str]:
        segment = self.get_entry(entry_id)
        if segment is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        if segment.get("status") == "已登记":
            return None, "分段已登记重放，不能退回"
        observations = self.segment_observations(segment)
        segment["status"] = "退回待核"
        segment["pending"] = True
        segment["abnormal"] = True
        segment["核对结论"] = ""
        segment["核对时间"] = ""
        # 观测定位不清除：退回后仍能找到当时的历史观测
        segment["核对观测"] = ",".join(str(item["观测编号"]) for item in observations)
        segment["退回原因"] = reason.strip() or "恢复窗口推进，需按本代观测重新核对"
        return segment, "已带原观测退回待核，可按原筛选条件继续处理"

    # ---- 有效恢复范围判断：只允许仍属于有效范围的分段进入回填 ----
    def evaluate_against_window(self, segment: dict[str, Any],
                                window_lower: int, window_upper: int) -> dict[str, Any]:
        lower = int(segment["日志下界"])
        upper = int(segment["日志上界"])
        clipped_lower = max(lower, window_lower)
        fully_covered = lower >= window_lower and upper <= window_upper
        has_replay = clipped_lower <= upper and clipped_lower <= window_upper
        return {
            "window_lower": window_lower,
            "window_upper": window_upper,
            "fully_covered": fully_covered,
            "replay_lower": clipped_lower if has_replay else None,
            "replay_upper": min(upper, window_upper) if has_replay else None,
            "invalid_lower": lower if lower < window_lower else None,
            "invalid_upper": min(window_lower - 1, upper) if lower < window_lower else None,
        }

    def mark_invalid(self, segment: dict[str, Any], invalid_lower: int, invalid_upper: int) -> None:
        """把被恢复窗口下界越过的部分标记为失效范围（审计保留，不参与回填）。"""
        segment["失效下界"] = invalid_lower
        segment["失效上界"] = invalid_upper
        segment["失效说明"] = f"位点 {invalid_lower}-{invalid_upper} 已落在有效恢复范围之外"

    def is_dispatchable(self, segment: dict[str, Any]) -> tuple[bool, str]:
        """进入回填前的硬性前置：本代观测完整、核对依据齐备、未登记、未失效。"""
        if segment.get("status") == "已登记":
            return False, "分段已登记，重送不会重复派工"
        if segment.get("status") == "已失效":
            return False, "分段已失效，不能进入回填"
        if segment.get("观测完整性") == "缺样":
            return False, "分段缺采样，缺样段不能用其他代次数据填平"
        if not str(segment.get("核对结论", "")).strip():
            return False, "缺少本代核对结论，需先完成核对"
        observations = self.segment_observations(segment)
        if not observations:
            return False, "缺少本代历史观测，不能进入回填"
        positions = {int(item["日志位点"]) for item in observations}
        expected = set(range(int(segment["日志下界"]), int(segment["日志上界"]) + 1, 10))
        if not expected.issubset(positions):
            return False, "本代观测未覆盖分段全部位点，需退回待核"
        return True, ""
