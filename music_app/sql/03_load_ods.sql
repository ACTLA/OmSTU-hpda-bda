-- STAGING -> ODS: инкрементальная загрузка за период [p_sdt; p_edt] с очисткой.
-- Повторный запуск с теми же параметрами не создаёт дублей (сначала удаляется период).
-- ЛР2: функция также пишет журнал качества (ods.dq_log, ods.dq_reject, ods.load_run_log)
-- и применяет правило версий: событие (user_id + listen_time + dt) из более поздней партии заменяет
-- версию из более ранней (моделирование изменения записей).
CREATE OR REPLACE FUNCTION ods.load_ods(p_sdt DATE, p_edt DATE)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    n      INTEGER;
    n_stg  INTEGER;
    v_run  BIGINT;
    v_t0   TIMESTAMP := clock_timestamp();
BEGIN
    IF p_sdt IS NULL OR p_edt IS NULL OR p_sdt > p_edt THEN
        RAISE EXCEPTION 'Некорректный период: % - %', p_sdt, p_edt;
    END IF;

    INSERT INTO ods.load_run_log (period_start, period_end) VALUES (p_sdt, p_edt) RETURNING run_id INTO v_run;

    -- 1. Удаляем ранее загруженные данные и журнал за период
    DELETE FROM ods.music_tracks WHERE dt BETWEEN p_sdt AND p_edt;
    DELETE FROM ods.dq_log       WHERE dt BETWEEN p_sdt AND p_edt;
    DELETE FROM ods.dq_reject    WHERE dt BETWEEN p_sdt AND p_edt;

    -- 2. Выбираем данные STAGING за период
    DROP TABLE IF EXISTS tmp_stg;
    CREATE TEMP TABLE tmp_stg ON COMMIT DROP AS
    SELECT * FROM stg.music_tracks WHERE dt BETWEEN p_sdt AND p_edt;
    SELECT count(*) INTO n_stg FROM tmp_stg;

    -- 3. Нормализация и признаки проблем (n_* — очищенные значения, f_* — признаки)
    --   * служебный индекс (src_index) в ODS не переносится
    --   * BTRIM для текстовых полей; userID дополняется нулями слева до 8 символов
    --   * пропуски в track/artist/genre -> 'unknown'
    --   * записи без userID/city, с неверным day или time помечаются как брак
    DROP TABLE IF EXISTS tmp_norm;
    CREATE TEMP TABLE tmp_norm ON COMMIT DROP AS
    SELECT s.*,
           CASE WHEN length(x.u) < 8 THEN lpad(x.u, 8, '0') ELSE x.u END       AS n_user,
           COALESCE(x.t, 'unknown')                                            AS n_track,
           COALESCE(x.a, 'unknown')                                            AS n_artist,
           COALESCE(x.g, 'unknown')                                            AS n_genre,
           x.c                                                                 AS n_city,
           x.d                                                                 AS n_day,
           CASE WHEN x.tm ~ '^([01]?[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$'
                THEN x.tm::TIME END                                            AS n_time,
           (x.u IS NULL)                                                       AS f_bad_user,
           (x.c IS NULL)                                                       AS f_bad_city,
           NOT COALESCE(x.d IN ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'), FALSE) AS f_bad_day,
           NOT COALESCE(x.tm ~ '^([01]?[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$', FALSE) AS f_bad_time,
           (x.u IS NOT NULL AND length(x.u) < 8)                               AS f_padded,
           (s.track  IS NOT NULL AND s.track  <> BTRIM(s.track))               AS f_trim_track,
           (s.artist IS NOT NULL AND s.artist <> BTRIM(s.artist))              AS f_trim_artist,
           (x.t IS NULL)                                                       AS f_unk_track,
           (x.a IS NULL)                                                       AS f_unk_artist,
           (x.g IS NULL)                                                       AS f_unk_genre
    FROM tmp_stg s
    CROSS JOIN LATERAL (
        SELECT NULLIF(BTRIM(s.userid), '') AS u, NULLIF(BTRIM(s.track), '')  AS t,
               NULLIF(BTRIM(s.artist), '') AS a, NULLIF(BTRIM(s.genre), '')  AS g,
               NULLIF(BTRIM(s.city), '')   AS c, BTRIM(s."day") AS d, BTRIM(s."time") AS tm
    ) x;

    -- 4. Корректные записи; DISTINCT удаляет полные дубликаты (после нормализации)
    DROP TABLE IF EXISTS tmp_valid;
    CREATE TEMP TABLE tmp_valid ON COMMIT DROP AS
    SELECT DISTINCT n_user AS user_id, n_track AS track, n_artist AS artist, n_genre AS genre,
           n_city AS city, n_time AS listen_time, n_day AS day_of_week, dt, batch_id
    FROM tmp_norm
    WHERE NOT (f_bad_user OR f_bad_city OR f_bad_day OR f_bad_time);

    -- 5. Правило версий: остаётся версия события из самой поздней партии
    DROP TABLE IF EXISTS tmp_final;
    CREATE TEMP TABLE tmp_final ON COMMIT DROP AS
    SELECT user_id, track, artist, genre, city, listen_time, day_of_week, dt, batch_id
    FROM (SELECT v.*, max(v.batch_id) OVER (PARTITION BY v.user_id, v.listen_time, v.dt) AS max_batch
          FROM tmp_valid v) q
    WHERE batch_id = max_batch;

    -- 6. Загрузка в ODS
    INSERT INTO ods.music_tracks (user_id, track, artist, genre, city, listen_time, day_of_week, dt, batch_id)
    SELECT user_id, track, artist, genre, city, listen_time, day_of_week, dt, batch_id FROM tmp_final;
    GET DIAGNOSTICS n = ROW_COUNT;

    -- 7. Журнал качества по (дата, партия) и отклонённые записи
    INSERT INTO ods.dq_log (dt, batch_id, rows_stg, rej_userid, rej_city, rej_day, rej_time, rows_valid,
                            fix_userid_pad, fix_trim_track, fix_trim_artist, unk_track, unk_artist, unk_genre,
                            dup_removed, superseded, rows_ods, run_id)
    SELECT a.dt, a.batch_id, a.rows_stg, a.rej_userid, a.rej_city, a.rej_day, a.rej_time, a.rows_valid,
           a.fix_userid_pad, a.fix_trim_track, a.fix_trim_artist, a.unk_track, a.unk_artist, a.unk_genre,
           a.rows_valid - COALESCE(d.cnt, 0),
           COALESCE(d.cnt, 0) - COALESCE(f.cnt, 0),
           COALESCE(f.cnt, 0),
           v_run
    FROM (SELECT dt, batch_id,
                 count(*)::INT AS rows_stg,
                 (count(*) FILTER (WHERE f_bad_user))::INT AS rej_userid,
                 (count(*) FILTER (WHERE NOT f_bad_user AND f_bad_city))::INT AS rej_city,
                 (count(*) FILTER (WHERE NOT f_bad_user AND NOT f_bad_city AND f_bad_day))::INT AS rej_day,
                 (count(*) FILTER (WHERE NOT f_bad_user AND NOT f_bad_city AND NOT f_bad_day AND f_bad_time))::INT AS rej_time,
                 (count(*) FILTER (WHERE NOT (f_bad_user OR f_bad_city OR f_bad_day OR f_bad_time)))::INT AS rows_valid,
                 (count(*) FILTER (WHERE f_padded))::INT       AS fix_userid_pad,
                 (count(*) FILTER (WHERE f_trim_track))::INT   AS fix_trim_track,
                 (count(*) FILTER (WHERE f_trim_artist))::INT  AS fix_trim_artist,
                 (count(*) FILTER (WHERE f_unk_track))::INT    AS unk_track,
                 (count(*) FILTER (WHERE f_unk_artist))::INT   AS unk_artist,
                 (count(*) FILTER (WHERE f_unk_genre))::INT    AS unk_genre
          FROM tmp_norm GROUP BY dt, batch_id) a
    LEFT JOIN (SELECT dt, batch_id, count(*)::INT AS cnt FROM tmp_valid GROUP BY dt, batch_id) d
           ON d.dt = a.dt AND d.batch_id = a.batch_id
    LEFT JOIN (SELECT dt, batch_id, count(*)::INT AS cnt FROM tmp_final GROUP BY dt, batch_id) f
           ON f.dt = a.dt AND f.batch_id = a.batch_id;

    INSERT INTO ods.dq_reject (dt, batch_id, src_index, reason, userid, track, artist, genre, city, "time", "day")
    SELECT dt, batch_id, src_index,
           CASE WHEN f_bad_user THEN 'bad_userid' WHEN f_bad_city THEN 'bad_city'
                WHEN f_bad_day  THEN 'bad_day'    ELSE 'bad_time' END,
           userid, track, artist, genre, city, "time", "day"
    FROM tmp_norm
    WHERE f_bad_user OR f_bad_city OR f_bad_day OR f_bad_time;

    UPDATE ods.load_run_log
       SET rows_stg = n_stg, rows_ods = n,
           duration_ms = (EXTRACT(EPOCH FROM clock_timestamp() - v_t0) * 1000)::INT
     WHERE run_id = v_run;
    RETURN n;
END;
$$;
