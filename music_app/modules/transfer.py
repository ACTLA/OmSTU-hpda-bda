"""Вторая СУБД: перенос аналитической витрины из PostgreSQL и проверка согласованности (АБД, ЛР2).

Целевая СУБД выбирается переменной TARGET_DBMS в .env:
  clickhouse — основной вариант (clickhouse-connect, HTTP 8123, см. docker-compose.yml);
  sqlite     — запасной вариант без установки сервера (файл data/mart_target.sqlite3).
"""
import sqlite3
import time
from abc import ABC, abstractmethod
from contextlib import closing
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from . import db
from .config import MART_TABLE, SQLITE_PATH

MART_COLS = ["dt", "month_key", "day_of_week", "city_name", "genre_name", "listens_cnt", "unique_users",
             "unique_tracks", "unique_artists", "unknown_artist_cnt", "avg_listens_per_user", "city_day_share"]
KEY_COLS = ["dt", "city_name", "genre_name"]
# допуски сравнения числовых колонок: целые — точно, дробные — с учётом float/Decimal в разных СУБД
TOL = {"month_key": 0, "listens_cnt": 0, "unique_users": 0, "unique_tracks": 0, "unique_artists": 0,
       "unknown_artist_cnt": 0, "avg_listens_per_user": 1e-6, "city_day_share": 1e-6}
CHUNK = 20000
PG_TABLE = f"dwh.{MART_TABLE}"


# ---------------------------------------------------------------- целевые СУБД
class Target(ABC):
    name: str
    table: str
    types_note: str

    @abstractmethod
    def init_mart(self) -> None: ...

    @abstractmethod
    def delete_period(self, sdt, edt) -> None: ...

    @abstractmethod
    def insert_rows(self, cols, rows) -> None: ...

    @abstractmethod
    def query(self, sql: str) -> pd.DataFrame: ...

    def scalar(self, sql: str):
        return self.query(sql).iat[0, 0]

    def where(self, sdt, edt) -> str:
        return f"dt BETWEEN '{sdt}' AND '{edt}'"


class SQLiteTarget(Target):
    name = "SQLite"
    types_note = "DATE → TEXT (ISO), BIGINT → INTEGER, NUMERIC → REAL"

    def __init__(self, path=SQLITE_PATH):
        self.path, self.table = path, MART_TABLE

    def _conn(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return sqlite3.connect(self.path)

    def init_mart(self):
        with closing(self._conn()) as c:
            c.execute(f"""CREATE TABLE IF NOT EXISTS {self.table} (
                dt TEXT NOT NULL, month_key INTEGER NOT NULL, day_of_week TEXT NOT NULL,
                city_name TEXT NOT NULL, genre_name TEXT NOT NULL, listens_cnt INTEGER NOT NULL,
                unique_users INTEGER NOT NULL, unique_tracks INTEGER NOT NULL, unique_artists INTEGER NOT NULL,
                unknown_artist_cnt INTEGER NOT NULL, avg_listens_per_user REAL NOT NULL, city_day_share REAL NOT NULL,
                PRIMARY KEY (dt, city_name, genre_name))""")
            c.commit()

    def delete_period(self, sdt, edt):
        with closing(self._conn()) as c:
            c.execute(f"DELETE FROM {self.table} WHERE {self.where(sdt, edt)}")
            c.commit()

    def insert_rows(self, cols, rows):
        conv = [tuple(v.isoformat() if isinstance(v, date) else float(v) if isinstance(v, Decimal) else v
                      for v in r) for r in rows]
        with closing(self._conn()) as c:
            c.executemany(f"INSERT OR REPLACE INTO {self.table} ({', '.join(cols)}) "
                          f"VALUES ({', '.join('?' * len(cols))})", conv)
            c.commit()

    def query(self, sql):
        with closing(self._conn()) as c:
            return pd.read_sql_query(sql, c)


class ClickHouseTarget(Target):
    name = "ClickHouse"
    types_note = ("DATE → Date, TEXT → LowCardinality(String), BIGINT → UInt64, "
                  "NUMERIC(12,4)/(10,6) → Decimal(12,4)/(10,6)")

    def __init__(self, host="localhost", port=8123, user="default", password="", database="music_mart"):
        self.cfg = dict(host=host, port=int(port), username=user, password=password)
        self.database, self.table = database, f"{database}.{MART_TABLE}"

    def _client(self):
        try:
            import clickhouse_connect
        except ImportError as e:
            raise RuntimeError("Не установлен пакет clickhouse-connect: pip install clickhouse-connect") from e
        return clickhouse_connect.get_client(connect_timeout=5, send_receive_timeout=120, **self.cfg)

    def init_mart(self):
        c = self._client()
        c.command(f"CREATE DATABASE IF NOT EXISTS {self.database}")
        c.command(f"""CREATE TABLE IF NOT EXISTS {self.table} (
            dt Date, month_key UInt32, day_of_week LowCardinality(String),
            city_name LowCardinality(String), genre_name LowCardinality(String),
            listens_cnt UInt64, unique_users UInt64, unique_tracks UInt64, unique_artists UInt64,
            unknown_artist_cnt UInt64, avg_listens_per_user Decimal(12, 4), city_day_share Decimal(10, 6))
            ENGINE = MergeTree PARTITION BY toYYYYMM(dt) ORDER BY (dt, city_name, genre_name)""")

    def delete_period(self, sdt, edt):
        # mutations_sync=2: ждём завершения удаления на всех репликах, затем вставляем заново
        self._client().command(f"ALTER TABLE {self.table} DELETE WHERE {self.where(sdt, edt)}",
                               settings={"mutations_sync": 2})

    def insert_rows(self, cols, rows):
        c = self._client()
        for i in range(0, len(rows), CHUNK):
            c.insert(self.table, [list(r) for r in rows[i:i + CHUNK]], column_names=list(cols))

    def query(self, sql):
        r = self._client().query(sql)
        return pd.DataFrame(r.result_rows, columns=r.column_names)


def get_target(kind: str | None = None) -> Target:
    e = db.get_env()
    kind = (kind or e.get("TARGET_DBMS", "clickhouse")).strip().lower()
    if kind == "sqlite":
        return SQLiteTarget()
    if kind == "clickhouse":
        return ClickHouseTarget(e.get("CH_HOST", "localhost"), e.get("CH_PORT", 8123), e.get("CH_USER", "default"),
                                e.get("CH_PASSWORD", ""), e.get("CH_DATABASE", "music_mart"))
    raise ValueError(f"TARGET_DBMS='{kind}' не поддерживается (clickhouse | sqlite)")


# ---------------------------------------------------------------- перенос
def transfer_mart(sdt, edt, target: Target | None = None) -> dict:
    """Переносит строки витрины за период из PostgreSQL в целевую СУБД.
    Повторный перенос безопасен: период в целевой таблице сначала удаляется."""
    t = target or get_target()
    t0 = time.time()
    _, rows = db.fetch_rows(f"SELECT {', '.join(MART_COLS)} FROM {PG_TABLE} WHERE dt BETWEEN %s AND %s "
                            f"ORDER BY dt, city_name, genre_name", (sdt, edt))
    t.init_mart()
    t.delete_period(sdt, edt)
    t.insert_rows(MART_COLS, rows)
    in_target = int(t.scalar(f"SELECT count(*) FROM {t.table} WHERE {t.where(sdt, edt)}"))
    return {"target": t.name, "rows_read": len(rows), "rows_in_target": in_target,
            "seconds": round(time.time() - t0, 2)}


# ---------------------------------------------------------------- сверка
# (показатель, SQL с подстановками {t} — таблица, {w} — условие периода, допуск, пояснение)
CHECKS = [
    ("Количество записей в витрине", "SELECT count(*) FROM {t} WHERE {w}", 0,
     "число строк уровня «дата × город × жанр»"),
    ("Количество уникальных дат (dt)", "SELECT count(DISTINCT dt) FROM {t} WHERE {w}", 0, "покрытие периода"),
    ("Количество уникальных жанров", "SELECT count(DISTINCT genre_name) FROM {t} WHERE {w}", 0, "набор жанров"),
    ("Количество уникальных городов", "SELECT count(DISTINCT city_name) FROM {t} WHERE {w}", 0, "набор городов"),
    ("Минимальная дата", "SELECT min(dt) FROM {t} WHERE {w}", 0, "начало периода в витрине"),
    ("Максимальная дата", "SELECT max(dt) FROM {t} WHERE {w}", 0, "конец периода в витрине"),
    ("Всего прослушиваний, Σ listens_cnt", "SELECT sum(listens_cnt) FROM {t} WHERE {w}", 0,
     "агрегированный показатель, должен равняться числу фактов"),
    ("Σ unique_users по строкам", "SELECT sum(unique_users) FROM {t} WHERE {w}", 0, "сумма уникальных по строкам витрины"),
    ("Σ unique_tracks по строкам", "SELECT sum(unique_tracks) FROM {t} WHERE {w}", 0,
     "уникальные композиции (сумма по строкам витрины)"),
    ("Σ unique_artists по строкам", "SELECT sum(unique_artists) FROM {t} WHERE {w}", 0, "исполнители без 'unknown'"),
    ("Σ unknown_artist_cnt", "SELECT sum(unknown_artist_cnt) FROM {t} WHERE {w}", 0, "прослушивания без исполнителя"),
    ("Максимум listens_cnt в строке", "SELECT max(listens_cnt) FROM {t} WHERE {w}", 0, "самая «горячая» ячейка"),
    ("Σ avg_listens_per_user", "SELECT sum(avg_listens_per_user) FROM {t} WHERE {w}", 1e-6,
     "дробный показатель: Decimal(12,4) / float"),
    ("Σ city_day_share", "SELECT sum(city_day_share) FROM {t} WHERE {w}", 1e-6,
     "дробный показатель: Decimal(10,6) / float; ≈ числу пар (дата, город)"),
]


def _norm(v):
    """Приводит значения разных СУБД к сопоставимому виду: даты → ISO-строка, числа → float/int."""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    if hasattr(v, "isoformat"):
        return v.isoformat()[:10]
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return float(v)
    return v


def compare_metrics(sdt, edt, target: Target | None = None) -> pd.DataFrame:
    """Таблица «Проверяемый показатель | способ расчёта | PostgreSQL | целевая СУБД | расхождение | результат»."""
    t = target or get_target()
    rows = []
    for name, sql, tol, note in CHECKS:
        pg_sql = sql.format(t=PG_TABLE, w=f"dt BETWEEN '{sdt}' AND '{edt}'")
        tg_sql = sql.format(t=t.table, w=t.where(sdt, edt))
        a, b = _norm(db.query(pg_sql).iat[0, 0]), _norm(t.scalar(tg_sql))
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            diff = abs(a - b)
            ok = diff <= tol
        else:
            diff, ok = (0.0 if a == b else None), a == b
        if diff == 0:
            verdict = "Значения идентичны"
        elif ok and diff is not None:
            verdict = f"Совпадает в пределах допуска (Δ = {diff:.2e})"
        else:
            verdict = "Расхождение" + (f" Δ = {diff:.6g}" if diff is not None else "")
        rows.append({"Проверяемый показатель": name, "Способ расчёта": pg_sql.split(" WHERE")[0].replace(PG_TABLE, "<витрина>"),
                     "Пояснение": note, "PostgreSQL": a, t.name: b, "Расхождение": diff, "Допуск": tol,
                     "Результат": "Совпадает" if ok else "Не совпадает", "Вывод": verdict})
    return pd.DataFrame(rows)


def prep_mart(df: pd.DataFrame) -> pd.DataFrame:
    """Единый вид витрины для построчного сравнения: dt — строка, числа — float."""
    d = df.copy()
    d["dt"] = d["dt"].map(lambda v: _norm(v))
    for c in TOL:
        d[c] = d[c].map(lambda v: float(v) if v is not None else np.nan)
    return d[MART_COLS]


def compare_rows(pg: pd.DataFrame, tg: pd.DataFrame, tol: dict | None = None):
    """Построчное сопоставление двух витрин по ключу (dt, город, жанр).
    Возвращает (сводка, строки с расхождениями, сравнение по датам)."""
    tol = {**TOL, **(tol or {})}
    a, b = prep_mart(pg), prep_mart(tg)
    m = a.merge(b, on=KEY_COLS, how="outer", suffixes=("_pg", "_tg"), indicator=True)
    only_pg, only_tg = m[m["_merge"] == "left_only"], m[m["_merge"] == "right_only"]
    both = m[m["_merge"] == "both"]
    bad = pd.Series(False, index=both.index)
    per_col = {}
    for c, tl in tol.items():
        d = (both[c + "_pg"] - both[c + "_tg"]).abs()
        per_col[c] = {"расхождений": int((d > tl).sum()), "макс. отклонение": float(d.max()) if len(d) else 0.0}
        bad |= d > tl
    daily = (a.groupby("dt").agg(строк_pg=("listens_cnt", "size"), прослушиваний_pg=("listens_cnt", "sum"))
             .join(b.groupby("dt").agg(строк_tg=("listens_cnt", "size"), прослушиваний_tg=("listens_cnt", "sum")),
                   how="outer").fillna(0).reset_index())
    daily["совпадает"] = (daily["строк_pg"] == daily["строк_tg"]) & (daily["прослушиваний_pg"] == daily["прослушиваний_tg"])
    summary = {"rows_pg": len(a), "rows_target": len(b), "only_in_pg": len(only_pg), "only_in_target": len(only_tg),
               "rows_with_diff": int(bad.sum()), "per_column": per_col,
               "ok": not len(only_pg) and not len(only_tg) and not bad.any()}
    return summary, both[bad].head(200), daily
