"""延迟历史观测服务：保留每次采样当时的检查点、恢复代次和采样时间。

历史观测一经采样不可改写，查询与复核都必须按恢复代次切分；缺采样的区间
不会补造观测行，也不允许用其他代次的观测填平。
"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class LagObservationService(BaseModuleService):
    MODULE = "lag_observation"
    ENTITY = "延迟历史观测"
    OPERATION_TERM = "采样"
    LIST_FIELDS = [
        "观测编号", "数据流", "恢复代次", "检查点编号", "检查点位点",
        "采样时间", "日志位点", "批次号", "延迟秒数", "观测完整性",
    ]
    REQUIRED_FIELDS = ["观测编号", "数据流", "恢复代次", "采样时间", "日志位点"]
    STATUS_ORDER = ["已采样"]
    ACTION_RULES: dict[str, str] = {}
    NEGATIVE_ACTIONS = []

    def find_observations(
        self,
        *,
        data_stream: str,
        sample_time: str | None = None,
        pos_from: int | None = None,
        pos_to: int | None = None,
        generation: int | None = None,
    ) -> list[dict[str, Any]]:
        """按数据流、采样时间、日志位点区间筛选历史观测，代次为空时不跨代混填。

        所有条件为合取；generation 必须显式给出才按代次过滤，返回结果严格
        限定在同一代次内，不会拿其他代次的同位点观测补齐缺样。
        """
        rows = store.rows(self.MODULE)
        matched: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("数据流", "")) != data_stream:
                continue
            if generation is not None and int(row.get("恢复代次", -1)) != generation:
                continue
            if sample_time and str(row.get("采样时间", "")) != sample_time:
                continue
            position = int(row.get("日志位点", 0))
            if pos_from is not None and position < pos_from:
                continue
            if pos_to is not None and position > pos_to:
                continue
            matched.append(dict(row))
        matched.sort(key=lambda item: (int(item.get("恢复代次", 0)), int(item.get("日志位点", 0))))
        return matched

    def coverage(self, data_stream: str, generation: int, sample_time: str,
                 lower: int, upper: int) -> dict[str, Any]:
        """统计同一代次观测对 [lower, upper] 的覆盖情况，缺样位点单独列出。"""
        observations = self.find_observations(
            data_stream=data_stream, sample_time=sample_time,
            pos_from=lower, pos_to=upper, generation=generation,
        )
        seen_positions = {int(item["日志位点"]) for item in observations}
        expected = {point for point in range(lower, upper + 1, 10)}
        missing = sorted(expected - seen_positions)
        return {
            "observations": observations,
            "covered_positions": sorted(seen_positions),
            "missing_positions": missing,
            "complete": not missing and bool(observations),
        }
