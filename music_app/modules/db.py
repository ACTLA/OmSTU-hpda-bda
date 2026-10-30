"""Работа с PostgreSQL: подключение, инициализация, загрузка CSV и партий, вызов функций слоёв, витрина."""
import hashlib
import io
import os
from contextlib import contextmanager
from datetime import timedelta

import pandas as pd
import psycopg2

from . import analysis as an
from .config import DAY_ORDER, DATA_PATH, DT_START, DT_WEEKS, INDEX_COL, ROOT, SQL_DIR

SRC_COLS = ["src_index", "userid", "track", "artist", "genre", "city", "time", "day", "dt", "batch_id"]
CSV_COLS = ["userID", "Track", "artist", "genre", "City", "time", "Day"]


def _env() -> dict:
    env = dict(os.environ)
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"')
    return env


get_env = _env   # публичное имя: настройки .env нужны и модулю второй СУБД


def conn_params(dbname=None) -> dict:
    e = _env()
    return dict(host=e.get("PGHOST", "localhost"), port=int(e.get("PGPORT", 5432)), user=e.get("PGUSER", "postgres"),
                password=e.get("PGPASSWORD", ""), dbname=dbname or e.get("PGDATABASE", "music_dwh"))


@contextmanager
def tx(dbname=None):
    c = psycopg2.connect(**conn_params(dbname))
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init_schema() -> None:
    """Создаёт БД (если нет), схемы, таблицы и функции из sql/*.sql."""
    p = conn_params()
    c = psycopg2.connect(**{**p, "dbname": "postgres"})
    c.autocommit = True
    with c.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (p["dbname"],))
        if not cur.fetchone():
            cur.execute(f'CREATE DATABASE "{p["dbname"]}"')
    c.close()
    with tx() as conn, conn.cursor() as cur:
        for f in sorted(SQL_DIR.glob("*.sql")):
            cur.execute(f.read_text(encoding="utf-8"))


def make_dt(df: pd.DataFrame) -> list:
    """Синтетическая дата: неделя = хэш содержимого строки (одинаковые строки -> одинаковая dt),
    день недели берётся из колонки Day, поэтому dt согласована с Day."""
    wd = df["Day"].map({d: i for i, d in enumerate(DAY_ORDER)}).fillna(0).astype(int)
    cols = [df[c].fillna("") for c in CSV_COLS]
    weeks = [int(hashlib.md5("|".join(t).encode("utf-8")).hexdigest(), 16) % DT_WEEKS for t in zip(*cols)]
    return [DT_START + timedelta(weeks=w, days=int(d)) for w, d in zip(weeks, wd)]


def _replace_batch(out: pd.DataFrame, batch_id: int) -> int:
    """Идемпотентная загрузка партии в src: удаляет ту же партию и копирует данные заново.
    Другие партии и слои хранилища не затрагиваются (без полного пересоздания)."""
    out = out.copy()
    out["batch_id"] = batch_id
    buf = io.StringIO()
    out[SRC_COLS].to_csv(buf, index=False, header=False)
    buf.seek(0)
    cols = ",".join(f'"{c}"' for c in SRC_COLS)
    with tx() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM src.music_tracks WHERE batch_id = %s", (batch_id,))
        cur.copy_expert(f"COPY src.music_tracks ({cols}) FROM STDIN WITH (FORMAT csv)", buf)
        cur.execute("SELECT count(*) FROM src.music_tracks WHERE batch_id = %s", (batch_id,))
        return cur.fetchone()[0]


def load_source(path=DATA_PATH) -> int:
    """Первоначальная загрузка CSV -> src.music_tracks (все поля текстом + dt), партия 0.
    Перезаписывает только партию 0, синтетические партии остаются."""
    raw = an.load_raw(path)
    out = pd.DataFrame({"src_index": raw[INDEX_COL] if INDEX_COL in raw.columns else pd.Series(range(len(raw))).astype(str)})
    for name, col in zip(SRC_COLS[1:8], CSV_COLS):
        out[name] = raw[col]
    out["dt"] = make_dt(raw)
    return _replace_batch(out, 0)


def load_batch(path) -> dict:
    """Загрузка синтетической партии из CSV генератора в src.music_tracks (ЛР2)."""
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    miss = {"src_index", "dt", "batch_id", *CSV_COLS} - set(df.columns)
    if miss:
        raise ValueError(f"В файле {path} нет колонок: {', '.join(sorted(miss))}")
    ids = df["batch_id"].dropna().unique()
    if len(ids) != 1 or int(ids[0]) < 1:
        raise ValueError("Файл должен содержать одну партию с batch_id >= 1 (0 — исходный CSV)")
    dts = pd.to_datetime(df["dt"], format="%Y-%m-%d")
    out = pd.DataFrame({"src_index": df["src_index"]})
    for name, col in zip(SRC_COLS[1:8], CSV_COLS):
        out[name] = df[col]
    out["dt"] = df["dt"]
    n = _replace_batch(out, int(ids[0]))
    return {"batch_id": int(ids[0]), "rows": n, "dt_min": dts.min().date(), "dt_max": dts.max().date()}


def run_staging() -> int:
    with tx() as conn, conn.cursor() as cur:
        cur.execute("SELECT stg.load_staging()")
        return cur.fetchone()[0]


def run_ods(sdt, edt) -> int:
    with tx() as conn, conn.cursor() as cur:
        cur.execute("SELECT ods.load_ods(%s, %s)", (sdt, edt))
        return cur.fetchone()[0]


def fetch_rows(sql: str, params=None):
    """Возвращает (имена колонок, список кортежей) без приведения типов — для переноса между СУБД."""
    with tx() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return [d[0] for d in cur.description], cur.fetchall()


def query(sql: str, params=None) -> pd.DataFrame:
    with tx() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return pd.DataFrame(cur.fetchall(), columns=cols)


CHECKS = {
    "Строк по слоям": """SELECT 'src' AS layer, count(*) AS rows FROM src.music_tracks
UNION ALL SELECT 'stg', count(*) FROM stg.music_tracks
UNION ALL SELECT 'ods', count(*) FROM ods.music_tracks""",
    "Дубликаты в ODS (должно быть 0)": """SELECT count(*) AS duplicate_groups FROM (
  SELECT 1 FROM ods.music_tracks
  GROUP BY user_id, track, artist, genre, city, listen_time, day_of_week, dt HAVING count(*) > 1) t""",
    "Длины user_id в ODS (должно быть 8)": """SELECT length(user_id) AS len, count(*) AS rows
FROM ods.music_tracks GROUP BY 1 ORDER BY 1""",
    "Значения 'unknown' в ODS": """SELECT sum((track = 'unknown')::int) AS track, sum((artist = 'unknown')::int) AS artist,
       sum((genre = 'unknown')::int) AS genre FROM ods.music_tracks""",
    "День недели dt совпадает с Day (расхождений 0)": """SELECT count(*) AS mismatches FROM ods.music_tracks
WHERE trim(to_char(dt, 'Day')) <> day_of_week""",
    "Загрузка по месяцам": """SELECT date_trunc('month', dt)::date AS month, count(*) AS rows
FROM ods.music_tracks GROUP BY 1 ORDER BY 1""",
}


def run_dwh(sdt, edt) -> int:
    with tx() as conn, conn.cursor() as cur:
        cur.execute("SELECT dwh.load_dwh(%s, %s)", (sdt, edt))
        return cur.fetchone()[0]


def run_mart(sdt, edt) -> int:
    """Загрузка аналитической витрины DWH за период (ЛР2)."""
    with tx() as conn, conn.cursor() as cur:
        cur.execute("SELECT dwh.load_mart(%s, %s)", (sdt, edt))
        return cur.fetchone()[0]


def state_token() -> str:
    """Быстрый «отпечаток» состояния хранилища: меняется после любой загрузки. Нужен для кэша страницы."""
    r = query("""SELECT (SELECT count(*) FROM src.music_tracks) AS s, (SELECT count(*) FROM stg.music_tracks) AS g,
       (SELECT count(*) FROM ods.music_tracks) AS o, (SELECT count(*) FROM dwh.fact_listening) AS f,
       (SELECT count(*) FROM dwh.mart_listening_daily) AS m,
       (SELECT COALESCE(max(run_id), 0) FROM ods.load_run_log) AS r,
       (SELECT COALESCE(max(loaded_at)::text, '') FROM dwh.mart_listening_daily) AS ml""")
    return "|".join(str(v) for v in r.iloc[0])


DWH_CHECKS = {
    "Строк в таблицах DWH": """SELECT 'fact_listening' AS tbl, count(*) AS rows FROM dwh.fact_listening
UNION ALL SELECT 'dim_user', count(*) FROM dwh.dim_user
UNION ALL SELECT 'dim_track', count(*) FROM dwh.dim_track
UNION ALL SELECT 'dim_artist', count(*) FROM dwh.dim_artist
UNION ALL SELECT 'dim_genre', count(*) FROM dwh.dim_genre
UNION ALL SELECT 'dim_city', count(*) FROM dwh.dim_city
UNION ALL SELECT 'dim_date', count(*) FROM dwh.dim_date
UNION ALL SELECT 'dim_month', count(*) FROM dwh.dim_month
UNION ALL SELECT 'dim_time', count(*) FROM dwh.dim_time
UNION ALL SELECT 'dim_daypart', count(*) FROM dwh.dim_daypart""",
    "Сверка: ODS и факты (числа должны совпасть)": """SELECT (SELECT count(*) FROM ods.music_tracks) AS ods_rows,
       (SELECT count(*) FROM dwh.fact_listening) AS fact_rows""",
    "Витрина: сверка с фактами (числа должны совпасть)": """SELECT (SELECT count(*) FROM dwh.fact_listening) AS fact_rows,
       (SELECT COALESCE(sum(listens_cnt), 0) FROM dwh.mart_listening_daily) AS mart_listens,
       (SELECT count(*) FROM dwh.mart_listening_daily) AS mart_rows""",
    "Топ-10 жанров": """SELECT g.genre_name, count(*) AS listens
FROM dwh.fact_listening f JOIN dwh.dim_genre g ON g.genre_key = f.genre_key
GROUP BY g.genre_name ORDER BY listens DESC LIMIT 10""",
    "Топ-5 жанров в каждом городе": """SELECT * FROM (
  SELECT c.city_name, g.genre_name, count(*) AS listens,
         row_number() OVER (PARTITION BY c.city_name ORDER BY count(*) DESC) AS rn
  FROM dwh.fact_listening f
  JOIN dwh.dim_city c ON c.city_key = f.city_key
  JOIN dwh.dim_genre g ON g.genre_key = f.genre_key
  GROUP BY c.city_name, g.genre_name) t
WHERE rn <= 5 ORDER BY city_name, rn""",
    "Прослушивания по времени суток": """SELECT p.daypart_name, count(*) AS listens
FROM dwh.fact_listening f
JOIN dwh.dim_time t ON t.time_key = f.time_key
JOIN dwh.dim_daypart p ON p.daypart_key = t.daypart_key
GROUP BY p.daypart_key, p.daypart_name ORDER BY p.daypart_key""",
    "Топ-10 исполнителей (без unknown)": """SELECT a.artist_name, count(*) AS listens
FROM dwh.fact_listening f
JOIN dwh.dim_track t ON t.track_key = f.track_key
JOIN dwh.dim_artist a ON a.artist_key = t.artist_key
WHERE a.artist_name <> 'unknown'
GROUP BY a.artist_name ORDER BY listens DESC LIMIT 10""",
    "Прослушивания по месяцам": """SELECT m.month_key, m.month_name, count(*) AS listens
FROM dwh.fact_listening f
JOIN dwh.dim_date d ON d.date_key = f.date_key
JOIN dwh.dim_month m ON m.month_key = d.month_key
GROUP BY m.month_key, m.month_name ORDER BY m.month_key""",
    "Прослушивания по дням недели": """SELECT d.weekday_num, d.day_of_week, count(*) AS listens
FROM dwh.fact_listening f JOIN dwh.dim_date d ON d.date_key = f.date_key
GROUP BY d.weekday_num, d.day_of_week ORDER BY d.weekday_num""",
}
