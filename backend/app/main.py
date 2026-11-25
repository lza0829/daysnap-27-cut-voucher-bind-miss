from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import seed
from app.routers import api
from app.services import 切点

app = FastAPI(title="日镜账", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(切点.切点并发冲突)
async def _切点并发冲突处理(_: Request, exc: 切点.切点并发冲突) -> JSONResponse:
    
    return JSONResponse(
        status_code=409,
        content={
            "detail": str(exc),
            "valid": False,
            "失败闭": True,
            "架构": 切点.架构,
            "归属可变": False,
            "mutable": False,
        },
    )


@app.on_event("startup")
def _启动():
    seed.初始化库()


app.include_router(api)
