"""Оркестрация ЛР2: генерация → загрузка партий → SOURCE → STAGING → ODS → DWH → витрина → вторая СУБД.
Используется командной строкой (cli.py); те же функции пригодятся интерфейсу управления загрузками (ЛР3)."""
from datetime import timedelta
from pathlib import Path

import pandas as pd

from . import analysis as an
from . import db, generator as gen, monitor, quality as q, transfer as tr
from .config import DATA_PATH, DT_START, DT_WEEKS, SYNTH_DIR


def original_period():
    return DT_START, DT_START + timedelta(weeks=DT_WEEKS) - timedelta(days=1)


def process_period(sdt, edt, with_transfer=True, log=print) -> dict:
    """Прогоняет период через все слои. Каждый шаг идемпотентен."""
    res = {"period": (sdt, edt)}
    res["stg"] = db.run_staging()
    log(f"  STAGING: {res['stg']:,} строк (перезалито as is)")
    res["ods"] = db.run_ods(sdt, edt)
    log(f"  ODS {sdt} … {edt}: {res['ods']:,} строк")
    res["fact"] = db.run_dwh(sdt, edt)
    log(f"  DWH факты: {res['fact']:,} строк")
    res["mart"] = db.run_mart(sdt, edt)
    log(f"  Витрина в PostgreSQL: {res['mart']:,} строк")
    if with_transfer:
        res["transfer"] = tr.transfer_mart(sdt, edt)
        t = res["transfer"]
        log(f"  Перенос в {t['target']}: прочитано {t['rows_read']:,}, в целевой таблице за период {t['rows_in_target']:,}")
    return res


def load_original(with_transfer=True, log=print) -> dict:
    log(f"Исходный CSV {DATA_PATH.name} → партия 0")
    n = db.load_source()
    log(f"  SOURCE: {n:,} строк")
    return process_period(*original_period(), with_transfer=with_transfer, log=log)


def load_batch_file(path, with_transfer=True, log=print) -> dict:
    """Загружает CSV партии в SOURCE (без пересоздания хранилища) и прогоняет её период по слоям."""
    info = db.load_batch(path)
    log(f"Партия {info['batch_id']} ({Path(path).name}): SOURCE +{info['rows']:,} строк, dt {info['dt_min']} … {info['dt_max']}")
    res = process_period(info["dt_min"], info["dt_max"], with_transfer=with_transfer, log=log)
    return {**info, **res}


def batch_files(directory=SYNTH_DIR):
    return sorted(Path(directory).glob("batch_*.csv"))


def full_period():
    r = db.query("SELECT min(dt) AS a, max(dt) AS b FROM src.music_tracks")
    return r.iat[0, 0], r.iat[0, 1]


# ---------------------------------------------------------------- проверки
def snapshot(sdt, edt, target=None) -> dict:
    """Числа, по которым определяется, изменилось ли хранилище после повторной загрузки."""
    p = {"a": sdt, "b": edt}
    s = {
        "ods_rows": int(db.query("SELECT count(*) FROM ods.music_tracks WHERE dt BETWEEN %(a)s AND %(b)s", p).iat[0, 0]),
        "ods_hash": int(db.query("""SELECT COALESCE(sum(hashtext(user_id || track || artist || genre || city ||
            listen_time::text || day_of_week || dt::text || batch_id::text)::BIGINT), 0)
            FROM ods.music_tracks WHERE dt BETWEEN %(a)s AND %(b)s""", p).iat[0, 0]),
        "fact_rows": monitor.facts_in_period(sdt, edt),
        "mart_rows": int(db.query("SELECT count(*) FROM dwh.mart_listening_daily WHERE dt BETWEEN %(a)s AND %(b)s", p).iat[0, 0]),
        "mart_listens": int(db.query("SELECT COALESCE(sum(listens_cnt), 0) FROM dwh.mart_listening_daily "
                                     "WHERE dt BETWEEN %(a)s AND %(b)s", p).iat[0, 0]),
    }
    t = target or tr.get_target()
    s["target_rows"] = int(t.scalar(f"SELECT count(*) FROM {t.table} WHERE {t.where(sdt, edt)}"))
    return s


def rerun_check(sdt, edt, log=print) -> dict:
    """Повторная загрузка за тот же период не должна менять данные (нет дублей)."""
    log(f"Повторная загрузка за {sdt} … {edt}")
    before = snapshot(sdt, edt)
    process_period(sdt, edt, log=lambda *_: None)
    after = snapshot(sdt, edt)
    diff = {k: (before[k], after[k]) for k in before if before[k] != after[k]}
    return {"before": before, "after": after, "diff": diff, "ok": not diff}


def check_against_reference(sdt, edt) -> dict:
    """Независимая сверка SQL с эталоном на pandas: журнал качества, содержимое ODS, витрина."""
    p = {"a": sdt, "b": edt}
    src = db.query("""SELECT src_index, userid AS "userID", track AS "Track", artist, genre, city AS "City",
        "time", "day" AS "Day", dt, batch_id FROM src.music_tracks WHERE dt BETWEEN %(a)s AND %(b)s""", p)
    clean, log_ref = q.clean_reference(src)
    log_db = db.query("SELECT * FROM ods.dq_log WHERE dt BETWEEN %(a)s AND %(b)s", p)
    log_db["dt"] = log_db["dt"].astype(str)
    m = log_ref.merge(log_db[q.LOG_COLS], on=q.KEY, how="outer", suffixes=("_ref", "_db"), indicator=True)
    both = m[m["_merge"] == "both"]
    log_bad = [c for c in q.LOG_COLS if c not in q.KEY and (both[c + "_ref"] != both[c + "_db"]).any()]
    ods = db.query("""SELECT user_id, track, artist, genre, city, listen_time::text AS listen_time, day_of_week,
        dt::text AS dt, batch_id FROM ods.music_tracks WHERE dt BETWEEN %(a)s AND %(b)s""", p)
    ods["listen_time"] = ods["listen_time"].str.slice(0, 8)
    cm = clean.merge(ods, how="outer", on=q.CLEAN_COLS, indicator=True)
    ods_diff = int((cm["_merge"] != "both").sum())
    mart_db = db.query("""SELECT * FROM dwh.mart_listening_daily WHERE dt BETWEEN %(a)s AND %(b)s""", p)[tr.MART_COLS]
    ms, _, _ = tr.compare_rows(mart_db, q.build_mart(clean), tol={"avg_listens_per_user": 2e-4, "city_day_share": 2e-6})
    return {"src_rows": len(src), "ods_ref": len(clean), "ods_db": len(ods), "log_groups_only_one_side": int((m["_merge"] != "both").sum()),
            "log_columns_mismatch": log_bad, "ods_rows_mismatch": ods_diff, "mart": ms,
            "ok": not log_bad and not ods_diff and ms["ok"] and not (m["_merge"] != "both").sum()}


def status_table() -> pd.DataFrame:
    return monitor.batches_info()


def demo(rows=15000, preset="source", with_transfer=True, log=print) -> dict:
    """Полный сценарий: схема → исходные данные → 2 синтетические партии + исправление → проверки."""
    out = {}
    db.init_schema()
    log("Схема БД создана/обновлена")
    if int(db.query("SELECT count(*) FROM src.music_tracks WHERE batch_id = 0").iat[0, 0]) == 0:
        out["original"] = load_original(with_transfer, log)
    files = batch_files()
    if not files:
        prof = gen.build_profile(an.load_raw(DATA_PATH))
        files = gen.generate_series(prof, n_batches=2, rows=rows, preset=preset, correction=True)
        log(f"Сгенерировано партий: {len(files)}")
    out["batches"] = [load_batch_file(f, with_transfer, log) for f in files]
    return out
