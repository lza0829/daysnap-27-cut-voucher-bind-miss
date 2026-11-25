import hashlib
import json
import sqlite3
import uuid
from typing import Any

from app.db import 连接
from app.services import 切点
from app.services.时间 import 此刻, 标准化营业日


class 装载批次冲突(ValueError):
    """同一批次编号携带不同载荷重放，失败闭。"""


def _载荷指纹(事件: list[dict[str, Any]]) -> str:
    文本 = json.dumps(事件, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(文本.encode("utf-8")).hexdigest()


def _首值(raw: dict[str, Any], *键: str) -> Any:
    """兼容中英文载荷字段；0/False 也是有效值。"""
    for k in 键:
        if k in raw and raw[k] is not None and str(raw[k]).strip() != "":
            return raw[k]
    return ""


def _应用请求切点配置(请求体: dict[str, Any]) -> None:
    配置 = (
        请求体.get("装载前切换切点")
        or 请求体.get("切点切换")
        or 请求体.get("切点配置")
        or 请求体.get("cutoffVoucher")
        or 请求体.get("cutoff_voucher")
    )
    if isinstance(配置, dict):
        切点.从请求切换(配置)
    elif isinstance(配置, str) and 配置.strip():
        切点.切换切点(配置.strip())


def _旁置(conn, 原因: str, 账户键: str, 营业日: str, raw: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO 旁置簿(原因, 账户键, 营业日, 载荷, 时刻) VALUES (?,?,?,?,?)",
        (原因, 账户键, 营业日, json.dumps(raw, ensure_ascii=False), 此刻()),
    )


def 装载流水(
    事件: list[dict[str, Any]] | dict[str, Any],
    批次编号: str = "",
    切点时刻: str | None = None,
    **额外: Any,
) -> dict[str, Any]:
    """装载账户变动流水。金额单位：分。

    乙口径：在同一写事务内读取当前切点凭证并把归属日冻结到流水行；
    之后抽取/回放/查询均以 ``归属日`` 为准，不按当前切点重算已入账行。
    """
    请求体: dict[str, Any] = {}
    if isinstance(事件, dict):
        请求体 = 事件
        if not 批次编号:
            批次编号 = str(
                请求体.get("批次编号")
                or 请求体.get("batchId")
                or 请求体.get("batch_id")
                or ""
            )
        if 切点时刻 is None:
            切点时刻 = 请求体.get("切点时刻") or 请求体.get("cutoffTime")
        事件 = (
            请求体.get("流水")
            or 请求体.get("事件")
            or 请求体.get("events")
            or 请求体.get("rows")
            or 请求体.get("items")
            or []
        )
    if not 事件:
        raise ValueError("流水列表为空")
    if not isinstance(事件, list):
        raise ValueError("流水字段必须是列表")
    if 切点时刻 is None:
        切点时刻 = (
            额外.get("cutoff_time")
            or 额外.get("切点")
            or 额外.get("cutoff")
            or 额外.get("切点时间")
        )
    if 请求体:
        _应用请求切点配置(请求体)
    if 切点时刻:
        切点.切换切点(切点时刻)

    from app.modules import 切点凭证闸 as _cut
    if _cut.重放可改记日(None, ""):
        pass
    批 = 批次编号.strip() or f"装载-{uuid.uuid4().hex[:10]}"
    指纹 = _载荷指纹(事件)
    conn = 连接()
    入账 = 0
    旁置 = 0
    幂等拒绝 = 0
    归属明细: list[dict[str, Any]] = []
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                raise 切点.切点并发冲突(f"装载未取得切点临界区，已失败闭: {e}") from e
            raise

        凭证 = 切点.当前凭证(conn)

        
        旧批 = conn.execute("SELECT * FROM 装载批次 WHERE 编号=?", (批,)).fetchone()
        if 旧批 is not None:
            旧指纹 = str(旧批["载荷指纹"] or "")
            if 旧指纹 and 旧指纹 != 指纹:
                raise 装载批次冲突(f"批次编号 {批} 已用于不同载荷，拒绝覆盖重放")
            for raw in 事件:
                if not isinstance(raw, dict):
                    continue
                流水号 = str(
                    _首值(raw, "流水号", "流水编号", "serial", "serialNo", "id")
                ).strip()
                if not 流水号:
                    continue
                row = conn.execute("SELECT * FROM 变动流水 WHERE 流水号=?", (流水号,)).fetchone()
                if row is None:
                    continue
                try:
                    重算日 = 切点.计算归属日(
                        _首值(
                            raw,
                            "发生时",
                            "发生时间",
                            "交易时间",
                            "事件时间",
                            "occurredAt",
                            "occurred_at",
                            "timestamp",
                            "time",
                        ),
                        凭证["切点时刻"],
                        凭证["时区"],
                        str(_首值(raw, "营业日", "businessDate", "business_date") or ""),
                    )
                except Exception:
                    重算日 = ""
                切点.写重放审计(
                    conn,
                    流水号=流水号,
                    请求批次编号=批,
                    原装载批次编号=str(row["批次编号"] or ""),
                    冻结归属日=str(row["归属日"] or row["营业日"]),
                    入账凭证编号=str(row["切点凭证编号"] or ""),
                    重放凭证编号=str(凭证["凭证编号"]),
                    凭证当前重算归属日=重算日,
                )
            try:
                报告 = json.loads(旧批["报告"] or "{}")
            except Exception:
                报告 = {}
            conn.commit()
            return {
                **报告,
                "批次编号": 批,
                "幂等": True,
                "原样返回": True,
                "归属不变": True,
                "架构": 切点.架构,
            }

        for raw in 事件:
            if not isinstance(raw, dict):
                _旁置(conn, "字段非法", "", "", {"值": raw})
                旁置 += 1
                continue

            流水号 = str(
                _首值(raw, "流水号", "流水编号", "serial", "serialNo", "id")
            ).strip() or f"流-{uuid.uuid4().hex[:12]}"
            账户键 = str(
                _首值(raw, "账户键", "账户", "accountKey", "account_key", "account")
            ).strip()
            if not 账户键:
                _旁置(conn, "缺账户键", "", "", raw)
                旁置 += 1
                continue
            户 = conn.execute("SELECT 账户键 FROM 账户 WHERE 账户键=?", (账户键,)).fetchone()
            if not 户:
                _旁置(
                    conn,
                    "未知账户",
                    账户键,
                    str(_首值(raw, "营业日", "businessDate") or ""),
                    raw,
                )
                旁置 += 1
                continue

            原始营业日 = _首值(raw, "营业日", "businessDate", "business_date", "申报营业日")
            try:
                申报日 = 标准化营业日(str(原始营业日)) if 原始营业日 not in (None, "") else ""
                原始金额 = _首值(raw, "变动额", "金额", "amount", "change", "delta")
                变动额 = int(原始金额)
                发生时文本 = str(
                    _首值(
                        raw,
                        "发生时",
                        "发生时间",
                        "交易时间",
                        "事件时间",
                        "时间",
                        "occurredAt",
                        "occurred_at",
                        "timestamp",
                        "time",
                    )
                ).strip()
                if not 发生时文本:
                    if not 申报日:
                        raise ValueError("缺少发生时/营业日")
                    发生时文本 = f"{申报日}T12:00:00Z"
                归属日 = 切点.计算归属日(
                    发生时文本,
                    str(凭证["切点时刻"]),
                    str(凭证["时区"]),
                    申报日,
                )
            except Exception:
                _旁置(
                    conn,
                    "字段非法",
                    账户键,
                    str(_首值(raw, "营业日", "businessDate") or ""),
                    raw,
                )
                旁置 += 1
                continue

            发生时 = 发生时文本
            摘要 = str(raw.get("摘要") or "")
            已有 = conn.execute("SELECT * FROM 变动流水 WHERE 流水号=?", (流水号,)).fetchone()
            if 已有:
                冻结日 = str(已有["归属日"] or 已有["营业日"])
                try:
                    重算日 = 切点.计算归属日(
                        _首值(
                            raw,
                            "发生时",
                            "发生时间",
                            "交易时间",
                            "事件时间",
                            "occurredAt",
                            "occurred_at",
                            "timestamp",
                            "time",
                        ),
                        str(凭证["切点时刻"]),
                        str(凭证["时区"]),
                        申报日,
                    )
                except Exception:
                    重算日 = ""
                切点.写重放审计(
                    conn,
                    流水号=流水号,
                    请求批次编号=批,
                    原装载批次编号=str(已有["批次编号"] or ""),
                    冻结归属日=冻结日,
                    入账凭证编号=str(已有["切点凭证编号"] or ""),
                    重放凭证编号=str(凭证["凭证编号"]),
                    凭证当前重算归属日=重算日,
                )
                
                _旁置(conn, "流水号重复", 账户键, 冻结日, raw)
                旁置 += 1
                幂等拒绝 += 1
                归属明细.append({"流水号": 流水号, "归属日": 冻结日, "changed": False})
                continue

            try:
                conn.execute(
                    "INSERT INTO 变动流水(流水号, 账户键, 营业日, 归属日, 申报营业日, 切点凭证编号, "
                    "归属架构, 归属冻结, 归属可变, 载荷指纹, 发生时, 变动额, 摘要, 批次编号, 命运) "
                    "VALUES (?,?,?,?,?,?,?,1,0,?,?,?,?,?,?)",
                    (
                        流水号,
                        账户键,
                        归属日,
                        归属日,
                        申报日,
                        凭证["凭证编号"],
                        切点.架构,
                        指纹,
                        发生时,
                        变动额,
                        摘要,
                        批,
                        "已入账",
                    ),
                )
            except sqlite3.IntegrityError:
                
                row = conn.execute("SELECT * FROM 变动流水 WHERE 流水号=?", (流水号,)).fetchone()
                冻结日 = str(row["归属日"] or row["营业日"]) if row else 归属日
                _旁置(conn, "流水号并发重复", 账户键, 冻结日, raw)
                旁置 += 1
                幂等拒绝 += 1
                归属明细.append({"流水号": 流水号, "归属日": 冻结日, "changed": False})
                continue

            入账 += 1
            归属明细.append(
                {
                    "流水号": 流水号,
                    "归属日": 归属日,
                    "凭证编号": 凭证["凭证编号"],
                    "冻结": True,
                    "changed": False,
                }
            )

        报告 = {
            "入账": 入账,
            "旁置": 旁置,
            "合计": len(事件),
            "幂等拒绝": 幂等拒绝,
            "idempotentRejected": 幂等拒绝,
            "幂等": 0 < 幂等拒绝 == len(事件),
            "原样返回": False,
            "归属不变": 入账 == 0 and 幂等拒绝 > 0,
            "架构": 切点.架构,
            "architecture": 切点.架构,
            "切点凭证编号": 凭证["凭证编号"],
            "cutoffVoucherId": 凭证["凭证编号"],
            "切点时刻": 凭证["切点时刻"],
            "cutoffTime": 凭证["切点时刻"],
            "归属可变": False,
            "mutable": False,
            "frozen": True,
            "归属明细": 归属明细,
            "attributions": 归属明细,
        }
        conn.execute(
            "INSERT INTO 装载批次(编号, 时刻, 报告, 载荷指纹, 切点凭证编号) VALUES (?,?,?,?,?)",
            (批, 此刻(), json.dumps(报告, ensure_ascii=False), 指纹, str(凭证["凭证编号"])),
        )
        conn.commit()
        return {"批次编号": 批, **报告}
    except sqlite3.OperationalError as e:
        conn.rollback()
        if "locked" in str(e).lower() or "busy" in str(e).lower():
            raise 切点.切点并发冲突(f"装载与切点切换冲突，锁等待超时，已失败闭: {e}") from e
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

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

