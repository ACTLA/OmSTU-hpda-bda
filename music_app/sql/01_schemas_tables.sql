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

-- ===== ЛР2: партии загрузки и журнал качества данных =====
-- batch_id: номер партии (0 = исходный CSV, 1, 2, … = синтетические партии). Идемпотентно: можно запускать повторно.
ALTER TABLE src.music_tracks ADD COLUMN IF NOT EXISTS batch_id INT NOT NULL DEFAULT 0;
ALTER TABLE stg.music_tracks ADD COLUMN IF NOT EXISTS batch_id INT NOT NULL DEFAULT 0;
ALTER TABLE ods.music_tracks ADD COLUMN IF NOT EXISTS batch_id INT NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS ix_src_music_tracks_batch ON src.music_tracks (batch_id);
CREATE INDEX IF NOT EXISTS ix_stg_music_tracks_dt    ON stg.music_tracks (dt);

-- Журнал качества: по одной строке на (дата, партия). Перезаписывается за период вместе с загрузкой ODS,
-- поэтому всегда соответствует фактически сохранённому результату очистки.
CREATE TABLE IF NOT EXISTS ods.dq_log (
    dt              DATE      NOT NULL,
    batch_id        INT       NOT NULL,
    rows_stg        INT       NOT NULL,   -- записей в STAGING
    rej_userid      INT       NOT NULL,   -- исключено: нет userID
    rej_city        INT       NOT NULL,   -- исключено: нет City
    rej_day         INT       NOT NULL,   -- исключено: недопустимый Day
    rej_time        INT       NOT NULL,   -- исключено: неверный формат time
    rows_valid      INT       NOT NULL,   -- прошли проверки
    fix_userid_pad  INT       NOT NULL,   -- userID короче 8 символов (дополнен нулями)
    fix_trim_track  INT       NOT NULL,   -- пробелы по краям Track
    fix_trim_artist INT       NOT NULL,   -- пробелы по краям artist
    unk_track       INT       NOT NULL,   -- пропуск Track -> 'unknown'
    unk_artist      INT       NOT NULL,   -- пропуск artist -> 'unknown'
    unk_genre       INT       NOT NULL,   -- пропуск genre -> 'unknown'
    dup_removed     INT       NOT NULL,   -- удалено полных дубликатов (после очистки)
    superseded      INT       NOT NULL,   -- заменено новой версией события из более поздней партии
    rows_ods        INT       NOT NULL,   -- записей в ODS
    run_id          BIGINT,
    loaded_at       TIMESTAMP NOT NULL DEFAULT now(),
    PRIMARY KEY (dt, batch_id)
);

-- Отклонённые записи с причиной (для просмотра примеров)
CREATE TABLE IF NOT EXISTS ods.dq_reject (
    dt DATE, batch_id INT, src_index TEXT, reason TEXT,
    userid TEXT, track TEXT, artist TEXT, genre TEXT, city TEXT, "time" TEXT, "day" TEXT
);
CREATE INDEX IF NOT EXISTS ix_dq_reject_dt ON ods.dq_reject (dt);

-- История запусков ods.load_ods
CREATE TABLE IF NOT EXISTS ods.load_run_log (
    run_id      BIGSERIAL PRIMARY KEY,
    loaded_at   TIMESTAMP NOT NULL DEFAULT now(),
    period_start DATE, period_end DATE,
    rows_stg    INT, rows_ods INT, duration_ms INT
);
