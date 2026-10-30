-- ЛР2. Аналитическая витрина «Прослушивания по дням, городам и жанрам».
-- Назначение: динамика интереса к жанрам в разрезе городов и дней недели, качество данных об исполнителях.
-- Гранулярность: одна строка = дата (dt) × город × жанр.
-- Показатели: listens_cnt, unique_users, unique_tracks, unique_artists (без 'unknown'), unknown_artist_cnt,
--             avg_listens_per_user (listens_cnt / unique_users), city_day_share (доля жанра в прослушиваниях города за день).
-- Источник: слой DWH (факты + измерения).
CREATE TABLE IF NOT EXISTS dwh.mart_listening_daily (
    dt                   DATE          NOT NULL,
    month_key            INT           NOT NULL,
    day_of_week          TEXT          NOT NULL,
    city_name            TEXT          NOT NULL,
    genre_name           TEXT          NOT NULL,
    listens_cnt          BIGINT        NOT NULL,
    unique_users         BIGINT        NOT NULL,
    unique_tracks        BIGINT        NOT NULL,
    unique_artists       BIGINT        NOT NULL,
    unknown_artist_cnt   BIGINT        NOT NULL,
    avg_listens_per_user NUMERIC(12,4) NOT NULL,
    city_day_share       NUMERIC(10,6) NOT NULL,
    loaded_at            TIMESTAMP     NOT NULL DEFAULT now(),
    PRIMARY KEY (dt, city_name, genre_name)
);

-- Загрузка витрины за период [p_sdt; p_edt]. Первоначальная загрузка — весь диапазон дат,
-- повторная — любой период: строки периода удаляются и считаются заново (дублей нет).
CREATE OR REPLACE FUNCTION dwh.load_mart(p_sdt DATE, p_edt DATE)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    n INTEGER;
BEGIN
    IF p_sdt IS NULL OR p_edt IS NULL OR p_sdt > p_edt THEN
        RAISE EXCEPTION 'Некорректный период: % - %', p_sdt, p_edt;
    END IF;

    DELETE FROM dwh.mart_listening_daily WHERE dt BETWEEN p_sdt AND p_edt;

    DROP TABLE IF EXISTS tmp_mart;
    CREATE TEMP TABLE tmp_mart ON COMMIT DROP AS
    SELECT d.dt, d.month_key, d.day_of_week, c.city_name, g.genre_name,
           count(*)                                                              AS listens_cnt,
           count(DISTINCT f.user_key)                                            AS unique_users,
           count(DISTINCT f.track_key)                                           AS unique_tracks,
           count(DISTINCT t.artist_key) FILTER (WHERE a.artist_name <> 'unknown') AS unique_artists,
           count(*) FILTER (WHERE a.artist_name = 'unknown')                     AS unknown_artist_cnt
    FROM dwh.fact_listening f
    JOIN dwh.dim_date   d ON d.date_key   = f.date_key
    JOIN dwh.dim_city   c ON c.city_key   = f.city_key
    JOIN dwh.dim_genre  g ON g.genre_key  = f.genre_key
    JOIN dwh.dim_track  t ON t.track_key  = f.track_key
    JOIN dwh.dim_artist a ON a.artist_key = t.artist_key
    WHERE d.dt BETWEEN p_sdt AND p_edt
    GROUP BY d.dt, d.month_key, d.day_of_week, c.city_name, g.genre_name;

    INSERT INTO dwh.mart_listening_daily
        (dt, month_key, day_of_week, city_name, genre_name, listens_cnt, unique_users, unique_tracks,
         unique_artists, unknown_artist_cnt, avg_listens_per_user, city_day_share)
    SELECT dt, month_key, day_of_week, city_name, genre_name, listens_cnt, unique_users, unique_tracks,
           unique_artists, unknown_artist_cnt,
           round(listens_cnt::NUMERIC / unique_users, 4),
           round(listens_cnt::NUMERIC / sum(listens_cnt) OVER (PARTITION BY dt, city_name), 6)
    FROM tmp_mart;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
