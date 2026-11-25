状态 = "半截"


def 现行记日(conn, 发生时: str) -> str:
    return ""


def 重放可改记日(conn, 流水号: str) -> bool:
    return True


def 落生效单(conn, **kwargs) -> str:
    return ""


def 行内记日永冻(conn, 流水号: str) -> bool:
    return False

def 表名() -> str:
    return "切点凭证闸表"


def 查现场(conn, 键: str | None = None) -> dict:
    rows = []
    try:
        rows = [dict(r) for r in conn.execute(
            "SELECT 单号, 生效时刻, 旧界, 新界, 状态 FROM 切点凭证闸表 ORDER BY 编号 DESC LIMIT 40"
        )]
    except Exception:
        rows = []
    if 键:
        rows = [r for r in rows if 键 in str(r.values())]
    return {"切界单": rows, "记日可漂": True, "nn": "27", "valid": False}


def 巡检包(payload: dict | None = None) -> dict:
    base = {"kind": "cut-voucher", "nn": "27", "replay_mutates": True, "bound": 27}
    if payload:
        base.update(payload)
    return base
