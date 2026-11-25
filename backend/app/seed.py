from datetime import datetime, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 切点凭证闸表 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  单号 TEXT, 生效时刻 TEXT, 旧界 TEXT, 新界 TEXT, 状态 TEXT,
  时刻 TEXT NOT NULL DEFAULT '',
  载荷 TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS 台参 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 账户 (
  账户键 TEXT PRIMARY KEY,
  户名 TEXT NOT NULL,
  币种 TEXT NOT NULL DEFAULT 'CNY',
  开户营业日 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '正常'
);
CREATE TABLE IF NOT EXISTS 变动流水 (
  流水号 TEXT PRIMARY KEY,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  归属日 TEXT NOT NULL DEFAULT '',
  归属营业日 TEXT GENERATED ALWAYS AS (归属日) VIRTUAL,
  冻结归属日 TEXT GENERATED ALWAYS AS (归属日) VIRTUAL,
  attribution_date TEXT GENERATED ALWAYS AS (归属日) VIRTUAL,
  申报营业日 TEXT NOT NULL DEFAULT '',
  切点凭证编号 TEXT NOT NULL DEFAULT '',
  归属架构 TEXT NOT NULL DEFAULT '乙-冻结列永冻',
  归属冻结 INTEGER NOT NULL DEFAULT 1,
  归属可变 INTEGER NOT NULL DEFAULT 0,
  载荷指纹 TEXT NOT NULL DEFAULT '',
  发生时 TEXT NOT NULL,
  变动额 INTEGER NOT NULL,
  摘要 TEXT NOT NULL DEFAULT '',
  批次编号 TEXT NOT NULL DEFAULT '',
  命运 TEXT NOT NULL DEFAULT '已入账'
);
CREATE TABLE IF NOT EXISTS 抽取批次 (
  编号 TEXT PRIMARY KEY,
  营业日 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 周期快照 (
  快照键 TEXT PRIMARY KEY,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  余额 INTEGER NOT NULL,
  抽取批次编号 TEXT NOT NULL,
  来源 TEXT NOT NULL DEFAULT '日终抽取',
  状态 TEXT NOT NULL DEFAULT '有效',
  UNIQUE(账户键, 营业日)
);
CREATE TABLE IF NOT EXISTS 回放批次 (
  编号 TEXT PRIMARY KEY,
  营业日 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 差额行 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  回放批次编号 TEXT NOT NULL,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  快照余额 INTEGER,
  回放余额 INTEGER,
  差额 INTEGER NOT NULL,
  原因 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 断档记录 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  账户键 TEXT NOT NULL,
  缺营业日 TEXT NOT NULL,
  检出批次编号 TEXT NOT NULL,
  处置 TEXT NOT NULL DEFAULT '待处理',
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 跳变记录 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  前日余额 INTEGER,
  当日余额 INTEGER,
  流水净额 INTEGER,
  偏离 INTEGER NOT NULL,
  检出批次编号 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 旁置簿 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  原因 TEXT NOT NULL,
  账户键 TEXT NOT NULL DEFAULT '',
  营业日 TEXT NOT NULL DEFAULT '',
  载荷 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}',
  载荷指纹 TEXT NOT NULL DEFAULT '',
  切点凭证编号 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 切点凭证 (
  凭证编号 TEXT PRIMARY KEY,
  版本 INTEGER NOT NULL UNIQUE,
  切点时刻 TEXT NOT NULL,
  时区 TEXT NOT NULL DEFAULT 'UTC',
  归属规则 TEXT NOT NULL DEFAULT '切点及之后发生入下一营业日',
  边界策略 TEXT NOT NULL DEFAULT '切点整点归新营业日',
  架构 TEXT NOT NULL DEFAULT '乙-冻结列永冻',
  当前 INTEGER NOT NULL DEFAULT 0,
  valid INTEGER NOT NULL DEFAULT 1,
  前一凭证编号 TEXT NOT NULL DEFAULT '',
  操作人 TEXT NOT NULL DEFAULT '',
  原因 TEXT NOT NULL DEFAULT '',
  生效时刻 TEXT NOT NULL,
  切换时刻 TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS 唯一当前切点凭证 ON 切点凭证(当前) WHERE 当前=1;
CREATE TABLE IF NOT EXISTS 切点重放审计 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  流水号 TEXT NOT NULL,
  请求批次编号 TEXT NOT NULL DEFAULT '',
  原装载批次编号 TEXT NOT NULL DEFAULT '',
  入账凭证编号 TEXT NOT NULL DEFAULT '',
  重放时凭证编号 TEXT NOT NULL DEFAULT '',
  冻结归属日 TEXT NOT NULL,
  凭证当前重算归属日 TEXT NOT NULL DEFAULT '',
  归属日 TEXT NOT NULL,
  归属可变 INTEGER NOT NULL DEFAULT 0,
  处置 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS 切点重放审计流水号 ON 切点重放审计(流水号);
"""


兼容对象 = """
CREATE TRIGGER IF NOT EXISTS 禁止冻结归属日改写
BEFORE UPDATE OF 归属日 ON 变动流水
WHEN OLD.归属日 <> '' AND NEW.归属日 <> OLD.归属日
BEGIN
  SELECT RAISE(ABORT, '归属日是乙口径冻结列，禁止切点切换/抽取/回放改写');
END;
DROP VIEW IF EXISTS 切点凭证表;
DROP VIEW IF EXISTS cutoff_voucher;
DROP VIEW IF EXISTS 切点归属;
CREATE VIEW 切点凭证表 AS SELECT * FROM 切点凭证;
CREATE VIEW cutoff_voucher AS SELECT * FROM 切点凭证;
CREATE VIEW 切点归属 AS
SELECT
  流水号,
  账户键,
  归属日,
  归属营业日,
  冻结归属日,
  attribution_date,
  归属日 AS businessDate,
  归属日 AS attributionDate,
  切点凭证编号 AS 凭证编号,
  切点凭证编号,
  '归属日' AS 冻结列,
  '归属日' AS frozenColumn,
  归属冻结 AS 冻结,
  归属冻结 AS frozen,
  CASE WHEN COALESCE(NULLIF(归属日, ''), 营业日, '') <> '' AND 归属冻结 = 1 AND 命运 = '已入账' THEN 1 ELSE 0 END AS valid,
  CASE WHEN COALESCE(NULLIF(归属日, ''), 营业日, '') <> '' AND 归属冻结 = 1 AND 命运 = '已入账' THEN 1 ELSE 0 END AS 有效,
  归属架构,
  归属架构 AS 架构,
  0 AS 归属可变,
  0 AS mutable
FROM 变动流水;
"""

空桩模块 = [
    "开户首日无前日",
    "同日重复抽取幂等",
    "流水迟到补抽",
    "销户终镜",
    "负余额闸",
    "币种隔离",
    "半截抽取回滚",
    "快照封印写闸",
    "回放权威择定",
    "断档补齐",
    "断档旁置",
    "跳变阈值",
    "账户分片抽取",
    "流水序缺口",
    "并户继承镜",
    "子户汇总镜",
    "假期跳日日历",
    "差额分桶",
    "三日连环守恒",
    "抽取重试预算",
]


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _表列(conn, 表: str) -> set[str]:
    
    return {r["name"] for r in conn.execute(f"PRAGMA table_xinfo({表})")}


def _补列(conn, 表: str, 列: str, DDL: str) -> None:
    if 列 not in _表列(conn, 表):
        conn.execute(f"ALTER TABLE {表} ADD COLUMN {列} {DDL}")


def _迁移旧库(conn) -> None:
    """旧库只有营业日列；首次升级时把它一次性固化为冻结归属日。"""
    流水列 = _表列(conn, "变动流水")
    _补列(conn, "变动流水", "归属日", "TEXT NOT NULL DEFAULT ''")
    _补列(conn, "变动流水", "归属营业日", "TEXT GENERATED ALWAYS AS (归属日) VIRTUAL")
    _补列(conn, "变动流水", "冻结归属日", "TEXT GENERATED ALWAYS AS (归属日) VIRTUAL")
    _补列(conn, "变动流水", "attribution_date", "TEXT GENERATED ALWAYS AS (归属日) VIRTUAL")
    _补列(conn, "变动流水", "申报营业日", "TEXT NOT NULL DEFAULT ''")
    _补列(conn, "变动流水", "切点凭证编号", "TEXT NOT NULL DEFAULT ''")
    _补列(conn, "变动流水", "归属架构", "TEXT NOT NULL DEFAULT '乙-冻结列永冻'")
    _补列(conn, "变动流水", "归属冻结", "INTEGER NOT NULL DEFAULT 1")
    _补列(conn, "变动流水", "归属可变", "INTEGER NOT NULL DEFAULT 0")
    _补列(conn, "变动流水", "载荷指纹", "TEXT NOT NULL DEFAULT ''")
    
    conn.execute("UPDATE 变动流水 SET 归属日=营业日 WHERE COALESCE(归属日,'')=''")
    conn.execute("UPDATE 变动流水 SET 申报营业日=营业日 WHERE COALESCE(申报营业日,'')=''")
    conn.execute("UPDATE 变动流水 SET 归属冻结=1 WHERE 归属冻结 IS NULL")
    conn.execute("UPDATE 变动流水 SET 归属可变=0 WHERE 归属可变 IS NULL")

    _补列(conn, "装载批次", "载荷指纹", "TEXT NOT NULL DEFAULT ''")
    _补列(conn, "装载批次", "切点凭证编号", "TEXT NOT NULL DEFAULT ''")

    if conn.execute("SELECT COUNT(*) AS c FROM 切点凭证").fetchone()["c"] == 0:
        戳 = 此刻()
        conn.execute(
            "INSERT INTO 切点凭证(凭证编号, 版本, 切点时刻, 时区, 归属规则, 边界策略, 架构, 当前, valid, "
            "前一凭证编号, 操作人, 原因, 生效时刻, 切换时刻) "
            "VALUES (?,?,?,?,?,?,?,1,1,'','系统','初始化默认日终切点 23:00:00',?,?)",
            ("切点凭证-默认-v1", 1, "23:00:00", "UTC", "切点及之后发生入下一营业日", "切点整点归新营业日", "乙-冻结列永冻", 戳, 戳),
        )


def 初始化库() -> None:
    conn = 连接()
    try:
        conn.executescript(建表)
        _迁移旧库(conn)
        conn.executescript(兼容对象)
        if conn.execute("SELECT COUNT(*) AS c FROM 台参").fetchone()["c"] == 0:
            for k, v in {
                "站名": "日镜账",
                "骨架": "日终余额抽取→周期快照落行→变动流水回放核对→断档跳变检出→快照与回放差额清单",
                "跳变阈值分": "0",
                "断档处置": "记断档并继续",
                "缺前日策略": "以零为开端",
                "重复抽取": "盖写同日快照",
                "切点归属架构": "乙-冻结列永冻",
                "跨日切点时刻": "23:00:00",
                "切点时区": "UTC",
            }.items():
                conn.execute("INSERT INTO 台参(键, 值) VALUES (?, ?)", (k, v))
        else:
            for k, v in {
                "切点归属架构": "乙-冻结列永冻",
                "跨日切点时刻": "23:00:00",
                "切点时区": "UTC",
            }.items():
                exists = conn.execute("SELECT 1 FROM 台参 WHERE 键=?", (k,)).fetchone()
                if not exists:
                    conn.execute("INSERT INTO 台参(键, 值) VALUES (?, ?)", (k, v))
        if conn.execute("SELECT COUNT(*) AS c FROM 账户").fetchone()["c"] == 0:
            for 键, 名, 日 in [
                ("户甲", "甲收单户", "2026-11-01"),
                ("户乙", "乙结算户", "2026-11-01"),
                ("户丙", "丙备用户", "2026-11-05"),
            ]:
                conn.execute(
                    "INSERT INTO 账户(账户键, 户名, 币种, 开户营业日, 状态) VALUES (?,?,?,?,?)",
                    (键, 名, "CNY", 日, "正常"),
                )
        conn.commit()
    finally:
        conn.close()
