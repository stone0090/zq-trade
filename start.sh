#!/bin/bash
# ZQ-Trade 启动脚本 (Git Bash / WSL / Linux / macOS)

cd "$(dirname "$0")"

echo "========================================"
echo "  ZQ-Trade 六维分析半自动交易系统"
echo "========================================"
echo ""

# 检测 Python 路径（兼容 Windows venv 和 Unix venv）
if [ -f "venv/Scripts/python.exe" ]; then
    PYTHON="venv/Scripts/python.exe"
    PIP="venv/Scripts/python.exe -m pip"
elif [ -f "venv/bin/python" ]; then
    PYTHON="venv/bin/python"
    PIP="venv/bin/python -m pip"
else
    echo "[错误] 未找到虚拟环境 venv，请先执行:"
    echo "  python -m venv venv"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# 检查 uvicorn
if ! $PYTHON -c "import uvicorn" 2>/dev/null; then
    echo "[提示] 正在安装缺少的依赖..."
    $PIP install fastapi "uvicorn[standard]" jinja2 python-multipart apscheduler httpx requests yfinance
    echo ""
fi

# 检查端口 8000 是否被占用，杀掉所有占用进程（含 uvicorn 子进程）
if command -v lsof &>/dev/null; then
    PIDS=$(lsof -ti:8000 2>/dev/null)
    if [ -n "$PIDS" ]; then
        echo "[提示] 端口 8000 被占用，正在释放..."
        for p in $PIDS; do
            kill -9 "$p" 2>/dev/null
        done
        sleep 2
    fi
elif command -v netstat &>/dev/null; then
    PIDS=$(netstat -ano 2>/dev/null | grep ":8000.*LISTENING" | awk '{print $5}' | sort -u)
    if [ -n "$PIDS" ]; then
        echo "[提示] 端口 8000 被占用，正在释放..."
        for p in $PIDS; do
            [ "$p" = "0" ] && continue
            # Windows: 用 taskkill /T 杀掉进程树（含子进程）
            taskkill //PID "$p" //F //T 2>/dev/null || kill -9 "$p" 2>/dev/null
        done
        sleep 2
    fi
fi

echo "[ZQ-Trade] 启动服务 http://localhost:8000"
echo "[ZQ-Trade] 按 Ctrl+C 停止"
echo ""

# 延迟2秒后自动打开浏览器
(sleep 2 && (
    if command -v start &>/dev/null; then
        start http://localhost:8000
    elif command -v xdg-open &>/dev/null; then
        xdg-open http://localhost:8000
    elif command -v open &>/dev/null; then
        open http://localhost:8000
    fi
)) &

# 启动服务
$PYTHON -m uvicorn web.app:app --host 0.0.0.0 --port 8000 --reload
