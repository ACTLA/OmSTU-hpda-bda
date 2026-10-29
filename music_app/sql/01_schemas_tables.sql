-- Слои хранилища: src (источник) -> stg (STAGING) -> ods (ODS) -> dwh (снежинка)
CREATE SCHEMA IF NOT EXISTS src;
CREATE SCHEMA IF NOT EXISTS stg;
CREATE SCHEMA IF NOT EXISTS ods;
CREATE SCHEMA IF NOT EXISTS dwh;

-- Источник: всё текстом, как в CSV (+ синтетическое поле dt)
CREATE TABLE IF NOT EXISTS src.music_tracks (
    src_index TEXT, userid TEXT, track TEXT, artist TEXT, genre TEXT,
    city TEXT, "time" TEXT, "day" TEXT, dt DATE
);

-- STAGING: копия источника as is
CREATE TABLE IF NOT EXISTS stg.music_tracks (
    src_index TEXT, userid TEXT, track TEXT, artist TEXT, genre TEXT,
    city TEXT, "time" TEXT, "day" TEXT, dt DATE
);

-- ODS: очищенные данные с нормальными типами
CREATE TABLE IF NOT EXISTS ods.music_tracks (
    ods_id      BIGSERIAL PRIMARY KEY,
    user_id     TEXT      NOT NULL,
    track       TEXT      NOT NULL,
    artist      TEXT      NOT NULL,
    genre       TEXT      NOT NULL,
    city        TEXT      NOT NULL,
    listen_time TIME      NOT NULL,
    day_of_week TEXT      NOT NULL,
    dt          DATE      NOT NULL,
    loaded_at   TIMESTAMP NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ods_music_tracks_dt ON ods.music_tracks (dt);
