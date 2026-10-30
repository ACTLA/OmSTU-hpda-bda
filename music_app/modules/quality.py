"""Эталонная реализация правил очистки и витрины на pandas (без БД).

Зеркалит SQL: ods.load_ods (очистка, журнал качества) и dwh.load_mart (витрина).
Нужна для независимой сверки: результат SQL должен совпасть с результатом этого модуля.
"""
import numpy as np
import pandas as pd

from .config import DAY_ORDER

TIME_RE = r"([01]?[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"   # как в ods.load_ods
KEY = ["dt", "batch_id"]
LOG_COLS = ["dt", "batch_id", "rows_stg", "rej_userid", "rej_city", "rej_day", "rej_time", "rows_valid",
            "fix_userid_pad", "fix_trim_track", "fix_trim_artist", "unk_track", "unk_artist", "unk_genre",
            "dup_removed", "superseded", "rows_ods"]
CLEAN_COLS = ["user_id", "track", "artist", "genre", "city", "listen_time", "day_of_week", "dt", "batch_id"]
EVENT_KEY = ["user_id", "listen_time", "dt"]   # ключ события: пользователь + время + дата


def _trim(s: pd.Series) -> pd.Series:
    """BTRIM из PostgreSQL: обрезает только пробелы; пропуск остаётся пропуском."""
    return s.astype("object").str.strip(" ")


def _nullif_blank(s: pd.Series) -> pd.Series:
    s = _trim(s)
    return s.where(s.notna() & (s != ""), np.nan)


def clean_reference(df: pd.DataFrame):
    """df: userID, Track, artist, genre, City, time, Day, dt, batch_id.
    Возвращает (очищенные записи ODS, журнал качества по (dt, batch_id))."""
    d = df.reset_index(drop=True)
    u, t, a, g, c = (_nullif_blank(d[x]) for x in ("userID", "Track", "artist", "genre", "City"))
    day, tm = _trim(d["Day"]), _trim(d["time"])
    ok_day = day.isin(DAY_ORDER)
    ok_time = tm.fillna("").str.fullmatch(TIME_RE).astype(bool)

    f = pd.DataFrame({"dt": d["dt"].astype(str), "batch_id": d["batch_id"].astype(int)})
    bad_user, bad_city, bad_day, bad_time = u.isna(), c.isna(), ~ok_day, ~ok_time
    f["rows_stg"] = 1
    f["rej_userid"] = bad_user
    f["rej_city"] = ~bad_user & bad_city
    f["rej_day"] = ~bad_user & ~bad_city & bad_day
    f["rej_time"] = ~bad_user & ~bad_city & ~bad_day & bad_time
    valid = ~(bad_user | bad_city | bad_day | bad_time)
    f["rows_valid"] = valid
    f["fix_userid_pad"] = u.notna() & (u.str.len() < 8)
    f["fix_trim_track"] = d["Track"].notna() & (d["Track"].astype("object") != _trim(d["Track"]))
    f["fix_trim_artist"] = d["artist"].notna() & (d["artist"].astype("object") != _trim(d["artist"]))
    f["unk_track"], f["unk_artist"], f["unk_genre"] = t.isna(), a.isna(), g.isna()

    parts = tm.where(ok_time, np.nan).str.extract(r"^(\d{1,2}):(\d{2}):(\d{2})$")
    n = pd.DataFrame({
        "user_id": u.where(u.isna() | (u.str.len() >= 8), u.str.pad(8, side="left", fillchar="0")),
        "track": t.fillna("unknown"), "artist": a.fillna("unknown"), "genre": g.fillna("unknown"),
        "city": c, "listen_time": parts[0].str.zfill(2) + ":" + parts[1] + ":" + parts[2],
        "day_of_week": day, "dt": f["dt"], "batch_id": f["batch_id"],
    })[valid]

    distinct = n.drop_duplicates()                                    # полные дубликаты после очистки
    newest = distinct.groupby(EVENT_KEY)["batch_id"].transform("max")
    final = distinct[distinct["batch_id"] == newest]                  # новая версия события заменяет старую

    cnt = [c_ for c_ in LOG_COLS if c_ not in KEY + ["dup_removed", "superseded", "rows_ods"]]
    log = f.groupby(KEY)[cnt].sum().astype(int)
    log["dup_removed"] = log["rows_valid"] - distinct.groupby(KEY).size().reindex(log.index, fill_value=0)
    log["rows_ods"] = final.groupby(KEY).size().reindex(log.index, fill_value=0)
    log["superseded"] = log["rows_valid"] - log["dup_removed"] - log["rows_ods"]
    return final.reset_index(drop=True)[CLEAN_COLS], log.reset_index()[LOG_COLS]


def build_mart(clean: pd.DataFrame) -> pd.DataFrame:
    """Витрина dt × город × жанр (как dwh.mart_listening_daily) из очищенных записей."""
    c = clean.copy()
    c["dt"] = c["dt"].astype(str)
    c["tk"] = c["track"] + "\x1f" + c["artist"]
    c["art_known"] = c["artist"].where(c["artist"] != "unknown", np.nan)
    c["is_unk"] = (c["artist"] == "unknown").astype(int)
    m = c.groupby(["dt", "city", "genre"]).agg(
        listens_cnt=("user_id", "size"), unique_users=("user_id", "nunique"), unique_tracks=("tk", "nunique"),
        unique_artists=("art_known", "nunique"), unknown_artist_cnt=("is_unk", "sum")).reset_index()
    m = m.rename(columns={"city": "city_name", "genre": "genre_name"})
    m["month_key"] = m["dt"].str[:4].astype(int) * 100 + m["dt"].str[5:7].astype(int)
    m["day_of_week"] = pd.to_datetime(m["dt"]).dt.day_name()
    m["avg_listens_per_user"] = (m["listens_cnt"] / m["unique_users"]).round(4)
    m["city_day_share"] = (m["listens_cnt"] / m.groupby(["dt", "city_name"])["listens_cnt"].transform("sum")).round(6)
    cols = ["dt", "month_key", "day_of_week", "city_name", "genre_name", "listens_cnt", "unique_users",
            "unique_tracks", "unique_artists", "unknown_artist_cnt", "avg_listens_per_user", "city_day_share"]
    return m[cols]
