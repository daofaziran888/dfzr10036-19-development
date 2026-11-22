"""延迟分段与恢复编排路由：历史观测查询、分段核对、断点恢复续办与重放登记。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from app.schemas import ReviewPayload, SegmentSelectPayload, WindowPayload
from app.services.lag_observation import LagObservationService
from app.services.lag_recovery import LagRecoveryService
from app.services.lag_replay import LagReplayService
from app.services.lag_segment import LagSegmentService

router = APIRouter(prefix="/api", tags=["延迟恢复编排"])

observation_service = LagObservationService()
segment_service = LagSegmentService()
recovery_service = LagRecoveryService()
replay_service = LagReplayService()


def _int_param(value: int | None) -> int | None:
    return None if value is None else int(value)


# ---- 历史延迟观测：按数据流、采样时间、日志位点、代次查询，缺样不填平 ----
@router.get("/lag-observations")
def query_observations(
    data_stream: str = Query(..., description="数据流"),
    sample_time: str | None = Query(default=None),
    pos_from: int | None = Query(default=None),
    pos_to: int | None = Query(default=None),
    generation: int | None = Query(default=None, description="恢复代次，必须显式按代切分"),
) -> dict[str, Any]:
    items = observation_service.find_observations(
        data_stream=data_stream, sample_time=sample_time,
        pos_from=_int_param(pos_from), pos_to=_int_param(pos_to),
        generation=_int_param(generation),
    )
    coverage = None
    if pos_from is not None and pos_to is not None and generation is not None:
        coverage = observation_service.coverage(
            data_stream, int(generation), sample_time or "", int(pos_from), int(pos_to))
        coverage.pop("observations", None)
    return {"ok": True, "items": items, "total": len(items), "coverage": coverage}


# ---- 延迟分段明细：按流、采样时间、日志位点筛选，支持按代次切分 ----
@router.get("/lag-segments")
def query_segments(
    data_stream: str | None = Query(default=None),
    sample_time: str | None = Query(default=None),
    pos_from: int | None = Query(default=None),
    pos_to: int | None = Query(default=None),
    generation: int | None = Query(default=None),
) -> dict[str, Any]:
    items = segment_service.list_segments(
        data_stream=data_stream, sample_time=sample_time,
        pos_from=_int_param(pos_from), pos_to=_int_param(pos_to),
        generation=_int_param(generation),
    )
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/lag-segments/{segment_id}/observations")
def segment_observations(segment_id: int) -> dict[str, Any]:
    segment = segment_service.get_entry(segment_id)
    if segment is None:
        return {"ok": False, "message": f"延迟分段 {segment_id} 不存在或已归档"}
    items = segment_service.segment_observations(segment)
    return {"ok": True, "items": items, "total": len(items),
            "segment_id": segment_id, "观测完整性": segment.get("观测完整性")}


@router.post("/lag-segments/{segment_id}/confirm")
def confirm_segment(segment_id: int, payload: ReviewPayload) -> dict[str, Any]:
    entry, message = segment_service.confirm_review(segment_id, payload.conclusion)
    return {"ok": entry is not None, "message": message, "entry": entry}


@router.post("/lag-segments/{segment_id}/return")
def return_segment(segment_id: int, payload: ReviewPayload) -> dict[str, Any]:
    entry, message = segment_service.return_for_recheck(segment_id, payload.reason)
    return {"ok": entry is not None, "message": message, "entry": entry}


# ---- 恢复编排批次 ----
@router.get("/lag-recoveries")
def list_recoveries() -> dict[str, Any]:
    items = recovery_service.list_recoveries()
    return {"ok": True, "items": items, "total": len(items)}


# 静态路径需在 /lag-recoveries/{recovery_id} 之前注册，避免被动态路径抢先匹配
@router.get("/lag-recoveries/segments/{segment_id}/dossier")
def segment_dossier(segment_id: int) -> dict[str, Any]:
    return recovery_service.review_dossier(segment_id)


@router.get("/lag-recoveries/{recovery_id}")
def get_recovery(recovery_id: int) -> dict[str, Any]:
    recovery = recovery_service.get_recovery(recovery_id)
    if recovery is None:
        return {"ok": False, "message": f"恢复批次 {recovery_id} 不存在"}
    return {"ok": True, "entry": recovery,
            "segments": recovery_service.selected_segments_of(recovery_id)}


@router.post("/lag-recoveries/{recovery_id}/select")
def select_segments(recovery_id: int, payload: SegmentSelectPayload) -> dict[str, Any]:
    return recovery_service.select_segments(recovery_id, payload.segment_ids)


@router.post("/lag-recoveries/{recovery_id}/interrupt")
def interrupt_recovery(recovery_id: int) -> dict[str, Any]:
    return recovery_service.interrupt(recovery_id)


@router.post("/lag-recoveries/{recovery_id}/window")
def advance_window(recovery_id: int, payload: WindowPayload) -> dict[str, Any]:
    return recovery_service.advance_window(recovery_id, payload.new_lower, payload.new_upper)


@router.post("/lag-recoveries/{recovery_id}/resume")
def resume_recovery(recovery_id: int) -> dict[str, Any]:
    return recovery_service.resume(recovery_id)


# ---- 重放派工登记查询：核对同一段是否只派一次 ----
@router.get("/lag-replays")
def list_replays(
    data_stream: str | None = Query(default=None),
    generation: int | None = Query(default=None),
) -> dict[str, Any]:
    items = replay_service.list_dispatches(
        data_stream=data_stream, generation=_int_param(generation))
    return {"ok": True, "items": items, "total": len(items)}
