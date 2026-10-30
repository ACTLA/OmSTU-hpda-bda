"""Проверки ЛР2 без PostgreSQL и ClickHouse: генератор, эталонная очистка, витрина, перенос во вторую СУБД (SQLite),
сопоставление витрин. Запуск:  python music_app/tests/check_lab2.py   (или pytest music_app/tests)
Если есть music_app/data/music_project.csv — профиль строится по нему, иначе по небольшому искусственному набору."""
import sqlite3
import sys
import tempfile
import types
from datetime import date
from decimal import Decimal
from pathlib import Path
from contextlib import closing

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
try:
    import psycopg2  # noqa: F401
except ImportError:                       # драйвер нужен только модулю db — для этих проверок подставляем заглушку
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from modules import analysis as an, compare_data as cd, db, generator as gen, quality as q, transfer as tr  # noqa: E402
from modules.config import DATA_PATH  # noqa: E402


def _source() -> pd.DataFrame:
    if DATA_PATH.exists():
        return an.load_raw(DATA_PATH)
    rng = np.random.default_rng(1)
    n = 20000
    users = np.array([f"{v:08X}" for v in rng.integers(0, 16 ** 8, 12000)], dtype=object)
    ti = rng.integers(0, 15000, n)
    df = pd.DataFrame({
        "userID": users[rng.integers(0, len(users), n)], "Track": [f"Track {i}" for i in ti],
        "artist": [f"Artist {i % 9000}" for i in ti], "genre": rng.choice(["pop", "rock", "dance", "jazz"], n),
        "City": rng.choice(["Moscow", "Saint-Petersburg"], n, p=[.7, .3]),
        "time": [f"{h:02d}:{m:02d}:{s:02d}" for h, m, s in zip(rng.choice([8, 9, 13, 14, 20, 21], n),
                                                              rng.integers(0, 60, n), rng.integers(0, 60, n))],
        "Day": rng.choice(["Friday", "Monday", "Wednesday"], n)})
    m = df["userID"].str.startswith("0")
    df.loc[m, "userID"] = df.loc[m, "userID"].str.lstrip("0")
    return df


PROF = gen.build_profile(_source())


def test_generator_volume_period_and_domain():
    cfg = gen.make_config("source", n_rows=7000, batch_id=1, dt_start=date(2025, 7, 7), dt_end=date(2025, 8, 3))
    df, meta = gen.generate_batch(cfg, PROF)
    assert len(df) == 7000 and meta["rows"] == 7000
    assert df["dt"].min() >= "2025-07-07" and df["dt"].max() <= "2025-08-03"
    wd = pd.to_datetime(df["dt"]).dt.day_name()
    assert ((wd == df["Day"]) | df["Day"].isna()).all(), "день недели должен соответствовать dt"
    assert set(df["Day"].dropna()) <= set(PROF.days)
    assert set(df["City"].dropna()) <= set(PROF.cities)
    g2, _ = gen.generate_batch(cfg, PROF)
    assert df.equals(g2), "одинаковый seed — одинаковые данные"
    other, _ = gen.generate_batch(gen.make_config("source", n_rows=7000, batch_id=1, seed=7), PROF)
    assert not df.equals(other)


def test_generator_batches_have_different_periods_and_ids():
    import tempfile as tf
    with tf.TemporaryDirectory() as d:
        paths = gen.generate_series(PROF, n_batches=2, rows=3000, out_dir=Path(d), correction=True)
        assert len(paths) == 3
        b1, b2 = (gen.read_batch(p) for p in paths[:2])
        assert b1["dt"].max() < b2["dt"].min(), "периоды партий не пересекаются"
        assert set(b1["batch_id"]) == {"1"} and set(b2["batch_id"]) == {"2"}
        corr = gen.read_batch(paths[2])
        assert set(corr["batch_id"]) == {"3"} and corr["dt"].between(b1["dt"].min(), b1["dt"].max()).all()


def test_injected_problems_match_configuration():
    cfg = gen.make_config("dirty", n_rows=20000, batch_id=2, dt_start=date(2025, 8, 4), dt_end=date(2025, 8, 31))
    df, meta = gen.generate_batch(cfg, PROF)
    g = meta["generated"]
    assert abs(g["full_duplicates"] - 2000) <= 1 and g["duplicates_actual"] >= g["full_duplicates"] - 5
    n_base = 20000 - g["full_duplicates"]                           # доли задаются от уникальных строк
    for k, p in (("invalid_userid", .01), ("invalid_city", .01), ("invalid_day", .01), ("invalid_time", .01),
                 ("miss_artist", .20)):
        assert abs(g[k] / n_base - p) < 0.006, k
    _, log = q.clean_reference(df)
    t = log.drop(columns=q.KEY).sum()
    assert t["rej_userid"] > 0 and t["rej_city"] > 0 and t["rej_day"] > 0 and t["rej_time"] > 0
    assert t["fix_trim_track"] > 0 and t["fix_userid_pad"] > 0 and t["unk_artist"] > 0


def test_reference_cleaner_balance_and_formats():
    df, _ = gen.generate_batch(gen.make_config("dirty", n_rows=6000, batch_id=1), PROF)
    clean, log = q.clean_reference(df)
    t = log.drop(columns=q.KEY).sum()
    assert t["rows_stg"] == 6000
    assert t["rows_stg"] - (t["rej_userid"] + t["rej_city"] + t["rej_day"] + t["rej_time"]) == t["rows_valid"]
    assert t["rows_valid"] - t["dup_removed"] - t["superseded"] == t["rows_ods"] == len(clean)
    assert (clean["user_id"].str.len() == 8).all() and clean["user_id"].str.fullmatch(r"[0-9A-F]{8}").all()
    assert not clean[["track", "artist", "genre"]].isna().any().any()
    assert (clean["track"] == clean["track"].str.strip()).all()
    assert not clean.drop(columns="batch_id").duplicated().any()


def test_truncated_userids_are_restored_exactly():
    cfg = gen.make_config("clean", n_rows=4000, batch_id=1)
    base, _ = gen.generate_batch(cfg, PROF)
    cut = base.copy()
    m = cut["userID"].str.startswith("0")
    cut.loc[m, "userID"] = cut.loc[m, "userID"].str.lstrip("0")
    assert m.any()
    a, _ = q.clean_reference(base)
    b, _ = q.clean_reference(cut)
    key = ["user_id", "listen_time", "dt", "track", "artist"]
    assert a.sort_values(key).reset_index(drop=True)[key].equals(b.sort_values(key).reset_index(drop=True)[key])


def test_reload_same_batch_is_idempotent_and_correction_supersedes():
    b1, _ = gen.generate_batch(gen.make_config("source", n_rows=5000, batch_id=1), PROF)
    c1, _ = q.clean_reference(b1)
    c2, _ = q.clean_reference(pd.concat([b1, b1], ignore_index=True))      # повторная загрузка той же партии
    assert len(c1) == len(c2)
    corr, meta = gen.make_correction(b1, PROF, 3)
    cc, log = q.clean_reference(pd.concat([b1, corr], ignore_index=True))
    assert len(cc) == len(c1), "исправление заменяет записи, а не добавляет"
    assert int(log["superseded"].sum()) == len(corr) == int((cc["batch_id"] == 3).sum())
    old = c1.merge(cc, on=q.EVENT_KEY, suffixes=("_old", "_new"))
    changed = (old[["track_old", "artist_old", "genre_old", "city_old"]].to_numpy()
               != old[["track_new", "artist_new", "genre_new", "city_new"]].to_numpy()).any(axis=1)
    assert changed.sum() >= len(corr) * 0.95, "значения в исправленных записях действительно изменились"


# ---------- перенос витрины: «PostgreSQL» эмулируется SQLite с присоединённой схемой dwh ----------
def _fake_pg(mart: pd.DataFrame):
    con = sqlite3.connect(":memory:")
    con.execute("ATTACH DATABASE ':memory:' AS dwh")
    mart.to_sql("mart_tmp", con, index=False)
    con.execute(f"CREATE TABLE dwh.{tr.MART_TABLE} AS SELECT * FROM main.mart_tmp")
    return con


def _patch_db(con):
    def fetch_rows(sql, params=None):
        cur = con.execute(sql.replace("%s", "?"), params or ())
        cols = [d[0] for d in cur.description]
        out = []
        for r in cur.fetchall():
            r = list(r)
            r[0] = date.fromisoformat(r[0])                         # как psycopg2: DATE → date
            for c in ("avg_listens_per_user", "city_day_share"):
                r[cols.index(c)] = Decimal(str(r[cols.index(c)]))   # NUMERIC → Decimal
            out.append(tuple(r))
        return cols, out

    db.fetch_rows = fetch_rows
    db.query = lambda sql, params=None: pd.read_sql_query(sql, con)


def test_transfer_roundtrip_repeat_and_detection_of_differences():
    df, _ = gen.generate_batch(gen.make_config("source", n_rows=8000, batch_id=1), PROF)
    clean, _ = q.clean_reference(df)
    mart = q.build_mart(clean)
    con = _fake_pg(mart)
    _patch_db(con)
    with tempfile.TemporaryDirectory() as d:
        t = tr.SQLiteTarget(Path(d) / "t.sqlite3")
        s, e = mart["dt"].min(), mart["dt"].max()
        r1 = tr.transfer_mart(s, e, t)
        assert r1["rows_read"] == len(mart) == r1["rows_in_target"]
        r2 = tr.transfer_mart(s, e, t)                              # повторный перенос не создаёт дублей
        assert r2["rows_in_target"] == len(mart)
        cmp_ = tr.compare_metrics(s, e, t)
        assert (cmp_["Результат"] == "Совпадает").all(), cmp_[cmp_["Результат"] != "Совпадает"]
        assert int(cmp_.loc[cmp_["Проверяемый показатель"].str.startswith("Всего прослушиваний"), "PostgreSQL"].iat[0]) == len(clean)
        pg = db.query("SELECT * FROM dwh.mart_listening_daily")
        tg = t.query(f"SELECT * FROM {t.table}")
        summ, _, daily = tr.compare_rows(pg, tg)
        assert summ["ok"] and daily["совпадает"].all()
        # намеренно портим целевую витрину: проверка должна это заметить
        with closing(sqlite3.connect(t.path)) as c:
            c.execute(f"UPDATE {t.table} SET listens_cnt = listens_cnt + 1 WHERE rowid = 1")
            c.execute(f"DELETE FROM {t.table} WHERE rowid = 2")
            c.commit()
        bad = tr.compare_metrics(s, e, t)
        assert (bad["Результат"] == "Не совпадает").any()
        summ2, rows, _ = tr.compare_rows(pg, t.query(f"SELECT * FROM {t.table}"))
        assert not summ2["ok"] and summ2["only_in_pg"] == 1 and summ2["rows_with_diff"] >= 1
        tr.transfer_mart(s, e, t)                                   # повторный перенос чинит расхождение
        assert (tr.compare_metrics(s, e, t)["Результат"] == "Совпадает").all()


def test_mart_properties():
    df, _ = gen.generate_batch(gen.make_config("source", n_rows=6000, batch_id=1), PROF)
    clean, _ = q.clean_reference(df)
    m = q.build_mart(clean)
    assert m["listens_cnt"].sum() == len(clean)
    assert not m.duplicated(subset=tr.KEY_COLS).any()
    s = m.groupby(["dt", "city_name"])["city_day_share"].sum()
    assert (s.sub(1).abs() < 1e-3).all()
    assert (m["unique_users"] <= m["listens_cnt"]).all() and (m["unknown_artist_cnt"] <= m["listens_cnt"]).all()


def test_comparison_helpers():
    orig = _source().drop(columns=["Unnamed: 0"], errors="ignore")
    syn, _ = gen.generate_batch(gen.make_config("source", n_rows=5000, batch_id=1), PROF)
    prof = cd.profile_table({"orig": orig, "syn": syn}, ref_name="orig")
    assert prof.loc["Записей", "syn"] == 5000 and "Треки вне исходного набора, %" in prof.index
    assert 5 < prof.loc["Пропуски artist, %", "syn"] < 20
    tbl, tvd = cd.category_shares(orig["genre"], orig["genre"])
    assert tvd == 0 and (tbl["Разница, п.п."] == 0).all()
    _, tvd2 = cd.category_shares(orig["genre"], syn["genre"])
    assert 0 <= tvd2 < 0.2
    hs, htvd = cd.hour_shares(orig, syn)
    assert htvd < 0.1 and abs(hs["Исходный, %"].sum() - 100) < 0.5
    stg = pd.Series(dict(rows=100, miss_userid=1, rej_city=2, rej_day=3, rej_time=4, short_userid=6, miss_track=5, miss_artist=11,
                         miss_genre=2, ws_track=1, ws_artist=2, full_dups=7))
    ods = pd.Series(dict(rows=80, bad_len_userid=0, unk_track=4, unk_artist=9, unk_genre=2, ws_any=0, bad_day=0,
                         full_dups=0, multi_version_events=0))
    dq = pd.Series(dict(rows_stg=100, rej_userid=1, rej_city=2, rej_day=3, rej_time=4, rows_valid=90, fix_userid_pad=6,
                        fix_trim_track=1, fix_trim_artist=3, unk_track=5, unk_artist=11, unk_genre=2, dup_removed=8,
                        superseded=2, rows_ods=80))
    ba = cd.before_after(stg, ods, dq)
    st = dict(zip(ba["Проблема"], ba["Сверка с журналом"]))
    assert st["Пробелы по краям artist"] == "✗ расхождение" and st["Нет City"] == "✓ совпадает"
    wf = cd.waterfall_steps(dq)
    assert wf["Записей"].iloc[:-1].sum() == wf["Записей"].iloc[-1]


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("OK  ", name)
            except Exception as ex:                                    # noqa: BLE001
                fails += 1
                import traceback
                traceback.print_exc()
                print("FAIL", name, "-", ex)
    sys.exit(1 if fails else 0)
