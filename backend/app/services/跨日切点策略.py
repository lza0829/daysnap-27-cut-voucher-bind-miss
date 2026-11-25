"""兼容按功能名查找服务层的调用方，实现仍统一在 :mod:`app.services.切点`。"""

from app.services.切点 import *  
from app.services.切点 import (  
    架构,
    归属冻结列,
    归属可变,
    切换切点,
    从请求切换,
    计算归属日,
    查询归属,
    列归属,
    当前凭证,
    列凭证,
    列重放审计,
    切点错误,
    切点并发冲突,
    切点凭证冲突,
)
from app.services.装载 import 装载流水  
from app.services.抽取 import 日终抽取  
from app.services.回放 import 回放核对  

启用 = True
口径 = "乙"


def 归属日是否可变() -> bool:
    return False
