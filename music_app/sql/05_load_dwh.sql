-- ODS -> DWH: загрузка за период [p_sdt; p_edt]. Измерения пополняются новыми значениями,
-- факты за период удаляются и загружаются заново (повторный запуск дублей не создаёт).
CREATE OR REPLACE FUNCTION dwh.load_dwh(p_sdt DATE, p_edt DATE)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    n INTEGER;
BEGIN
    IF p_sdt IS NULL OR p_edt IS NULL OR p_sdt > p_edt THEN
        RAISE EXCEPTION 'Некорректный период: % - %', p_sdt, p_edt;
    END IF;

    -- Измерения
    INSERT INTO dwh.dim_month (month_key, year_num, month_num, month_name)
    SELECT DISTINCT to_char(dt, 'YYYYMM')::INT, EXTRACT(YEAR FROM dt)::INT, EXTRACT(MONTH FROM dt)::INT,
           trim(to_char(dt, 'FMMonth'))
    FROM ods.music_tracks WHERE dt BETWEEN p_sdt AND p_edt
    ON CONFLICT DO NOTHING;

    INSERT INTO dwh.dim_date (date_key, dt, month_key, day_of_week, weekday_num)
    SELECT DISTINCT to_char(dt, 'YYYYMMDD')::INT, dt, to_char(dt, 'YYYYMM')::INT,
           trim(to_char(dt, 'FMDay')), EXTRACT(ISODOW FROM dt)::INT
    FROM ods.music_tracks WHERE dt BETWEEN p_sdt AND p_edt
    ON CONFLICT DO NOTHING;

    INSERT INTO dwh.dim_time (time_key, listen_time, hour_num, daypart_key)
    SELECT DISTINCT EXTRACT(EPOCH FROM listen_time)::INT, listen_time, EXTRACT(HOUR FROM listen_time)::INT,
           CASE WHEN EXTRACT(HOUR FROM listen_time) < 6  THEN 1
                WHEN EXTRACT(HOUR FROM listen_time) < 12 THEN 2
                WHEN EXTRACT(HOUR FROM listen_time) < 18 THEN 3
                ELSE 4 END
    FROM ods.music_tracks WHERE dt BETWEEN p_sdt AND p_edt
    ON CONFLICT DO NOTHING;

    INSERT INTO dwh.dim_artist (artist_name)
    SELECT DISTINCT artist FROM ods.music_tracks WHERE dt BETWEEN p_sdt AND p_edt
    ON CONFLICT DO NOTHING;

    INSERT INTO dwh.dim_track (track_name, artist_key)
    SELECT DISTINCT o.track, a.artist_key
    FROM ods.music_tracks o JOIN dwh.dim_artist a ON a.artist_name = o.artist
    WHERE o.dt BETWEEN p_sdt AND p_edt
    ON CONFLICT DO NOTHING;

    INSERT INTO dwh.dim_genre (genre_name)
    SELECT DISTINCT genre FROM ods.music_tracks WHERE dt BETWEEN p_sdt AND p_edt
    ON CONFLICT DO NOTHING;

    INSERT INTO dwh.dim_city (city_name)
    SELECT DISTINCT city FROM ods.music_tracks WHERE dt BETWEEN p_sdt AND p_edt
    ON CONFLICT DO NOTHING;

    INSERT INTO dwh.dim_user (user_id)
    SELECT DISTINCT user_id FROM ods.music_tracks WHERE dt BETWEEN p_sdt AND p_edt
    ON CONFLICT DO NOTHING;

    -- Факты: удаляем период и загружаем заново
    DELETE FROM dwh.fact_listening
    WHERE date_key BETWEEN to_char(p_sdt, 'YYYYMMDD')::INT AND to_char(p_edt, 'YYYYMMDD')::INT;

    INSERT INTO dwh.fact_listening (date_key, time_key, user_key, track_key, genre_key, city_key)
    SELECT to_char(o.dt, 'YYYYMMDD')::INT, EXTRACT(EPOCH FROM o.listen_time)::INT,
           u.user_key, t.track_key, g.genre_key, c.city_key
    FROM ods.music_tracks o
    JOIN dwh.dim_user   u ON u.user_id = o.user_id
    JOIN dwh.dim_artist a ON a.artist_name = o.artist
    JOIN dwh.dim_track  t ON t.track_name = o.track AND t.artist_key = a.artist_key
    JOIN dwh.dim_genre  g ON g.genre_name = o.genre
    JOIN dwh.dim_city   c ON c.city_name = o.city
    WHERE o.dt BETWEEN p_sdt AND p_edt;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
