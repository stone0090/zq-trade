"""服务器配置"""
from pathlib import Path

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parent.parent

# 数据目录
DATA_DIR = ROOT_DIR / "data"

# 数据库
DB_PATH = DATA_DIR / "zqtrade.db"

# 图表输出目录
CHARTS_DIR = ROOT_DIR / "charts" / "web"

# 标注案例 CSV
LABELED_CASES_CSV = DATA_DIR / "labeled_cases.csv"
