"""Запросы к хранилищу для страницы «Анализ и сопоставление данных» (ВОиАД, ЛР2).
Все показатели считаются по фактическим данным слоёв и журнала качества ods.dq_log, без заготовленных значений."""
import pandas as pd

from . import db

FILTER = "batch_id = ANY(%(b)s) AND dt BETWEEN %(d1)s AND %(d2)s"
DAYS = "('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')"
TIME_RX = "'^([01]?[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$'"
FREQ = {"день": "day", "неделя": "week", "месяц": "month"}


def _p(batches, d1, d2):
    return {"b": [int(x) for x in batches], "d1": str(d1), "d2": str(d2)}


def batches_info() -> pd.DataFrame:
    """Партии в источнике: объёмы по слоям и период dt."""
    return db.query("""
        WITH s AS (SELECT batch_id, count(*) AS src_rows, min(dt) AS dt_min, max(dt) AS dt_max
                   FROM src.music_tracks GROUP BY 1),
             g AS (SELECT batch_id, count(*) AS stg_rows FROM stg.music_tracks GROUP BY 1),
             o AS (SELECT batch_id, count(*) AS ods_rows FROM ods.music_tracks GROUP BY 1)
        SELECT s.batch_id, s.src_rows, COALESCE(g.stg_rows, 0) AS stg_rows, COALESCE(o.ods_rows, 0) AS ods_rows,
               s.dt_min, s.dt_max
        FROM s LEFT JOIN g ON g.batch_id = s.batch_id LEFT JOIN o ON o.batch_id = s.batch_id ORDER BY 1""")


def layer_counts(batches, d1, d2) -> pd.DataFrame:
    """Записей по слоям и партиям при заданных фильтрах."""
    sql = " UNION ALL ".join(
        f"SELECT '{name}' AS layer, batch_id, count(*) AS rows FROM {tbl} WHERE {FILTER} GROUP BY 2"
        for name, tbl in (("SOURCE", "src.music_tracks"), ("STAGING", "stg.music_tracks"), ("ODS", "ods.music_tracks")))
    return db.query(sql, _p(batches, d1, d2))


def dq_by_batch(batches, d1, d2) -> pd.DataFrame:
    """Журнал качества ods.dq_log, просуммированный по партиям."""
    return db.query(f"""
        SELECT batch_id, count(*) AS dates, sum(rows_stg) AS rows_stg, sum(rej_userid) AS rej_userid,
               sum(rej_city) AS rej_city, sum(rej_day) AS rej_day, sum(rej_time) AS rej_time,
               sum(rows_valid) AS rows_valid, sum(fix_userid_pad) AS fix_userid_pad,
               sum(fix_trim_track) AS fix_trim_track, sum(fix_trim_artist) AS fix_trim_artist,
               sum(unk_track) AS unk_track, sum(unk_artist) AS unk_artist, sum(unk_genre) AS unk_genre,
               sum(dup_removed) AS dup_removed, sum(superseded) AS superseded, sum(rows_ods) AS rows_ods,
               max(loaded_at) AS loaded_at
        FROM ods.dq_log WHERE {FILTER} GROUP BY batch_id ORDER BY batch_id""", _p(batches, d1, d2))


def stg_quality(batches, d1, d2) -> pd.DataFrame:
    """Качество ДО очистки: измерение по STAGING (независимо от журнала)."""
    return db.query(f"""
        SELECT count(*) AS rows,
          count(*) FILTER (WHERE NULLIF(BTRIM(userid), '') IS NULL) AS miss_userid,
          count(*) FILTER (WHERE NULLIF(BTRIM(userid), '') IS NOT NULL
                             AND NULLIF(BTRIM(city), '') IS NULL) AS rej_city,
          count(*) FILTER (WHERE NULLIF(BTRIM(userid), '') IS NOT NULL AND NULLIF(BTRIM(city), '') IS NOT NULL
                             AND NOT COALESCE(BTRIM("day") IN {DAYS}, FALSE)) AS rej_day,
          count(*) FILTER (WHERE NULLIF(BTRIM(userid), '') IS NOT NULL AND NULLIF(BTRIM(city), '') IS NOT NULL
                             AND COALESCE(BTRIM("day") IN {DAYS}, FALSE)
                             AND NOT COALESCE(BTRIM("time") ~ {TIME_RX}, FALSE)) AS rej_time,
          count(*) FILTER (WHERE NULLIF(BTRIM(userid), '') IS NOT NULL AND length(BTRIM(userid)) < 8) AS short_userid,
          count(*) FILTER (WHERE NULLIF(BTRIM(track), '')  IS NULL) AS miss_track,
          count(*) FILTER (WHERE NULLIF(BTRIM(artist), '') IS NULL) AS miss_artist,
          count(*) FILTER (WHERE NULLIF(BTRIM(genre), '')  IS NULL) AS miss_genre,
          count(*) FILTER (WHERE track  IS NOT NULL AND track  <> BTRIM(track))  AS ws_track,
          count(*) FILTER (WHERE artist IS NOT NULL AND artist <> BTRIM(artist)) AS ws_artist,
          (SELECT COALESCE(sum(c - 1), 0) FROM (
              SELECT count(*) AS c FROM stg.music_tracks WHERE {FILTER}
              GROUP BY userid, track, artist, genre, city, "time", "day", dt, batch_id) t) AS full_dups
        FROM stg.music_tracks WHERE {FILTER}""", _p(batches, d1, d2))


def ods_quality(batches, d1, d2) -> pd.DataFrame:
    """Качество ПОСЛЕ очистки: измерение по ODS."""
    return db.query(f"""
        SELECT count(*) AS rows,
          count(*) FILTER (WHERE length(user_id) <> 8) AS bad_len_userid,
          count(*) FILTER (WHERE track  = 'unknown') AS unk_track,
          count(*) FILTER (WHERE artist = 'unknown') AS unk_artist,
          count(*) FILTER (WHERE genre  = 'unknown') AS unk_genre,
          count(*) FILTER (WHERE track <> BTRIM(track) OR artist <> BTRIM(artist)) AS ws_any,
          count(*) FILTER (WHERE day_of_week NOT IN {DAYS}) AS bad_day,
          (SELECT COALESCE(sum(c - 1), 0) FROM (
              SELECT count(*) AS c FROM ods.music_tracks WHERE {FILTER}
              GROUP BY user_id, track, artist, genre, city, listen_time, day_of_week, dt) t) AS full_dups,
          (SELECT count(*) FROM (
              SELECT 1 FROM ods.music_tracks WHERE {FILTER}
              GROUP BY user_id, listen_time, dt HAVING count(DISTINCT batch_id) > 1) t) AS multi_version_events
        FROM ods.music_tracks WHERE {FILTER}""", _p(batches, d1, d2))


def rows_by_period(batches, d1, d2, freq: str) -> pd.DataFrame:
    """Динамика объёма по синтетической дате dt: SOURCE / STAGING / ODS."""
    unit = FREQ[freq]
    sql = " UNION ALL ".join(
        f"SELECT '{name}' AS layer, date_trunc('{unit}', dt)::date AS period, batch_id, count(*) AS rows "
        f"FROM {tbl} WHERE {FILTER} GROUP BY 2, 3"
        for name, tbl in (("SOURCE", "src.music_tracks"), ("STAGING", "stg.music_tracks"), ("ODS", "ods.music_tracks")))
    return db.query(sql + " ORDER BY 2, 1", _p(batches, d1, d2))


def reject_examples(batches, d1, d2, limit=200) -> pd.DataFrame:
    return db.query(f"""SELECT dt, batch_id, src_index, reason, userid, track, artist, genre, city, "time", "day"
        FROM ods.dq_reject WHERE {FILTER} ORDER BY dt, src_index LIMIT {int(limit)}""", _p(batches, d1, d2))


def reject_counts(batches, d1, d2) -> pd.DataFrame:
    return db.query(f"SELECT reason, count(*) AS rows FROM ods.dq_reject WHERE {FILTER} GROUP BY 1 ORDER BY 2 DESC",
                    _p(batches, d1, d2))


def fetch_src(batches, d1, d2) -> pd.DataFrame:
    """Записи SOURCE (как хранятся) в формате исходного CSV — для профилирования и сравнения наборов."""
    return db.query(f"""SELECT src_index, userid AS "userID", track AS "Track", artist, genre, city AS "City",
        "time", "day" AS "Day", dt, batch_id FROM src.music_tracks WHERE {FILTER}""", _p(batches, d1, d2))


def dwh_relations() -> pd.DataFrame:
    """Таблицы DWH и «эквивалент» в ODS (то, чему должно соответствовать число строк)."""
    return db.query("""
        SELECT 'fact_listening' AS tbl, 'одно прослушивание' AS grain, (SELECT count(*) FROM dwh.fact_listening) AS rows,
               (SELECT count(*) FROM ods.music_tracks) AS ods_equiv, 'равно'::text AS rule
        UNION ALL SELECT 'dim_user', 'пользователь', (SELECT count(*) FROM dwh.dim_user),
               (SELECT count(DISTINCT user_id) FROM ods.music_tracks), 'не меньше'
        UNION ALL SELECT 'dim_track', 'пара (трек, исполнитель)', (SELECT count(*) FROM dwh.dim_track),
               (SELECT count(*) FROM (SELECT DISTINCT track, artist FROM ods.music_tracks) t), 'не меньше'
        UNION ALL SELECT 'dim_artist', 'исполнитель', (SELECT count(*) FROM dwh.dim_artist),
               (SELECT count(DISTINCT artist) FROM ods.music_tracks), 'не меньше'
        UNION ALL SELECT 'dim_genre', 'жанр', (SELECT count(*) FROM dwh.dim_genre),
               (SELECT count(DISTINCT genre) FROM ods.music_tracks), 'не меньше'
        UNION ALL SELECT 'dim_city', 'город', (SELECT count(*) FROM dwh.dim_city),
               (SELECT count(DISTINCT city) FROM ods.music_tracks), 'не меньше'
        UNION ALL SELECT 'dim_date', 'календарная дата', (SELECT count(*) FROM dwh.dim_date),
               (SELECT count(DISTINCT dt) FROM ods.music_tracks), 'не меньше'
        UNION ALL SELECT 'dim_month', 'месяц', (SELECT count(*) FROM dwh.dim_month),
               (SELECT count(DISTINCT to_char(dt, 'YYYYMM')) FROM ods.music_tracks), 'не меньше'
        UNION ALL SELECT 'dim_time', 'секунда суток', (SELECT count(*) FROM dwh.dim_time),
               (SELECT count(DISTINCT listen_time) FROM ods.music_tracks), 'не меньше'""")


def facts_in_period(d1, d2) -> int:
    return int(db.query("""SELECT count(*) FROM dwh.fact_listening f JOIN dwh.dim_date d ON d.date_key = f.date_key
        WHERE d.dt BETWEEN %(d1)s AND %(d2)s""", {"d1": str(d1), "d2": str(d2)}).iat[0, 0])


def dates_by_layer(batches, d1, d2) -> dict:
    """Сколько различных дат dt загружено в каждый слой (покрытие периода)."""
    r = db.query(f"""SELECT
        (SELECT count(DISTINCT dt) FROM src.music_tracks WHERE {FILTER}) AS src,
        (SELECT count(DISTINCT dt) FROM stg.music_tracks WHERE {FILTER}) AS stg,
        (SELECT count(DISTINCT dt) FROM ods.dq_log WHERE {FILTER}) AS ods_journal,
        (SELECT count(DISTINCT dt) FROM ods.music_tracks WHERE {FILTER}) AS ods,
        (SELECT count(DISTINCT dt) FROM dwh.mart_listening_daily WHERE dt BETWEEN %(d1)s AND %(d2)s) AS mart""",
                 _p(batches, d1, d2))
    return {k: int(v) for k, v in r.iloc[0].items()}


def mart_pg(d1, d2) -> pd.DataFrame:
    cols = "dt, month_key, day_of_week, city_name, genre_name, listens_cnt, unique_users, unique_tracks, " \
           "unique_artists, unknown_artist_cnt, avg_listens_per_user, city_day_share"
    return db.query(f"SELECT {cols} FROM dwh.mart_listening_daily WHERE dt BETWEEN %(d1)s AND %(d2)s "
                    f"ORDER BY dt, city_name, genre_name", {"d1": str(d1), "d2": str(d2)})


def run_log(limit=20) -> pd.DataFrame:
    return db.query(f"""SELECT run_id, loaded_at, period_start, period_end, rows_stg, rows_ods, duration_ms
        FROM ods.load_run_log ORDER BY run_id DESC LIMIT {int(limit)}""")
