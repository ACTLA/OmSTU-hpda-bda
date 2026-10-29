-- STAGING -> ODS: инкрементальная загрузка за период [p_sdt; p_edt] с очисткой.
-- Повторный запуск с теми же параметрами не создаёт дублей (сначала удаляется период).
CREATE OR REPLACE FUNCTION ods.load_ods(p_sdt DATE, p_edt DATE)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    n INTEGER;
BEGIN
    IF p_sdt IS NULL OR p_edt IS NULL OR p_sdt > p_edt THEN
        RAISE EXCEPTION 'Некорректный период: % - %', p_sdt, p_edt;
    END IF;

    -- 1. Удаляем ранее загруженные данные за период
    DELETE FROM ods.music_tracks WHERE dt BETWEEN p_sdt AND p_edt;

    -- 2. Выбираем данные STAGING за период
    DROP TABLE IF EXISTS tmp_stg;
    CREATE TEMP TABLE tmp_stg ON COMMIT DROP AS
    SELECT * FROM stg.music_tracks WHERE dt BETWEEN p_sdt AND p_edt;

    -- 3-4. Очистка и преобразование типов
    --   * служебный индекс (src_index) не переносится
    --   * BTRIM для текстовых полей; userID дополняется нулями слева до 8 символов
    --   * пропуски в track/artist/genre -> 'unknown'
    --   * записи без userID/city, с неверным day или time исключаются
    --   * DISTINCT удаляет полные дубликаты (после нормализации)
    DROP TABLE IF EXISTS tmp_clean;
    CREATE TEMP TABLE tmp_clean ON COMMIT DROP AS
    SELECT DISTINCT
        CASE WHEN length(BTRIM(userid)) < 8 THEN lpad(BTRIM(userid), 8, '0')
             ELSE BTRIM(userid) END                        AS user_id,
        COALESCE(NULLIF(BTRIM(track),  ''), 'unknown')     AS track,
        COALESCE(NULLIF(BTRIM(artist), ''), 'unknown')     AS artist,
        COALESCE(NULLIF(BTRIM(genre),  ''), 'unknown')     AS genre,
        BTRIM(city)                                        AS city,
        BTRIM("time")::TIME                                AS listen_time,
        BTRIM("day")                                       AS day_of_week,
        dt
    FROM tmp_stg
    WHERE NULLIF(BTRIM(userid), '') IS NOT NULL
      AND NULLIF(BTRIM(city), '')   IS NOT NULL
      AND BTRIM("day") IN ('Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday')
      AND BTRIM("time") ~ '^([01]?[0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]$';

    -- 5. Загрузка в ODS
    INSERT INTO ods.music_tracks (user_id, track, artist, genre, city, listen_time, day_of_week, dt)
    SELECT user_id, track, artist, genre, city, listen_time, day_of_week, dt FROM tmp_clean;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
