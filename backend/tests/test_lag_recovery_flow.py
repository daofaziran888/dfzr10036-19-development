"""延迟分段与恢复编排端到端场景测试（TestClient，内存仓库，不依赖外部服务）。

按值班员真实操作顺序覆盖题述完整流程：
1. 按流/采样时间/日志位点筛选一条流两代 120-150，另一条流同时间采样+缺样对照；
2. 旧代核对结论固化后从 100 恢复；新代具备本代观测和核对依据，登记前中断；
3. 中断期间同代窗口下界 100→130、上界仍覆盖 150；
4. 续办首轮带原观测退回待核并登记失效范围 120-129；
5. 重新确认 130-150 后续办，实际登记位点为 130-150；
6. 旧代结论可读回、失效范围与登记位点可查询，重复提交只派一次。
"""
from __future__ import annotations

from pathlib import Path
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.main import app  # noqa: E402


@pytest.fixture()
def client() -> TestClient:
    # 每个用例把单例内存仓库重置回种子数据，保证用例间互不污染
    from app.seed import SEED_ROWS
    from app.store import store

    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    yield TestClient(app)


def post(client: TestClient, path: str, payload: dict | None = None) -> dict:
    response = client.post(path, json=payload or {})
    assert response.status_code == 200, (path, response.status_code, response.text)
    return response.json()


def get(client: TestClient, path: str) -> dict:
    response = client.get(path)
    assert response.status_code == 200, (path, response.status_code, response.text)
    return response.json()


def test_observation_history_split_by_generation(client: TestClient) -> None:
    """两代 120-150 各自完整，同位点不同批次、不同检查点。"""
    for generation in (1, 2):
        data = get(client, "/api/lag-observations?data_stream=order_events_cdc"
                           "&sample_time=2026-11-22%2009:30:00&pos_from=120&pos_to=150"
                           f"&generation={generation}")
        assert data["total"] == 4
        assert [item["日志位点"] for item in data["items"]] == [120, 130, 140, 150]
        assert len({item["检查点编号"] for item in data["items"]}) == 1
        assert data["coverage"]["complete"] is True

    g1 = get(client, "/api/lag-observations?data_stream=order_events_cdc&pos_from=120&pos_to=120&generation=1")
    g2 = get(client, "/api/lag-observations?data_stream=order_events_cdc&pos_from=120&pos_to=120&generation=2")
    assert g1["items"][0]["批次号"] != g2["items"][0]["批次号"]
    assert g1["items"][0]["检查点编号"] != g2["items"][0]["检查点编号"]


def test_missing_sample_is_never_backfilled(client: TestClient) -> None:
    """对照流缺样位点明确暴露；无数据代次返回空，不跨代/跨流填平。"""
    pay = get(client, "/api/lag-observations?data_stream=payment_log_cdc"
                      "&sample_time=2026-11-22%2009:30:00&pos_from=120&pos_to=150&generation=1")
    assert pay["total"] == 3
    assert pay["coverage"]["missing_positions"] == [140]

    pay_g2 = get(client, "/api/lag-observations?data_stream=payment_log_cdc&pos_from=120&pos_to=180&generation=2")
    assert pay_g2["total"] == 0

    gap = get(client, "/api/lag-observations?data_stream=payment_log_cdc&pos_from=160&pos_to=180&generation=1")
    assert gap["coverage"]["missing_positions"] == [170]


def test_segment_filter_and_generation_split(client: TestClient) -> None:
    seg_order = get(client, "/api/lag-segments?data_stream=order_events_cdc"
                            "&sample_time=2026-11-22%2009:30:00&pos_from=120&pos_to=150")
    assert seg_order["total"] == 2
    assert get(client, "/api/lag-segments?data_stream=order_events_cdc&generation=1")["total"] == 1
    assert get(client, "/api/lag-segments?data_stream=order_events_cdc&generation=2")["total"] == 1

    missing = get(client, "/api/lag-segments?data_stream=payment_log_cdc")
    assert all(item["观测完整性"] == "缺样" for item in missing["items"])
    assert {item["缺失位点"] for item in missing["items"]} == {"140", "170"}


def test_review_conclusion_is_persisted_and_immutable(client: TestClient) -> None:
    r = post(client, "/api/lag-segments/1/confirm",
             {"conclusion": "旧代120-150属G1批次，仅作审计留痕，不参与本代回填"})
    assert r["ok"]
    assert r["entry"]["status"] == "已核对"
    assert r["entry"]["核对观测"] == "LOBS-0001,LOBS-0002,LOBS-0003,LOBS-0004"

    # 已固化结论不能直接改写
    r = post(client, "/api/lag-segments/1/confirm", {"conclusion": "试图改结论"})
    assert not r["ok"]
    dossier = get(client, "/api/lag-recoveries/segments/1/dossier")
    assert "G1批次" in dossier["segment"]["核对结论"]


def test_missing_sample_segment_cannot_be_confirmed(client: TestClient) -> None:
    for segment_id in (3, 4):
        r = post(client, f"/api/lag-segments/{segment_id}/confirm", {"conclusion": "尝试填平缺样"})
        assert not r["ok"]
        assert "缺采样" in r["message"]


def test_recovery_select_rejects_cross_generation_and_other_stream(client: TestClient) -> None:
    r = post(client, "/api/lag-recoveries/1/select", {"segment_ids": [1]})
    assert not r["ok"] and "跨代" in r["rejected"][0]["原因"]

    r = post(client, "/api/lag-recoveries/1/select", {"segment_ids": [3]})
    assert not r["ok"] and "数据流" in r["rejected"][0]["原因"]

    r = post(client, "/api/lag-recoveries/1/select", {"segment_ids": [2, 4]})
    assert not r["ok"]
    assert get(client, "/api/lag-recoveries/1")["entry"]["选择分段"] == ""

    # 未核对的分段即使同代也不能选择
    r = post(client, "/api/lag-recoveries/1/select", {"segment_ids": [2]})
    assert not r["ok"] and "核对结论" in r["rejected"][0]["原因"]

    # 完成本代核对后选择成功
    post(client, "/api/lag-segments/2/confirm", {"conclusion": "新代核对通过待回填"})
    r = post(client, "/api/lag-recoveries/1/select", {"segment_ids": [2]})
    assert r["ok"]
    assert get(client, "/api/lag-recoveries/1")["entry"]["选择分段"] == "2"


def test_full_interrupt_window_narrow_and_resume_flow(client: TestClient) -> None:
    # 旧代核对（审计留痕）
    post(client, "/api/lag-segments/1/confirm",
         {"conclusion": "旧代120-150属G1批次，仅作审计留痕，不参与本代回填"})
    # 新代核对并选择
    r = post(client, "/api/lag-segments/2/confirm", {"conclusion": "新代120-150本代观测完整，核对通过待回填"})
    assert r["ok"] and r["entry"]["核对观测"].startswith("LOBS-0005")
    post(client, "/api/lag-recoveries/1/select", {"segment_ids": [2]})

    # 登记前中断：选择与结论保留，无派工
    post(client, "/api/lag-recoveries/1/interrupt")
    assert get(client, "/api/lag-recoveries/1")["entry"]["中断标记"] is True
    assert get(client, "/api/lag-replays")["total"] == 0
    assert "新代120-150" in get(client, "/api/lag-recoveries/segments/2/dossier")["segment"]["核对结论"]

    # 同代窗口推进 100→130，上界仍覆盖 150
    r = post(client, "/api/lag-recoveries/1/window", {"new_lower": 130})
    assert r["ok"] and r["有效下界"] == 130 and r["有效上界"] == 200
    assert not post(client, "/api/lag-recoveries/1/window", {"new_lower": 120})["ok"]

    # 首轮续办：退回待核 + 失效范围 120-129，不直接登记
    r = post(client, "/api/lag-recoveries/1/resume")
    assert r["report"]["registered"] == []
    returned = r["report"]["returned"][0]
    assert returned["失效范围"] == "120-129"
    assert returned["待重放区间"] == "130-150"
    assert "LOBS-0005" in returned["核对观测"]
    assert returned["筛选定位"] == {
        "数据流": "order_events_cdc", "采样时间": "2026-11-22 09:30:00",
        "恢复代次": 2, "日志下界": 120, "日志上界": 150,
    }
    dossier = get(client, "/api/lag-recoveries/segments/2/dossier")
    assert dossier["segment"]["status"] == "退回待核"
    assert dossier["invalid_range"]["lower"] == 120
    assert dossier["invalid_range"]["upper"] == 129
    assert get(client, "/api/lag-replays")["total"] == 0

    # 重新确认 130-150 后续办：登记 130-150
    r = post(client, "/api/lag-segments/2/confirm",
             {"conclusion": "失效120-129已剔除，确认仅重放有效范围130-150"})
    assert r["ok"]
    r = post(client, "/api/lag-recoveries/1/resume")
    registered = r["report"]["registered"][0]
    assert registered["登记位点"] == "130-150" and registered["新建"] is True
    replays = get(client, "/api/lag-replays")["items"]
    assert len(replays) == 1
    assert replays[0]["登记位点"] == "130-150" and replays[0]["恢复代次"] == 2
    assert get(client, "/api/lag-recoveries/segments/2/dossier")["segment"]["status"] == "已登记"

    # 重复提交：只派一次
    r = post(client, "/api/lag-recoveries/1/resume")
    assert r["report"]["registered"] == []
    assert r["report"]["duplicated"][0]["派工编号"] == replays[0]["派工编号"]
    assert get(client, "/api/lag-replays")["total"] == 1

    # 已登记分段不能再核对/退回
    assert not post(client, "/api/lag-segments/2/return", {"reason": "重开"})["ok"]
    assert not post(client, "/api/lag-segments/2/confirm", {"conclusion": "改判"})["ok"]


def test_dossier_and_filter_resume_after_return(client: TestClient) -> None:
    post(client, "/api/lag-segments/1/confirm",
         {"conclusion": "旧代120-150属G1批次，仅作审计留痕，不参与本代回填"})
    post(client, "/api/lag-segments/2/confirm", {"conclusion": "新代核对通过"})
    post(client, "/api/lag-recoveries/1/select", {"segment_ids": [2]})
    post(client, "/api/lag-recoveries/1/window", {"new_lower": 130})
    post(client, "/api/lag-recoveries/1/resume")

    old = get(client, "/api/lag-recoveries/segments/1/dossier")
    assert "G1批次" in old["segment"]["核对结论"]
    assert old["dispatch"] is None
    assert all(item["恢复代次"] == 1 for item in old["observations"])

    # 退回后仍能用原筛选条件回到该分段，并读回当时观测
    back = get(client, "/api/lag-segments?data_stream=order_events_cdc&generation=2"
                       "&sample_time=2026-11-22%2009:30:00&pos_from=120&pos_to=150")
    assert any(item["id"] == 2 for item in back["items"])
    obs = get(client, "/api/lag-segments/2/observations")
    assert [item["观测编号"] for item in obs["items"]] == [
        "LOBS-0005", "LOBS-0006", "LOBS-0007", "LOBS-0008"]
