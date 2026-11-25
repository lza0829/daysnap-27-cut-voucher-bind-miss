from __future__ import annotations

import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.services import 切点, 装载, 抽取, 回放


@pytest.fixture
def 临时库(tmp_path, monkeypatch):
    库 = tmp_path / "切点测试.db"
    monkeypatch.setattr("app.db.库路径", 库)
    from app.seed import 初始化库

    初始化库()
    return 库


@pytest.fixture
def client(临时库):
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as c:
        yield c


def _夹具流水(名: str):
    path = Path(__file__).resolve().parents[2] / "fixtures" / 名
    return json.loads(path.read_text(encoding="utf-8"))["流水"]


def test_切点前后两笔抽两日且回放一致(临时库):
    切点.切换切点("23:00", 凭证编号="test-2300")
    events = _夹具流水("切点前后夹具.json")
    r = 装载.装载流水(events, "b-cut")
    assert r["入账"] == 2
    assert [(x["流水号"], x["归属日"]) for x in r["归属明细"]] == [
        ("流-切-前-001", "2026-11-25"),
        ("流-切-后-001", "2026-11-26"),
    ]

    a1 = 切点.查询归属("流-切-前-001")
    a2 = 切点.查询归属("流-切-后-001")
    assert a1["归属日"] == "2026-11-25"
    assert a2["归属日"] == "2026-11-26"
    assert a1["valid"] is True and a1["冻结列"] == "归属日"
    assert a1["凭证编号"] == "test-2300"

    assert 抽取.日终抽取("2026-11-25")["写入"] == 3
    assert 抽取.日终抽取("2026-11-26")["写入"] == 3
    rep25 = 回放.回放核对("2026-11-25")
    rep26 = 回放.回放核对("2026-11-26")
    assert rep25["通过"] is True and rep25["差额"] == 0
    assert rep26["通过"] is True and rep26["差额"] == 0


def test_切换切点后重放旧载荷乙口径归属不变(临时库):
    events = _夹具流水("切点前后夹具.json")
    切点.切换切点("23:00", 凭证编号="old-2300")
    装载.装载流水(events, "first")

    before = {
        x: 切点.查询归属(x)["归属日"]
        for x in ("流-切-前-001", "流-切-后-001")
    }
    切点.切换切点("22:00", 凭证编号="new-2200", 原因="测试前移切点")

    
    重算 = 切点.计算归属日("2026-11-25T22:59:00Z", "22:00", "UTC")
    assert 重算 == "2026-11-26"

    replay = 装载.装载流水(events, "replay-new-batch")
    assert replay["入账"] == 0
    assert replay["幂等拒绝"] == 2

    after = {
        x: 切点.查询归属(x)["归属日"]
        for x in ("流-切-前-001", "流-切-后-001")
    }
    assert after == before
    attr = 切点.查询归属("流-切-前-001")
    assert attr["归属日"] == "2026-11-25"
    assert attr["归属可变"] is False
    assert attr["mutable"] is False
    assert attr["凭证仍当前"] is False
    assert attr["凭证"]["凭证编号"] == "old-2300"
    audit = 切点.列重放审计("流-切-前-001")
    assert audit and audit[0]["冻结归属日"] == "2026-11-25"
    assert audit[0]["凭证当前重算归属日"] == "2026-11-26"


def test_同批次编号原样幂等返回(临时库):
    events = _夹具流水("切点前后夹具.json")
    切点.切换切点("23:00")
    first = 装载.装载流水(events, "same-batch")
    second = 装载.装载流水(events, "same-batch")
    assert second["幂等"] is True
    assert second["原样返回"] is True
    assert second["入账"] == first["入账"]
    assert 切点.查询归属("流-切-前-001")["归属日"] == "2026-11-25"


def test_切点切换与同笔装载并发不得双归属(临时库):
    from app.db import 连接

    for round_id in range(12):
        serial = f"流-并发-{round_id}-{uuid.uuid4().hex[:6]}"
        
        
        切点.切换切点("23:00", 凭证编号=f"before-{round_id}-{uuid.uuid4().hex[:6]}")
        payload = [
            {
                "流水号": serial,
                "账户键": "户甲",
                "营业日": "2026-11-25",
                "发生时": "2026-11-25T22:59:30Z",
                "变动额": round_id + 1,
            }
        ]
        barrier = threading.Barrier(3)
        errors: list[Exception] = []

        def load_one():
            barrier.wait(timeout=10)
            try:
                装载.装载流水(payload, f"load-{round_id}-{uuid.uuid4().hex}")
            except Exception as e:  
                errors.append(e)

        def switch_one():
            barrier.wait(timeout=10)
            try:
                切点.切换切点(
                    "22:30",
                    凭证编号=f"switch-{round_id}-{uuid.uuid4().hex[:6]}",
                )
            except Exception as e:
                errors.append(e)

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(load_one), pool.submit(load_one), pool.submit(switch_one)]
            for f in futures:
                f.result(timeout=20)

        
        for e in errors:
            assert isinstance(e, 切点.切点并发冲突), repr(e)

        conn = 连接()
        try:
            rows = conn.execute(
                "SELECT 归属日, COUNT(*) AS c FROM 变动流水 WHERE 流水号=? GROUP BY 归属日",
                (serial,),
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) AS c FROM 变动流水 WHERE 流水号=?",
                (serial,),
            ).fetchone()["c"]
        finally:
            conn.close()

        assert total <= 1
        if total == 1:
            assert len(rows) == 1
            assert rows[0]["c"] == 1
            assert rows[0]["归属日"] in {"2026-11-25", "2026-11-26"}


def test_冻结列数据库触发器禁止改写(临时库):
    import sqlite3

    切点.切换切点("23:00")
    装载.装载流水(_夹具流水("切点前后夹具.json"), "trigger-batch")
    from app.db import 连接

    conn = 连接()
    try:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute("UPDATE 变动流水 SET 归属日='2099-01-01' WHERE 流水号='流-切-前-001'")
        conn.rollback()
    finally:
        conn.close()
    assert 切点.查询归属("流-切-前-001")["归属日"] == "2026-11-25"


def test_切点归属只读接口_and_fixture_routes(client):
    missing = client.get("/api/切点归属/流-不存在")
    assert missing.status_code == 200
    assert missing.json()["valid"] is False

    switched = client.post(
        "/api/切点凭证",
        json={"凭证编号": "api-2300", "切点时刻": "23:00", "时区": "UTC"},
    )
    assert switched.status_code == 200, switched.text

    loaded = client.post("/api/装载夹具/切点前后夹具.json")
    
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["入账"] == 2

    attr = client.get("/api/切点归属/流-切-前-001")
    assert attr.status_code == 200
    body = attr.json()
    assert body["流水号"] == "流-切-前-001"
    assert body["归属日"] == "2026-11-25"
    assert body["valid"] is True
    assert body["冻结列"] == "归属日"
    assert body["归属可变"] is False
    assert "凭证" in body

    assert client.post("/api/抽取", json={"营业日": "2026-11-25"}).status_code == 200
    assert client.post("/api/抽取", json={"营业日": "2026-11-26"}).status_code == 200
    assert client.post("/api/回放", json={"营业日": "2026-11-25"}).json()["通过"] is True
    assert client.post("/api/回放", json={"营业日": "2026-11-26"}).json()["通过"] is True

    replay = client.post("/api/装载夹具/切点重放夹具.json")
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["入账"] == 0
    assert replay_body["旁置"] == 2

    after = client.get("/api/切点归属?流水号=流-切-前-001").json()
    assert after["归属日"] == "2026-11-25"
    assert after["归属可变"] is False
    assert after["mutable"] is False

    flow_page = client.get("/api/流水").json()
    row = next(x for x in flow_page if x["流水号"] == "流-切-前-001")
    assert row["归属日"] == "2026-11-25"
    assert row["归属冻结"] == 1

    day25 = client.get("/api/流水?营业日=2026-11-25").json()
    day26 = client.get("/api/流水?营业日=2026-11-26").json()
    assert {x["流水号"] for x in day25} == {"流-切-前-001"}
    assert {x["流水号"] for x in day26} == {"流-切-后-001"}
    assert all(x["归属可变"] is False and x["mutable"] is False for x in day25 + day26)
