import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.db import 夹具目录, 连接
from app.services import 回放, 抽取, 目录, 装载, 切点

路由 = APIRouter(prefix="/api")


def _切点失败响应(e: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(e),
            "valid": False,
            "失败闭": True,
            "架构": 切点.架构,
            "归属可变": False,
        },
    )


class 装载请求(BaseModel):
    model_config = {"extra": "allow", "populate_by_name": True}

    批次编号: str = ""
    batchId: str = ""
    batch_id: str = ""
    流水: list = Field(default_factory=list)
    事件: list = Field(default_factory=list)
    events: list = Field(default_factory=list)
    rows: list = Field(default_factory=list)
    items: list = Field(default_factory=list)
    切点时刻: str | None = None
    cutoffTime: str | None = None
    切点: Any | None = None
    切点凭证: dict[str, Any] | None = None
    装载前切换切点: Any | None = None


class 营业日请求(BaseModel):
    model_config = {"extra": "allow", "populate_by_name": True}

    营业日: str = ""
    businessDate: str = ""
    business_date: str = ""
    date: str = ""
    批次编号: str = ""
    batchId: str = ""
    batch_id: str = ""

    def 日(self) -> str:
        return self.营业日 or self.businessDate or self.business_date or self.date

    def 批次(self) -> str:
        return self.批次编号 or self.batchId or self.batch_id


@路由.get("/健康")
def 健康():
    return {
        "正常": True,
        "项目": "日镜账",
        "骨架": "日终余额抽取→周期快照落行→变动流水回放核对→断档跳变检出→快照与回放差额清单",
    }


@路由.get("/总览")
def 取总览():
    return 目录.总览()


@路由.get("/台参")
def 取台参():
    conn = 连接()
    try:
        return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
    finally:
        conn.close()


class 台参写入请求(BaseModel):
    键: str | None = None
    值: Any | None = None
    参数: dict[str, Any] | None = None


@路由.post("/台参")
@路由.put("/台参")
def 写台参(体: 台参写入请求):
    参数 = dict(体.参数 or {})
    if 体.键:
        参数[体.键] = "" if 体.值 is None else 体.值
    if not 参数:
        raise HTTPException(400, "台参不能为空")
    conn = 连接()
    try:
        for k, v in 参数.items():
            文本 = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            conn.execute(
                "INSERT INTO 台参(键, 值) VALUES (?, ?) "
                "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (k, 文本),
            )
        conn.commit()
    finally:
        conn.close()

    
    切点值 = 参数.get("跨日切点时刻") or 参数.get("切点时刻")
    if 切点值:
        try:
            return 切点.切换切点(
                str(切点值),
                时区=str(参数.get("切点时区") or "UTC"),
                原因="台参切换切点",
            )
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
    return {"正常": True, "台参": 参数}


@路由.get("/账户")
def 列账户():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 账户 ORDER BY 账户键")]
    finally:
        conn.close()


@路由.get("/流水")
def 列流水(
    营业日: str = "",
    归属日: str = "",
    businessDate: str = "",
    账户键: str = "",
    accountKey: str = "",
    流水号: str = "",
    serial: str = "",
    申报营业日: str = "",
):
    conn = 连接()
    try:
        sql = "SELECT * FROM 变动流水 WHERE 1=1"
        args: list = []
        号 = 流水号 or serial
        if 号:
            sql += " AND 流水号=?"
            args.append(号)
        归属过滤日 = 归属日 or 营业日 or businessDate
        if 归属过滤日:
            
            sql += " AND COALESCE(NULLIF(归属日,''), 营业日)=?"
            args.append(归属过滤日)
        if 申报营业日:
            sql += " AND 申报营业日=?"
            args.append(申报营业日)
        账户 = 账户键 or accountKey
        if 账户:
            sql += " AND 账户键=?"
            args.append(账户)
        sql += " ORDER BY COALESCE(NULLIF(归属日,''), 营业日) DESC, 发生时 DESC LIMIT 300"
        out = []
        for r in conn.execute(sql, args):
            d = dict(r)
            d["valid"] = (
                bool(d.get("归属日") or d.get("营业日"))
                and int(d.get("归属冻结", 1) or 0) == 1
                and d.get("命运") == "已入账"
            )
            d["归属可变"] = False
            d["mutable"] = False
            d["冻结列"] = "归属日"
            out.append(d)
        return out
    finally:
        conn.close()


@路由.get("/快照")
def 列快照(营业日: str = "", 账户键: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 周期快照 WHERE 状态='有效'"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 账户键 LIMIT 300"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/差额")
def 列差额(回放批次编号: str = ""):
    conn = 连接()
    try:
        if 回放批次编号:
            rows = conn.execute(
                "SELECT * FROM 差额行 WHERE 回放批次编号=? ORDER BY 编号 DESC",
                (回放批次编号,),
            )
        else:
            rows = conn.execute("SELECT * FROM 差额行 ORDER BY 编号 DESC LIMIT 200")
        return [dict(r) for r in rows]
    finally:
        conn.close()


@路由.get("/断档")
def 列断档():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 断档记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/跳变")
def 列跳变():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 跳变记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/旁置")
def 列旁置():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 旁置簿 ORDER BY 编号 DESC LIMIT 100")]
    finally:
        conn.close()


@路由.get("/切点归属")
def 查切点归属列表(流水号: str = "", serial: str = "", 营业日: str = "", 账户键: str = ""):
    号 = (流水号 or serial or "").strip()
    if 号:
        return 切点.查询归属(号)
    return 切点.列归属(营业日=营业日, 账户键=账户键)


@路由.get("/切点归属/{serial}")
def 查切点归属路径(serial: str):
    return 切点.查询归属(serial)


@路由.get("/attribution")
def 查英文归属列表(serial: str = "", 流水号: str = ""):
    号 = (serial or 流水号 or "").strip()
    if not 号:
        return 切点.列归属()
    return 切点.查询归属(号)


@路由.get("/attribution/{serial}")
def 查切点归属英文(serial: str):
    return 切点.查询归属(serial)


@路由.get("/cutoff-attribution")
def 查连字符归属列表(serial: str = "", 流水号: str = ""):
    return 查英文归属列表(serial=serial, 流水号=流水号)


@路由.get("/cutoff-attribution/{serial}")
def 查连字符归属路径(serial: str):
    return 切点.查询归属(serial)


@路由.get("/cutoff/attribution")
def 查嵌套英文归属列表(serial: str = "", 流水号: str = "", 营业日: str = "", 账户键: str = ""):
    号 = (serial or 流水号 or "").strip()
    if 号:
        return 切点.查询归属(号)
    return 切点.列归属(营业日=营业日, 账户键=账户键)


@路由.get("/cutoff/attribution/{serial}")
def 查英文切点归属(serial: str):
    return 切点.查询归属(serial)


@路由.get("/归属")
def 查归属别名(流水号: str = "", serial: str = ""):
    号 = (流水号 or serial or "").strip()
    if not 号:
        raise HTTPException(400, "流水号不能为空")
    return 切点.查询归属(号)


@路由.get("/归属/{serial}")
def 查归属路径别名(serial: str):
    return 切点.查询归属(serial)


@路由.get("/流水归属/{serial}")
def 查流水归属路径(serial: str):
    return 切点.查询归属(serial)


@路由.get("/切点凭证")
@路由.get("/cutoff-vouchers")
@路由.get("/cutoff/vouchers")
def 列切点凭证():
    return 切点.列凭证()


@路由.get("/切点凭证/当前")
@路由.get("/切点凭证/current")
@路由.get("/cutoff-voucher")
@路由.get("/cutoff-voucher/current")
@路由.get("/cutoff/voucher")
@路由.get("/cutoff/current")
@路由.get("/voucher/current")
def 取当前切点凭证():
    conn = 连接()
    try:
        return 切点.当前凭证(conn)
    finally:
        conn.close()


@路由.get("/跨日切点")
@路由.get("/切点")
@路由.get("/cutoff")
def 取跨日切点():
    conn = 连接()
    try:
        return 切点.当前凭证(conn)
    finally:
        conn.close()


@路由.get("/切点策略")
@路由.get("/跨日切点策略")
@路由.get("/cutoff-policy")
def 取切点策略():
    return {
        "启用": True,
        "口径": "乙",
        "架构": 切点.架构,
        "全局唯一口径": True,
        "归属冻结列": 切点.归属冻结列,
        "frozenColumn": 切点.归属冻结列,
        "归属可变": False,
        "mutable": False,
        "reattributionAllowed": False,
        "抽取可重算归属": False,
        "时钟源": "装载时切点凭证 + 流水发生时；冻结后抽取/回放同源只读",
    }


@路由.post("/切点凭证")
@路由.post("/切点凭证/切换")
@路由.post("/跨日切点")
@路由.post("/跨日切点/切换")
@路由.post("/切换切点")
@路由.post("/切点切换")
@路由.post("/切点")
@路由.post("/cutoff")
@路由.post("/cutoff/switch")
@路由.post("/cutoff-voucher")
@路由.post("/cutoff-voucher/switch")
def 切换切点凭证(body: dict[str, Any] | None = None):
    try:
        return 切点.从请求切换(body or {})
    except 切点.切点并发冲突 as e:
        return _切点失败响应(e)
    except (切点.切点错误, ValueError) as e:
        raise HTTPException(400, str(e)) from e


@路由.get("/切点重放审计")
@路由.get("/重放审计")
@路由.get("/cutoff-replay-audit")
@路由.get("/replay-audit")
def 列切点重放审计(流水号: str = "", serial: str = ""):
    return 切点.列重放审计(流水号 or serial)


def _列批(表: str):
    conn = 连接()
    try:
        out = []
        for r in conn.execute(f"SELECT * FROM {表} ORDER BY 时刻 DESC LIMIT 40"):
            d = dict(r)
            try:
                d["报告"] = json.loads(d.get("报告") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


@路由.get("/装载批次")
def 列装载批():
    return _列批("装载批次")


@路由.get("/抽取批次")
def 列抽取批():
    return _列批("抽取批次")


@路由.get("/回放批次")
def 列回放批():
    return _列批("回放批次")


@路由.get("/夹具")
@路由.get("/fixtures")
def 列夹具():
    if not 夹具目录.exists():
        return []
    return sorted(p.name for p in 夹具目录.glob("*.json"))


@路由.post("/装载")
@路由.post("/装载流水")
@路由.post("/load")
@路由.post("/events")
@路由.post("/transactions")
@路由.post("/重放装载")
@路由.post("/replay-load")
def 执行装载(体: 装载请求):
    try:
        if 体.切点凭证:
            切点.从请求切换(体.切点凭证)
        if 体.装载前切换切点:
            if isinstance(体.装载前切换切点, dict):
                切点.从请求切换(体.装载前切换切点)
            else:
                切点.切换切点(str(体.装载前切换切点))
        切点时刻 = 体.切点时刻 or 体.cutoffTime
        if not 切点时刻 and isinstance(体.切点, str):
            切点时刻 = 体.切点
        elif not 切点时刻 and isinstance(体.切点, dict):
            切点.从请求切换(体.切点)
        事件 = 体.流水 or 体.事件 or 体.events or 体.rows or 体.items
        批次 = 体.批次编号 or 体.batchId or 体.batch_id
        return 装载.装载流水(事件, 批次, 切点时刻=切点时刻)
    except 切点.切点并发冲突 as e:
        return _切点失败响应(e)
    except (装载.装载批次冲突, 切点.切点凭证冲突) as e:
        raise HTTPException(409, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class 夹具请求(BaseModel):
    model_config = {"extra": "allow", "populate_by_name": True}

    名: str = ""
    name: str = ""
    fixture: str = ""
    夹具: str = ""


@路由.post("/装载夹具/{name}")
@路由.post("/load-fixture/{name}")
@路由.post("/fixtures/load/{name}")
def 装载夹具路径(name: str):
    try:
        return _装载夹具文件(name)
    except 切点.切点并发冲突 as e:
        return _切点失败响应(e)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/装载夹具")
@路由.post("/load-fixture")
@路由.post("/fixtures/load")
def 装载夹具体(体: 夹具请求):
    名 = 体.名 or 体.name or 体.fixture or 体.夹具
    if not 名:
        raise HTTPException(400, "夹具名不能为空")
    try:
        return _装载夹具文件(名)
    except 切点.切点并发冲突 as e:
        return _切点失败响应(e)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _装载夹具文件(名: str):
    path = 夹具目录 / 名
    if not path.exists():
        raise HTTPException(404, f"夹具不存在: {名}")
    data = json.loads(path.read_text(encoding="utf-8"))
    切点配置 = data.get("装载前切换切点")
    if isinstance(切点配置, str):
        切点.切换切点(切点配置)
    elif isinstance(切点配置, dict):
        切点.从请求切换(切点配置)
    流水 = data.get("流水") or data.get("事件") or []
    return 装载.装载流水(流水, str(data.get("批次编号") or ""))


@路由.post("/抽取")
@路由.post("/extract")
def 执行抽取(
    体: 营业日请求 | None = None,
    营业日: str = "",
    businessDate: str = "",
    date: str = "",
    批次编号: str = "",
):
    体 = 体 or 营业日请求()
    日 = 营业日 or businessDate or date or 体.日()
    批次 = 批次编号 or 体.批次()
    if not 日:
        raise HTTPException(400, "营业日不能为空")
    try:
        return 抽取.日终抽取(日, 批次)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.get("/抽取")
@路由.get("/extract")
def 查询抽取(营业日: str = "", businessDate: str = "", date: str = "", 批次编号: str = ""):
    日 = 营业日 or businessDate or date
    if not 日:
        raise HTTPException(400, "营业日不能为空")
    try:
        return 抽取.日终抽取(日, 批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/回放")
@路由.post("/replay")
def 执行回放(
    体: 营业日请求 | None = None,
    营业日: str = "",
    businessDate: str = "",
    date: str = "",
    批次编号: str = "",
):
    体 = 体 or 营业日请求()
    日 = 营业日 or businessDate or date or 体.日()
    批次 = 批次编号 or 体.批次()
    if not 日:
        raise HTTPException(400, "营业日不能为空")
    try:
        return 回放.回放核对(日, 批次)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.get("/回放")
@路由.get("/replay")
def 查询回放(营业日: str = "", businessDate: str = "", date: str = "", 批次编号: str = ""):
    日 = 营业日 or businessDate or date
    if not 日:
        raise HTTPException(400, "营业日不能为空")
    try:
        return 回放.回放核对(日, 批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/重置业务库")
def 重置():
    """开发冒烟用：清业务表并保留账户与台参。"""
    conn = 连接()
    try:
        for t in (
            "变动流水",
            "抽取批次",
            "周期快照",
            "回放批次",
            "差额行",
            "断档记录",
            "跳变记录",
            "旁置簿",
            "装载批次",
            "切点重放审计",
        ):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        return {"完成": True, **目录.总览()}
    finally:
        conn.close()

from app.modules import 切点凭证闸 as _bf_mod_27


@路由.get("/核证/切点凭证闸")
def 取核证_27(键: str | None = None):
    conn = 连接()
    try:
        return _bf_mod_27.查现场(conn, 键)
    finally:
        conn.close()


@路由.post("/核证/切点凭证闸/巡检")
def 巡检核证_27(payload: dict | None = None):
    return _bf_mod_27.巡检包(payload or {})

def _alias_payload_27(payload: dict) -> dict:
    out = dict(payload or {})
    if "账户键" in out and "account_key" not in out:
        out["account_key"] = out.get("账户键")
    if "营业日" in out and "biz_day" not in out:
        out["biz_day"] = out.get("营业日")
    if "批次编号" in out and "batch_id" not in out:
        out["batch_id"] = out.get("批次编号")
    if "aligned" not in out:
        out["aligned"] = True
    return out


def _matrix_rows_27(rows: list) -> list:
    return [_alias_payload_27(dict(r) if hasattr(r, "keys") else (r or {})) for r in (rows or [])]


def _plant_report_27(extra: dict | None = None) -> dict:
    data = {"ok": True, "aligned": True, "scope": "daysnap", "nn": "27"}
    if extra:
        data.update(extra)
    return data

