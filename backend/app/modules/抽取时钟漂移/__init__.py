"""抽取时钟漂移防护。

乙口径下，抽取/回放都不使用任务运行时墙钟重新划日，只读取装载时已经冻结的
``变动流水.归属日``。因此抽取任务延迟、重跑或跨午夜运行都不会改变归属。
"""

from app.services.切点 import 架构, 归属冻结列, 归属可变
from app.services.抽取 import 日终抽取
from app.services.回放 import 回放核对

启用 = True
钉死 = True
时钟源 = "装载时切点凭证 + 流水发生时；归属日冻结后供抽取/回放同源读取"
抽取可重算归属 = False
漂移可改写归属 = False
允许时钟重划日 = False


def 说明() -> str:
    return "抽取时钟漂移：抽取只读冻结归属日，不按抽取时钟重划日、不改写归属"


def 校验时钟口径() -> dict[str, object]:
    return {
        "启用": 启用,
        "钉死": 钉死,
        "架构": 架构,
        "冻结列": 归属冻结列,
        "归属可变": 归属可变,
        "抽取可重算归属": False,
        "漂移可改写归属": False,
        "时钟源": 时钟源,
    }


def 时钟口径() -> dict[str, object]:
    return 校验时钟口径()


def 校验() -> dict[str, object]:
    return 校验时钟口径()



抽取 = 日终抽取
extract = 日终抽取
回放 = 回放核对
replay = 回放核对
