"""跨日切点与归属查询。

全局架构口径（唯一实现，不做运行时双口径开关）：

乙 · 流水行归属列永冻
- 装载事务内读取当前切点凭证，计算一次归属日并写入 ``变动流水.归属日`` 冻结列；
- 抽取/回放只读取该冻结列，禁止重算或 UPDATE 归属；
- 切点凭证切换只影响切换后的新装载（新流水号）；
- 同流水号重放只能幂等拒绝或原样返回，归属日不可变；
- 切点切换与装载均以 ``BEGIN IMMEDIATE`` 串行化，锁等待超时即失败闭。

甲口径（按当前凭证重算、允许重放改变归属）在本仓库不启用。
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.db import 连接
from app.services.时间 import 此刻

架构 = "乙-冻结列永冻"
架构代号 = "乙"
归属冻结列 = "归属日"
归属可变 = False
归属可变标志 = 0


class 切点错误(ValueError):
    """切点配置或凭证不可用。"""


class 切点凭证冲突(切点错误):
    """凭证编号已用于不同切点。"""


class 切点并发冲突(RuntimeError):
    """切点切换/装载临界区获取失败；调用方必须按失败闭处理。"""


def 解析时区(值: str | None):
    文本 = str(值 or "UTC").strip() or "UTC"
    if 文本.upper() in {"UTC", "Z", "+00:00", "-00:00"}:
        return timezone.utc
    if 文本.upper().startswith("UTC"):
        文本 = 文本[3:].strip()
        if not 文本:
            return timezone.utc
    if len(文本) >= 5 and 文本[0] in "+-" and 文本[1:3].isdigit():
        符号 = 1 if 文本[0] == "+" else -1
        小时 = int(文本[1:3])
        分钟 = int(文本[4:6]) if len(文本) >= 6 and 文本[4:6].isdigit() else 0
        return timezone(符号 * timedelta(hours=小时, minutes=分钟))
    try:
        return ZoneInfo(文本)
    except ZoneInfoNotFoundError as e:
        raise 切点错误(f"不支持的切点时区: {值}") from e


def 标准化切点时刻(值: Any) -> str:
    """把 23:00 / 23:00:00 / ISO 时间统一为 HH:MM:SS，允许日终 24:00:00。"""
    if isinstance(值, dict):
        值 = 值.get("切点时刻") or 值.get("时刻") or 值.get("time") or 值.get("切点")
    文本 = str(值 or "").strip()
    if not 文本:
        raise 切点错误("切点时刻不能为空，示例：23:00")
    if 文本.endswith("Z"):
        文本 = 文本[:-1] + "+00:00"
    if "T" in 文本 or " " in 文本:
        dt = datetime.fromisoformat(文本)
        return dt.strftime("%H:%M:%S")
    段 = 文本.split(":")
    if len(段) not in {2, 3}:
        raise 切点错误("切点时刻格式须为 HH:MM 或 HH:MM:SS")
    try:
        小时 = int(段[0])
        分钟 = int(段[1])
        秒 = int(段[2]) if len(段) == 3 else 0
    except ValueError as e:
        raise 切点错误("切点时刻须为数字时间") from e
    if 小时 == 24:
        if 分钟 != 0 or 秒 != 0:
            raise 切点错误("24 点切点只能写作 24:00 或 24:00:00")
        return "24:00:00"
    if not (0 <= 小时 <= 23 and 0 <= 分钟 <= 59 and 0 <= 秒 <= 59):
        raise 切点错误("切点时刻超出合法范围")
    return f"{小时:02d}:{分钟:02d}:{秒:02d}"


def 解析发生时(发生时: Any, 申报营业日: str = "") -> datetime:
    文本 = str(发生时 or "").strip()
    if not 文本 and 申报营业日:
        文本 = f"{申报营业日}T12:00:00"
    if not 文本:
        raise 切点错误("发生时不能为空")
    if 文本.endswith("Z"):
        文本 = 文本[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(文本)
    except ValueError as e:
        raise 切点错误(f"发生时格式非法: {发生时}") from e


def 计算归属日(发生时: Any, 切点时刻: str, 时区: str = "UTC", 申报营业日: str = "") -> str:
    """按切点凭证把发生时刻映射为营业日。

    约定：切点是当日日终边界；到达/越过切点后，发生额归入下一营业日。
    例如 23:00 切点下，22:59 归当日，23:01 归次日。
    """
    标准切点 = 标准化切点时刻(切点时刻)
    tz = 解析时区(时区)
    dt = 解析发生时(发生时, 申报营业日)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    else:
        dt = dt.astimezone(tz)

    段 = 标准切点.split(":")
    h, m, s = map(int, 段)
    if h == 24:
        切点边界 = datetime.combine(dt.date() + timedelta(days=1), time(0, 0, 0), tzinfo=tz)
    else:
        切点边界 = dt.replace(hour=h, minute=m, second=s, microsecond=0)
    if dt >= 切点边界:
        return (dt.date() + timedelta(days=1)).isoformat()
    return dt.date().isoformat()


def _凭证字典(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    d["当前"] = bool(d.get("当前"))
    d["valid"] = bool(d.get("valid", 1))
    d["口径"] = 架构代号
    d["architecture"] = d.get("架构") or 架构
    d["归属冻结列"] = 归属冻结列
    d["frozenColumn"] = 归属冻结列
    d["归属可变"] = False
    d["mutable"] = False
    d["reattributionAllowed"] = False
    return d


def 当前凭证(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM 切点凭证 WHERE 当前=1 AND valid=1").fetchone()
    if row is None:
        
        raise 切点错误("当前切点凭证缺失，拒绝在切点状态不明时装载")
    return _凭证字典(row)  


def 列凭证() -> list[dict[str, Any]]:
    conn = 连接()
    try:
        rows = conn.execute(
            "SELECT * FROM 切点凭证 ORDER BY 版本 DESC"
        ).fetchall()
        return [_凭证字典(r) for r in rows if r is not None]
    finally:
        conn.close()


def 切换切点(
    切点时刻: Any = None,
    *,
    时区: str | None = None,
    凭证编号: str = "",
    原因: str = "",
    操作人: str = "",
    架构请求: str = "",
    conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """切换当前切点凭证。与装载一样使用 BEGIN IMMEDIATE 串行化。"""
    if 架构请求 and str(架构请求) not in {架构, 架构代号, "frozen", "frozen_attribution", "乙"}:
        raise 切点错误("本系统全局只启用乙口径：归属冻结列永冻；不能切换为甲口径")
    标准时刻 = 标准化切点时刻(切点时刻)
    自有连接 = conn is None
    if conn is None:
        conn = 连接()
    try:
        if 自有连接:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as e:
                raise 切点并发冲突(f"切点切换未取得排他临界区，已失败闭: {e}") from e
        旧 = conn.execute("SELECT 凭证编号, 版本, 时区 FROM 切点凭证 WHERE 当前=1").fetchone()
        旧编号 = str(旧["凭证编号"]) if 旧 else ""
        版本行 = conn.execute("SELECT COALESCE(MAX(版本),0)+1 AS v FROM 切点凭证").fetchone()
        版本 = int(版本行["v"])
        编号 = str(凭证编号 or "").strip() or f"切点凭证-v{版本}-{uuid.uuid4().hex[:8]}"
        时区值 = str(时区 or (旧["时区"] if 旧 else "UTC") or "UTC").strip()
        解析时区(时区值)  

        已存在 = conn.execute("SELECT * FROM 切点凭证 WHERE 凭证编号=?", (编号,)).fetchone()
        if 已存在:
            d = _凭证字典(已存在)
            if d and (d["切点时刻"] != 标准时刻 or d["时区"] != 时区值):
                raise 切点凭证冲突(f"切点凭证编号已存在但切点内容不同: {编号}")
            if d and not d.get("当前"):
                
                conn.execute("UPDATE 切点凭证 SET 当前=0 WHERE 当前=1")
                conn.execute("UPDATE 切点凭证 SET 当前=1 WHERE 凭证编号=?", (编号,))
                d["当前"] = True
                d["重新设为当前"] = True
            if 自有连接:
                conn.commit()
            d = d or {}
            d["幂等"] = True
            return d

        戳 = 此刻()
        conn.execute("UPDATE 切点凭证 SET 当前=0 WHERE 当前=1")
        conn.execute(
            "INSERT INTO 切点凭证(凭证编号, 版本, 切点时刻, 时区, 归属规则, 边界策略, 架构, 当前, valid, "
            "前一凭证编号, 操作人, 原因, 生效时刻, 切换时刻) "
            "VALUES (?,?,?,?,?,?,?,1,1,?,?,?,?,?)",
            (
                编号,
                版本,
                标准时刻,
                时区值,
                "切点及之后发生入下一营业日",
                "切点整点归新营业日",
                架构,
                旧编号,
                str(操作人 or ""),
                str(原因 or ""),
                戳,
                戳,
            ),
        )
        if 自有连接:
            conn.commit()
        row = conn.execute("SELECT * FROM 切点凭证 WHERE 凭证编号=?", (编号,)).fetchone()
        result = _凭证字典(row) or {}
        result["幂等"] = False
        return result
    except sqlite3.OperationalError as e:
        if 自有连接:
            conn.rollback()
        if "locked" in str(e).lower():
            raise 切点并发冲突(f"切点切换与装载冲突，锁等待超时，已失败闭: {e}") from e
        raise
    except Exception:
        if 自有连接:
            conn.rollback()
        raise
    finally:
        if 自有连接:
            conn.close()


def 从请求切换(body: dict[str, Any] | None) -> dict[str, Any]:
    body = body or {}
    嵌套 = body.get("切点凭证") if isinstance(body.get("切点凭证"), dict) else {}
    时刻 = (
        body.get("切点时刻")
        or body.get("切点时间")
        or body.get("切点")
        or body.get("时刻")
        or body.get("time")
        or body.get("cutoff")
        or body.get("cutoff_time")
        or body.get("cutoffAt")
        or body.get("cutoffTime")
        or 嵌套.get("切点时刻")
        or 嵌套.get("切点时间")
        or 嵌套.get("切点")
    )
    时区 = body.get("时区") or body.get("timezone") or 嵌套.get("时区")
    编号 = body.get("凭证编号") or body.get("切点凭证编号") or 嵌套.get("凭证编号") or ""
    return 切换切点(
        时刻,
        时区=时区,
        凭证编号=str(编号 or ""),
        原因=str(body.get("原因") or ""),
        操作人=str(body.get("操作人") or body.get("operator") or ""),
        架构请求=str(body.get("架构") or body.get("口径") or ""),
    )


def 归属视图(row: sqlite3.Row, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    d = dict(row)
    归属日 = str(d.get("归属日") or d.get("营业日") or "")
    凭证编号 = str(d.get("切点凭证编号") or "")
    凭证 = None
    当前编号 = ""
    if conn is not None:
        if 凭证编号:
            r = conn.execute("SELECT * FROM 切点凭证 WHERE 凭证编号=?", (凭证编号,)).fetchone()
            凭证 = _凭证字典(r)
        r = conn.execute("SELECT 凭证编号 FROM 切点凭证 WHERE 当前=1").fetchone()
        当前编号 = str(r["凭证编号"]) if r else ""
    valid = bool(归属日) and int(d.get("归属冻结", 1) or 0) == 1 and d.get("命运") == "已入账"
    return {
        "流水号": d.get("流水号"),
        "serial": d.get("流水号"),
        "accountKey": d.get("账户键"),
        "账户键": d.get("账户键"),
        "归属日": 归属日,
        "归属营业日": d.get("归属营业日") or 归属日,
        "冻结归属日": d.get("冻结归属日") or 归属日,
        "frozenAttributionDate": d.get("冻结归属日") or 归属日,
        "businessDate": 归属日,
        "business_date": 归属日,
        "attributionDate": 归属日,
        "attribution_date": 归属日,
        "营业日": d.get("营业日"),
        "申报营业日": d.get("申报营业日") or "",
        "凭证编号": 凭证编号,
        "切点凭证编号": 凭证编号,
        "cutoffVoucherId": 凭证编号,
        "voucherId": 凭证编号,
        "voucherCode": 凭证编号,
        "凭证": 凭证,
        "cutoffVoucher": 凭证,
        "voucher": 凭证,
        "当前凭证编号": 当前编号,
        "currentVoucherId": 当前编号,
        "凭证仍当前": bool(凭证编号 and 凭证编号 == 当前编号),
        "冻结列": 归属冻结列,
        "归属冻结列": 归属冻结列,
        "frozenColumn": 归属冻结列,
        "frozen_column": 归属冻结列,
        "frozen": True,
        "冻结": True,
        "归属冻结": True,
        "valid": valid,
        "isValid": valid,
        "is_valid": valid,
        "有效": valid,
        "架构": 架构,
        "architecture": 架构,
        "架构代号": 架构代号,
        "归属可变": False,
        "mutable": False,
        "reattributionAllowed": False,
        "重放语义": "同流水号幂等拒绝或原样返回，冻结归属日不变",
    }


def 查询归属(流水号: str) -> dict[str, Any]:
    号 = str(流水号 or "").strip()
    if not 号:
        raise 切点错误("流水号不能为空")
    conn = 连接()
    try:
        row = conn.execute("SELECT * FROM 变动流水 WHERE 流水号=?", (号,)).fetchone()
        if row is None:
            return {
                "流水号": 号,
                "serial": 号,
                "归属日": None,
                "归属营业日": None,
                "冻结归属日": None,
                "frozenAttributionDate": None,
                "businessDate": None,
                "business_date": None,
                "attributionDate": None,
                "attribution_date": None,
                "营业日": None,
                "凭证编号": None,
                "切点凭证编号": None,
                "cutoffVoucherId": None,
                "voucherId": None,
                "voucherCode": None,
                "凭证": None,
                "cutoffVoucher": None,
                "voucher": None,
                "冻结列": 归属冻结列,
                "frozenColumn": 归属冻结列,
                "frozen_column": 归属冻结列,
                "frozen": False,
                "valid": False,
                "isValid": False,
                "is_valid": False,
                "有效": False,
                "原因": "流水不存在或尚未入账",
                "架构": 架构,
                "architecture": 架构,
                "归属可变": False,
                "mutable": False,
                "reattributionAllowed": False,
            }
        return 归属视图(row, conn)
    finally:
        conn.close()


def 列归属(营业日: str = "", 账户键: str = "") -> list[dict[str, Any]]:
    conn = 连接()
    try:
        sql = "SELECT * FROM 变动流水 WHERE 1=1"
        args: list[Any] = []
        if 营业日:
            sql += " AND COALESCE(NULLIF(归属日,''), 营业日)=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 归属日 DESC, 发生时 DESC LIMIT 300"
        rows = conn.execute(sql, args).fetchall()
        return [归属视图(r, conn) for r in rows]
    finally:
        conn.close()


def 写重放审计(
    conn: sqlite3.Connection,
    *,
    流水号: str,
    请求批次编号: str,
    冻结归属日: str,
    入账凭证编号: str,
    重放凭证编号: str,
    凭证当前重算归属日: str,
    原装载批次编号: str = "",
    处置: str = "同流水号幂等拒绝；冻结归属不变",
) -> None:
    conn.execute(
        "INSERT INTO 切点重放审计(流水号, 请求批次编号, 原装载批次编号, 入账凭证编号, 重放时凭证编号, "
        "冻结归属日, 凭证当前重算归属日, 归属日, 归属可变, 处置, 时刻) "
        "VALUES (?,?,?,?,?,?,?,?,0,?,?)",
        (
            流水号,
            请求批次编号,
            原装载批次编号,
            入账凭证编号,
            重放凭证编号,
            冻结归属日,
            凭证当前重算归属日,
            冻结归属日,
            处置,
            此刻(),
        ),
    )


def 列重放审计(流水号: str = "") -> list[dict[str, Any]]:
    conn = 连接()
    try:
        if 流水号:
            rows = conn.execute(
                "SELECT * FROM 切点重放审计 WHERE 流水号=? ORDER BY 编号 DESC LIMIT 200",
                (流水号,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM 切点重放审计 ORDER BY 编号 DESC LIMIT 200").fetchall()
        out = [dict(r) for r in rows]
        for d in out:
            d["归属可变"] = False
            d["mutable"] = False
        return out
    finally:
        conn.close()
