"""T 系统应用装配。"""
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .db import Base, engine
from .services.order import BizError

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    from .logging_config import configure_runtime_logging

    configure_runtime_logging().info("T 系统已启动")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="T 系统", version="1.0.0",
                  description="记忆匹配修改 + 人工修改 + 兑换记忆库（智眸 → T → ePortal 闭环）",
                  lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def audit_http_request(request: Request, call_next):
        """全部 HTTP 动作均落审计日志；不记录请求体、密码和令牌。"""
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            from .logging_config import audit

            audit("http_error", method=request.method, path=request.url.path,
                  duration_ms=round((perf_counter() - started) * 1000, 1))
            raise
        from .logging_config import audit

        audit("http", method=request.method, path=request.url.path, status=response.status_code,
              duration_ms=round((perf_counter() - started) * 1000, 1))
        return response

    from .api import auth_api, eportal_session, intake, mock_eportal, orders, rules

    app.include_router(auth_api.router)
    app.include_router(intake.router)
    app.include_router(orders.router)
    app.include_router(rules.router)
    app.include_router(rules.history_router)
    app.include_router(eportal_session.router)
    app.include_router(mock_eportal.router)

    @app.exception_handler(BizError)
    async def biz_error_handler(request: Request, exc: BizError):
        return JSONResponse(status_code=exc.status_code, content={"message": exc.message, "detail": exc.message})

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"message": f"参数错误：{exc.errors()[:3]}"})

    @app.get("/api/health")
    def health():
        return {"ok": True, "service": "t-system"}

    # 页面路由
    @app.get("/")
    def page_admin():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/t2")
    def page_t2():
        return FileResponse(STATIC_DIR / "t2.html")

    @app.get("/eportal")
    def page_eportal():
        return FileResponse(STATIC_DIR / "eportal.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def init_db() -> None:
    Base.metadata.create_all(engine)
    from .seed import seed_all

    seed_all()


app = create_app()
