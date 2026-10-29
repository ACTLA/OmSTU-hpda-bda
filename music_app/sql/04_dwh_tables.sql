-- DWH: схема «снежинка». Факт: одно прослушивание. Измерения нормализованы:
--   dim_track -> dim_artist,  dim_date -> dim_month,  dim_time -> dim_daypart
CREATE TABLE IF NOT EXISTS dwh.dim_month (
    month_key  INT PRIMARY KEY,               -- ГГГГММ
    year_num   SMALLINT NOT NULL,
    month_num  SMALLINT NOT NULL,
    month_name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dwh.dim_daypart (
    daypart_key  SMALLINT PRIMARY KEY,
    daypart_name TEXT NOT NULL
);
INSERT INTO dwh.dim_daypart (daypart_key, daypart_name)
VALUES (1, 'ночь'), (2, 'утро'), (3, 'день'), (4, 'вечер')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS dwh.dim_date (
    date_key    INT PRIMARY KEY,              -- ГГГГММДД
    dt          DATE NOT NULL UNIQUE,
    month_key   INT NOT NULL REFERENCES dwh.dim_month (month_key),
    day_of_week TEXT NOT NULL,
    weekday_num SMALLINT NOT NULL             -- 1 = понедельник
);
CREATE TABLE IF NOT EXISTS dwh.dim_time (
    time_key    INT PRIMARY KEY,              -- секунд с начала суток
    listen_time TIME NOT NULL UNIQUE,
    hour_num    SMALLINT NOT NULL,
    daypart_key SMALLINT NOT NULL REFERENCES dwh.dim_daypart (daypart_key)
);
CREATE TABLE IF NOT EXISTS dwh.dim_artist (
    artist_key  SERIAL PRIMARY KEY,
    artist_name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS dwh.dim_track (
    track_key  SERIAL PRIMARY KEY,
    track_name TEXT NOT NULL,
    artist_key INT NOT NULL REFERENCES dwh.dim_artist (artist_key),
    UNIQUE (track_name, artist_key)
);
CREATE TABLE IF NOT EXISTS dwh.dim_genre (
    genre_key  SERIAL PRIMARY KEY,
    genre_name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS dwh.dim_city (
    city_key  SERIAL PRIMARY KEY,
    city_name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS dwh.dim_user (
    user_key SERIAL PRIMARY KEY,
    user_id  TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS dwh.fact_listening (
    listening_id BIGSERIAL PRIMARY KEY,
    date_key  INT NOT NULL REFERENCES dwh.dim_date (date_key),
    time_key  INT NOT NULL REFERENCES dwh.dim_time (time_key),
    user_key  INT NOT NULL REFERENCES dwh.dim_user (user_key),
    track_key INT NOT NULL REFERENCES dwh.dim_track (track_key),
    genre_key INT NOT NULL REFERENCES dwh.dim_genre (genre_key),
    city_key  INT NOT NULL REFERENCES dwh.dim_city (city_key)
);
CREATE INDEX IF NOT EXISTS ix_fact_listening_date ON dwh.fact_listening (date_key);
