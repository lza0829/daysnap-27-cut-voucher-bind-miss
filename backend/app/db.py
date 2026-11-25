import os
import sqlite3
from pathlib import Path

def _数据目录() -> Path:
    return Path(
        os.environ.get("数据目录")
        or os.environ.get("DATA_DIR")
        or (Path(__file__).resolve().parent.parent / "data")
    )


数据目录 = _数据目录()
夹具目录 = Path(
    os.environ.get("夹具目录")
    or os.environ.get("FIXTURE_DIR")
    or (Path(__file__).resolve().parent.parent.parent / "fixtures")
)


def _默认库路径() -> Path:
    显式路径 = (
        os.environ.get("DB_PATH")
        or os.environ.get("DATABASE_PATH")
        or os.environ.get("SQLITE_PATH")
        or os.environ.get("DAYSNAP_DB_PATH")
    )
    if 显式路径:
        return Path(显式路径)
    return _数据目录() / os.environ.get("DB_FILENAME", "日镜账.db")


库路径 = _默认库路径()


def 连接() -> sqlite3.Connection:
    
    显式路径 = (
        os.environ.get("DB_PATH")
        or os.environ.get("DATABASE_PATH")
        or os.environ.get("SQLITE_PATH")
        or os.environ.get("DAYSNAP_DB_PATH")
    )
    if 显式路径:
        路径 = Path(显式路径)
    elif os.environ.get("数据目录") or os.environ.get("DATA_DIR"):
        路径 = _默认库路径()
    else:
        路径 = 库路径
    路径.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(路径, timeout=5.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    
    c.execute("PRAGMA busy_timeout = 5000")
    return c
