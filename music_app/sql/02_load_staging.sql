-- Source -> STAGING: полная перезаливка as is, без параметров
CREATE OR REPLACE FUNCTION stg.load_staging()
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    n INTEGER;
BEGIN
    TRUNCATE TABLE stg.music_tracks;
    INSERT INTO stg.music_tracks (src_index, userid, track, artist, genre, city, "time", "day", dt)
    SELECT src_index, userid, track, artist, genre, city, "time", "day", dt
    FROM src.music_tracks;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$;
