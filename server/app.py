"""FastAPI 应用入口"""
import sys
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse

# 确保项目根目录在 sys.path
_root = Path(__file__).resolve().parent.parent
if str(_root) not in sys.path:
    sys.path.insert(0, str(_root))

from server.database import init_db, get_db
from server.routes import batches, stocks, labels

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

# 注册 API 路由
app.include_router(batches.router)
app.include_router(stocks.router)
app.include_router(labels.router)


@app.on_event("startup")
def startup():
    init_db()


# ─── HTML 页面路由 ───

@app.get("/", response_class=HTMLResponse)
def page_home(request: Request):
    return templates.TemplateResponse("batch_list.html", {"request": request})


@app.get("/batches/{batch_id}", response_class=HTMLResponse)
def page_stock_list(request: Request, batch_id: str):
    with get_db() as conn:
        batch = conn.execute("SELECT * FROM batches WHERE id=?", (batch_id,)).fetchone()
    if not batch:
        return HTMLResponse("<h1>批次不存在</h1>", status_code=404)
    return templates.TemplateResponse("stock_list.html", {
        "request": request,
        "batch": dict(batch),
    })


@app.get("/batches/{batch_id}/stock/{stock_id}", response_class=HTMLResponse)
def page_stock_detail(request: Request, batch_id: str, stock_id: str):
    with get_db() as conn:
        stock = conn.execute("SELECT * FROM stocks WHERE id=?", (stock_id,)).fetchone()
    if not stock:
        return HTMLResponse("<h1>股票不存在</h1>", status_code=404)
    return templates.TemplateResponse("stock_detail.html", {
        "request": request,
        "batch_id": batch_id,
        "stock": dict(stock),
    })
