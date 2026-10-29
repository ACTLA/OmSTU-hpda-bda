from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_PATH = ROOT / "data" / "music_project.csv"
REPORTS_DIR = ROOT / "reports"

INDEX_COL = "Unnamed: 0"  # служебная колонка индекса в CSV
DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# --- БД и синтетическая дата dt ---
from datetime import date

SQL_DIR = ROOT / "sql"
DT_START = date(2025, 1, 6)  # понедельник; от него отсчитываются недели
DT_WEEKS = 26                # dt попадает в 26 недель (янв-июн 2025)
