"""FastAPI 应用入口"""
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, RedirectResponse

# 确保项目根目录在 sys.path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from web.database import init_db, get_db
from web.routes import (
    tags, stocks, labels,
    settings as settings_routes,
    scheduler as scheduler_routes,
    universe as universe_routes,
    monitor as monitor_routes,
    trading as trading_routes,
    dashboard as dashboard_routes,
)

app = FastAPI(title="ZQ-Trade 六维分析标注系统")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Jinja2 模板
_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))

# 静态文件
_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# 注册 API 路由
app.include_router(tags.router)
app.include_router(stocks.router)
app.include_router(labels.router)
app.include_router(settings_routes.router)
app.include_router(scheduler_routes.router)
app.include_router(universe_routes.router)
app.include_router(monitor_routes.router)
app.include_router(trading_routes.router)
app.include_router(dashboard_routes.router)


@app.on_event("startup")
async def startup():
    init_db()
    # 启动定时任务引擎
    from scheduler.engine import start_scheduler
    start_scheduler()


@app.on_event("shutdown")
async def shutdown():
    from scheduler.engine import stop_scheduler
    stop_scheduler()


# ─── HTML 页面路由 ───

@app.get("/", response_class=HTMLResponse)
def page_home(request: Request):
    return templates.TemplateResponse("stock_list.html", {
        "request": request, "active_page": "stocks",
    })


@app.get("/stocks/{stock_id}", response_class=HTMLResponse)
def page_stock_detail(request: Request, stock_id: str, from_page: Optional[str] = None):
    with get_db() as conn:
        stock = conn.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
    if not stock:
        return HTMLResponse("<h1>股票不存在</h1>", status_code=404)
    active_page = "universe" if from_page == "universe" else "stocks"
    return templates.TemplateResponse("stock_detail.html", {
        "request": request, "active_page": active_page,
        "stock": dict(stock),
        "now": int(datetime.now().timestamp()),
    })


@app.get("/dashboard", response_class=HTMLResponse)
def page_dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "active_page": "dashboard",
    })


@app.get("/universe", response_class=HTMLResponse)
def page_universe(request: Request):
    return templates.TemplateResponse("universe.html", {
        "request": request, "active_page": "universe",
    })


@app.get("/monitor", response_class=HTMLResponse)
def page_monitor(request: Request):
    return RedirectResponse(url="/universe", status_code=302)


@app.get("/trading", response_class=HTMLResponse)
def page_trading(request: Request):
    return templates.TemplateResponse("trading.html", {
        "request": request, "active_page": "trading",
    })


@app.get("/scheduler", response_class=HTMLResponse)
def page_scheduler(request: Request):
    return templates.TemplateResponse("scheduler.html", {
        "request": request, "active_page": "scheduler",
    })


@app.get("/settings", response_class=HTMLResponse)
def page_settings(request: Request):
    return templates.TemplateResponse("settings.html", {
        "request": request, "active_page": "settings",
    })
