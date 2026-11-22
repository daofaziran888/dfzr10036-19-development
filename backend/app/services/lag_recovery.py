"""延迟恢复编排服务：读取选出的延迟分段明细，断点恢复后登记可重放段。

编排批次绑定一个数据流的一代检查点。续办时核对同代次的检查点和区间，再
决定直接登记还是带原观测退回待核：

- 分段与批次不同代次/不同检查点：拒绝选择，旧代片段只保留审计用途；
- 恢复窗口完全覆盖且核对依据齐备：直接登记重放；
- 恢复窗口收窄（源日志保留范围推进）：首次续办带原观测退回待核，并登记
  失效范围；重新确认后再次续办，按收窄后的区间登记；
- 重放登记按 (分段, 代次) 幂等，同一段重送不会多派一次。
"""
from __future__ import annotations

from typing import Any

from app.services.lag_replay import LagReplayService
from app.services.lag_segment import LagSegmentService
from app.store import store


class LagRecoveryService:
    MODULE = "lag_recovery"

    def __init__(self) -> None:
        self.segments = LagSegmentService()
        self.replays = LagReplayService()

    # ---- 批次读取与选择持久化（中断后靠这里读回） ----
    def get_recovery(self, recovery_id: int) -> dict[str, Any] | None:
        return store.find(self.MODULE, recovery_id)

    def list_recoveries(self) -> list[dict[str, Any]]:
        return [dict(row) for row in store.rows(self.MODULE)]

    def _selected_ids(self, recovery: dict[str, Any]) -> list[int]:
        raw = str(recovery.get("选择分段", "")).strip()
        return [int(part) for part in raw.split(",") if part.strip()]

    def selected_segments(self, recovery: dict[str, Any]) -> list[dict[str, Any]]:
        result = []
        for segment_id in self._selected_ids(recovery):
            segment = self.segments.get_entry(segment_id)
            if segment is not None:
                result.append(segment)
        return result

    def selected_segments_of(self, recovery_id: int) -> list[dict[str, Any]]:
        recovery = self.get_recovery(recovery_id)
        if recovery is None:
            return []
        return self.selected_segments(recovery)

    def select_segments(self, recovery_id: int, segment_ids: list[int]) -> dict[str, Any]:
        recovery = self.get_recovery(recovery_id)
        if recovery is None:
            return {"ok": False, "message": f"恢复批次 {recovery_id} 不存在"}
        if not segment_ids:
            return {"ok": False, "message": "至少选择一个延迟分段"}

        chosen: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for segment_id in segment_ids:
            segment = self.segments.get_entry(segment_id)
            if segment is None:
                rejected.append({"分段ID": segment_id, "原因": "分段不存在"})
                continue
            # 代次与检查点必须与恢复批次一致：旧代片段只有审计用途，不能进本代回填
            if str(segment["数据流"]) != str(recovery["数据流"]):
                rejected.append({"分段ID": segment_id, "分段编号": segment.get("分段编号"),
                                 "原因": "分段数据流与恢复批次不一致"})
                continue
            if int(segment["恢复代次"]) != int(recovery["恢复代次"]):
                rejected.append({"分段ID": segment_id, "分段编号": segment.get("分段编号"),
                                 "原因": f"分段属于第{segment['恢复代次']}代，"
                                         f"恢复批次为第{recovery['恢复代次']}代，不能跨代回填"})
                continue
            if str(segment["检查点编号"]) != str(recovery["检查点编号"]):
                rejected.append({"分段ID": segment_id, "分段编号": segment.get("分段编号"),
                                 "原因": "分段检查点与恢复批次检查点不一致"})
                continue
            # 同代之后再校验观测完整性：缺样段不能用其他代次数据填平
            if segment.get("观测完整性") == "缺样":
                rejected.append({"分段ID": segment_id, "分段编号": segment.get("分段编号"),
                                 "原因": "分段缺采样，缺样段不能用其他代次数据填平"})
                continue
            dispatchable, reason = self.segments.is_dispatchable(segment)
            if not dispatchable:
                rejected.append({"分段ID": segment_id, "分段编号": segment.get("分段编号"), "原因": reason})
                continue
            chosen.append(segment)

        if rejected:
            return {"ok": False, "message": "存在不满足本代恢复条件的分段，未保存选择",
                    "rejected": rejected, "chosen": [int(seg["id"]) for seg in chosen]}

        recovery["选择分段"] = ",".join(str(int(seg["id"])) for seg in chosen)
        recovery["中断标记"] = False
        recovery["最近决定"] = f"已选择 {len(chosen)} 个分段，等待断点恢复续办"
        return {"ok": True, "message": "选择已保存，中断后可按恢复批次读回",
                "segment_ids": [int(seg["id"]) for seg in chosen]}

    # ---- 中断：登记前打断，选择与核对结论都保留在内存仓库中 ----
    def interrupt(self, recovery_id: int) -> dict[str, Any]:
        recovery = self.get_recovery(recovery_id)
        if recovery is None:
            return {"ok": False, "message": f"恢复批次 {recovery_id} 不存在"}
        recovery["中断标记"] = True
        recovery["最近决定"] = "实际登记前中断，选择与核对结论已保留"
        return {"ok": True, "message": "已标记中断，续办时沿用原选择"}

    # ---- 源日志保留范围在同一代次内推进 ----
    def advance_window(self, recovery_id: int, new_lower: int,
                       new_upper: int | None = None) -> dict[str, Any]:
        recovery = self.get_recovery(recovery_id)
        if recovery is None:
            return {"ok": False, "message": f"恢复批次 {recovery_id} 不存在"}
        old_lower = int(recovery["有效下界"])
        old_upper = int(recovery["有效上界"])
        if new_lower < old_lower:
            return {"ok": False, "message": "日志保留下界只能向前推进，不能回退"}
        upper = old_upper if new_upper is None else new_upper
        selected = self.selected_segments(recovery)
        if selected and upper < max(int(seg["日志上界"]) for seg in selected):
            return {"ok": False, "message": "上界调整后不再覆盖已选分段，拒绝收窄"}
        recovery["有效下界"] = new_lower
        recovery["有效上界"] = upper
        recovery["最近决定"] = f"有效恢复窗口推进为 {new_lower}-{upper}（仍属第{recovery['恢复代次']}代）"
        return {"ok": True, "message": recovery["最近决定"],
                "有效下界": new_lower, "有效上界": upper}

    # ---- 续办：沿用原选择，逐段核对并登记 ----
    def resume(self, recovery_id: int) -> dict[str, Any]:
        recovery = self.get_recovery(recovery_id)
        if recovery is None:
            return {"ok": False, "message": f"恢复批次 {recovery_id} 不存在"}
        segment_ids = self._selected_ids(recovery)
        if not segment_ids:
            return {"ok": False, "message": "恢复批次没有已保存的分段选择"}

        window_lower = int(recovery["有效下界"])
        window_upper = int(recovery["有效上界"])
        report: dict[str, list[dict[str, Any]]] = {
            "registered": [], "returned": [], "invalid": [], "duplicated": [],
        }

        for segment_id in segment_ids:
            segment = self.segments.get_entry(segment_id)
            if segment is None:
                continue
            # 同代次检查点与区间复核
            if (int(segment["恢复代次"]) != int(recovery["恢复代次"])
                    or str(segment["检查点编号"]) != str(recovery["检查点编号"])):
                report["invalid"].append({"分段编号": segment.get("分段编号"),
                                          "原因": "代次或检查点已不匹配"})
                continue

            # 幂等：同一段同代次已派工的，重送直接读回，不再登记
            existing = self.replays.find_dispatch(int(segment["id"]), int(recovery["恢复代次"]))
            if existing is not None:
                report["duplicated"].append({"分段编号": segment["分段编号"],
                                             "派工编号": existing["派工编号"],
                                             "登记位点": existing["登记位点"]})
                continue

            window = self.segments.evaluate_against_window(segment, window_lower, window_upper)
            if window["replay_lower"] is None:
                segment["status"] = "已失效"
                segment["pending"] = False
                segment["abnormal"] = True
                self.segments.mark_invalid(
                    segment, int(segment["日志下界"]), int(segment["日志上界"]))
                report["invalid"].append({"分段编号": segment["分段编号"],
                                          "失效范围": f"{segment['日志下界']}-{segment['日志上界']}"})
                continue

            dispatchable, reason = self.segments.is_dispatchable(segment)
            already_adjudicated = bool(segment.get("失效上界") not in (None, ""))
            if not window["fully_covered"] and not already_adjudicated:
                # 窗口首次收窄：登记失效范围，带原观测退回待核，不直接登记
                self.segments.mark_invalid(
                    segment, int(window["invalid_lower"]), int(window["invalid_upper"]))
                returned, _ = self.segments.return_for_recheck(
                    int(segment["id"]),
                    f"可回放下界由 {segment['检查点位点']} 推进到 {window_lower}，"
                    f"原核对区间 {segment['日志下界']}-{segment['日志上界']} 需重新确认",
                )
                report["returned"].append({
                    "分段编号": segment["分段编号"],
                    "失效范围": f"{window['invalid_lower']}-{window['invalid_upper']}",
                    "待重放区间": f"{window['replay_lower']}-{window['replay_upper']}",
                    "核对观测": returned.get("核对观测", ""),
                    "筛选定位": {
                        "数据流": segment["数据流"], "采样时间": segment["采样时间"],
                        "恢复代次": segment["恢复代次"],
                        "日志下界": segment["日志下界"], "日志上界": segment["日志上界"],
                    },
                })
                continue
            if not dispatchable:
                returned, _ = self.segments.return_for_recheck(int(segment["id"]), reason)
                report["returned"].append({
                    "分段编号": segment["分段编号"], "原因": reason,
                    "核对观测": returned.get("核对观测", ""),
                })
                continue

            record, created = self.replays.dispatch(
                segment=segment, recovery=recovery,
                replay_lower=int(window["replay_lower"]),
                replay_upper=int(window["replay_upper"]),
            )
            report["registered"].append({
                "分段编号": segment["分段编号"], "派工编号": record["派工编号"],
                "登记位点": record["登记位点"], "新建": created,
            })

        recovery["中断标记"] = False
        recovery["最近决定"] = (
            f"续办完成：登记 {len(report['registered'])} 段，退回 {len(report['returned'])} 段，"
            f"失效 {len(report['invalid'])} 段，重送去重 {len(report['duplicated'])} 段"
        )
        return {"ok": True, "message": recovery["最近决定"], "report": report,
                "window": {"有效下界": window_lower, "有效上界": window_upper}}

    # ---- 复核查询：旧代结论、失效范围、实际登记位点 ----
    def review_dossier(self, segment_id: int) -> dict[str, Any]:
        segment = self.segments.get_entry(segment_id)
        if segment is None:
            return {"ok": False, "message": f"分段 {segment_id} 不存在"}
        dispatch = self.replays.find_dispatch(int(segment["id"]), int(segment["恢复代次"]))
        observations = self.segments.segment_observations(segment)
        return {
            "ok": True,
            "segment": dict(segment),
            "observations": observations,
            "invalid_range": {
                "lower": segment.get("失效下界"),
                "upper": segment.get("失效上界"),
                "note": segment.get("失效说明", ""),
            },
            "dispatch": dict(dispatch) if dispatch else None,
        }
