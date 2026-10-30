"""Генератор синтетических данных на основе структуры и распределений music_tracks (АБД, ЛР2).

Что делает:
  * строит профиль исходного набора (пользователи, каталог треков, жанры, города, дни, время);
  * генерирует партию с заданным числом записей и периодом dt;
  * намеренно вносит проблемы качества (пропуски, пробелы, усечённые userID, дубликаты, брак);
  * моделирует изменение ранее загруженных записей (партия-исправление);
  * сохраняет партию в CSV и рядом — .meta.json с тем, что реально было сгенерировано.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from . import quality as q
from .config import DAY_ORDER, INDEX_COL, SYNTH_DIR, SYNTH_START

OUT_COLS = ["src_index", "userID", "Track", "artist", "genre", "City", "time", "Day", "dt", "batch_id"]
BAD_DAYS = ["Funday", "Mon", "Someday", "Fri."]
BAD_TIMES = ["24:00:01", "12:60:00", "7:3:9", "noon", "99:99:99"]
ADJ = ["Blue", "Silent", "Golden", "Midnight", "Electric", "Lazy", "Wild", "Distant", "Neon", "Paper", "Broken", "Soft"]
NOUN = ["River", "Echo", "Dream", "Road", "City", "Fire", "Rain", "Signal", "Garden", "Machine", "Shadow", "Light"]
FIRST = ["Alex", "Mira", "Oleg", "Nina", "Ivan", "Lera", "Max", "Vera", "Ilya", "Dana"]
LAST = ["Stone", "Fox", "Volkov", "Lane", "Frost", "Hart", "Wolf", "Rey", "Cole", "Sky"]


# ---------------------------------------------------------------- профиль источника
@dataclass
class SourceProfile:
    users: np.ndarray
    user_w: np.ndarray
    user_city: dict
    cat_track: np.ndarray
    cat_artist: np.ndarray
    cat_genre: np.ndarray
    cat_w: np.ndarray
    genres: np.ndarray
    genre_w: np.ndarray
    cities: list
    city_w: np.ndarray
    days: list
    day_w: np.ndarray
    secs: np.ndarray


def _weights(counts) -> np.ndarray:
    w = np.asarray(counts, dtype=float)
    return w / w.sum()


def build_profile(raw: pd.DataFrame) -> SourceProfile:
    """Профиль по исходному CSV (все колонки — строки). Данные предварительно нормализуются."""
    df = raw.drop(columns=[INDEX_COL], errors="ignore").copy()
    for c in df.columns:
        df[c] = df[c].str.strip(" ")
    df = df.drop_duplicates()

    ud = df[df["userID"].notna() & (df["userID"] != "")].copy()
    ud["userID"] = ud["userID"].str.pad(8, side="left", fillchar="0")      # восстановили усечённые ID
    uc = ud["userID"].value_counts()
    home = (ud.dropna(subset=["City"]).groupby(["userID", "City"]).size().reset_index(name="n")
            .sort_values("n", ascending=False).drop_duplicates("userID"))

    cat = df.dropna(subset=["Track", "artist", "genre"])
    cat = cat[(cat["Track"] != "") & (cat["artist"] != "") & (cat["genre"] != "")]
    cat = (cat.groupby(["Track", "artist", "genre"]).size().reset_index(name="n")
           .sort_values("n", ascending=False).drop_duplicates(["Track", "artist"]))

    gc = cat.groupby("genre")["n"].sum().sort_values(ascending=False)
    cc = df["City"].dropna().value_counts()
    dc = df["Day"].dropna().value_counts()
    dc = dc[dc.index.isin(DAY_ORDER)]
    parts = df["time"].dropna().str.extract(r"^(\d{1,2}):(\d{2}):(\d{2})$").dropna().astype(int)
    secs = (parts[0] * 3600 + parts[1] * 60 + parts[2]).to_numpy()

    return SourceProfile(
        users=uc.index.to_numpy(dtype=object), user_w=_weights(uc.to_numpy()),
        user_city=dict(zip(home["userID"], home["City"])),
        cat_track=cat["Track"].to_numpy(dtype=object), cat_artist=cat["artist"].to_numpy(dtype=object),
        cat_genre=cat["genre"].to_numpy(dtype=object), cat_w=_weights(cat["n"].to_numpy()),
        genres=gc.index.to_numpy(dtype=object), genre_w=_weights(gc.to_numpy()),
        cities=list(cc.index), city_w=_weights(cc.to_numpy()),
        days=list(dc.index), day_w=_weights(dc.to_numpy()), secs=secs)


# ---------------------------------------------------------------- параметры
@dataclass
class GenConfig:
    n_rows: int = 15000                 # итоговое число записей в партии (вместе с дубликатами)
    dt_start: date = SYNTH_START        # период dt партии (включительно)
    dt_end: date = SYNTH_START + timedelta(days=27)
    batch_id: int = 1
    seed: int = 42
    new_user_share: float = 0.35        # доля прослушиваний новыми пользователями
    new_track_share: float = 0.15       # доля прослушиваний новых (не из исходного каталога) треков
    city_switch_rate: float = 0.001     # пользователь слушает из другого города
    time_jitter_sec: int = 30           # сдвиг времени относительно эмпирического (в пределах часа)
    # проблемы качества: доли от числа уникальных строк (дубликаты добавляются после, до итогового n_rows)
    miss_track: float = 0.019
    miss_artist: float = 0.11
    miss_genre: float = 0.018
    whitespace: float = 0.0006          # пробелы по краям в Track и artist (каждый столбец)
    truncate_userid: bool = True        # потеря ведущих нулей userID, как в источнике (~6,25 %)
    full_duplicates: float = 0.059
    invalid_userid: float = 0.0         # браковочные записи: должны быть исключены в ODS
    invalid_city: float = 0.0
    invalid_day: float = 0.0
    invalid_time: float = 0.0


PRESETS = {
    "clean": dict(miss_track=0, miss_artist=0, miss_genre=0, whitespace=0, truncate_userid=False, full_duplicates=0),
    "source": {},   # доли как в исходном наборе
    "dirty": dict(miss_track=0.05, miss_artist=0.20, miss_genre=0.05, whitespace=0.02, full_duplicates=0.10,
                  invalid_userid=0.01, invalid_city=0.01, invalid_day=0.01, invalid_time=0.01),
}


def make_config(preset: str = "source", **overrides) -> GenConfig:
    if preset not in PRESETS:
        raise ValueError(f"Неизвестный профиль '{preset}'. Доступно: {', '.join(PRESETS)}")
    names = {f.name for f in fields(GenConfig)}
    bad = set(overrides) - names
    if bad:
        raise ValueError(f"Неизвестные параметры: {', '.join(sorted(bad))}")
    return replace(GenConfig(), **{**PRESETS[preset], **overrides})


# ---------------------------------------------------------------- генерация
def _hhmmss(sec: np.ndarray) -> np.ndarray:
    sec = np.mod(sec, 86400)
    h, m, s = sec // 3600, (sec % 3600) // 60, sec % 60
    return np.array([f"{a:02d}:{b:02d}:{c:02d}" for a, b, c in zip(h, m, s)], dtype=object)


def _base_rows(cfg: GenConfig, prof: SourceProfile, rng: np.random.Generator, n: int):
    span = [cfg.dt_start + timedelta(days=i) for i in range((cfg.dt_end - cfg.dt_start).days + 1)]
    by_day = {d: [x for x in span if DAY_ORDER[x.weekday()] == d] for d in prof.days}
    names = [d for d in prof.days if by_day[d]]
    if not names:
        raise ValueError("В заданном периоде нет дней недели, встречающихся в исходных данных (Day)")
    w = _weights([prof.day_w[prof.days.index(d)] for d in names])
    day = rng.choice(np.array(names, dtype=object), size=n, p=w)
    dt = np.empty(n, dtype=object)
    for d in names:
        m = day == d
        idx = rng.integers(0, len(by_day[d]), size=int(m.sum()))
        dt[m] = [by_day[d][i].isoformat() for i in idx]

    # пользователи и их города
    is_new = rng.random(n) < cfg.new_user_share
    n_new = int(is_new.sum())
    user = np.empty(n, dtype=object)
    user[~is_new] = prof.users[rng.choice(len(prof.users), size=n - n_new, p=prof.user_w)]
    pool = np.array([f"{v:08X}" for v in rng.integers(0, 16 ** 8, size=max(1, int(n_new * 0.8)))], dtype=object)
    pool_city = rng.choice(np.array(prof.cities, dtype=object), size=len(pool), p=prof.city_w)
    pidx = rng.integers(0, len(pool), size=n_new)
    user[is_new] = pool[pidx]
    city = np.empty(n, dtype=object)
    city[is_new] = pool_city[pidx]
    fallback = rng.choice(np.array(prof.cities, dtype=object), size=n, p=prof.city_w)
    city[~is_new] = [prof.user_city.get(u, fallback[i]) for i, u in zip(np.flatnonzero(~is_new), user[~is_new])]
    for i in np.flatnonzero(rng.random(n) < cfg.city_switch_rate):       # редкая смена города
        others = [c for c in prof.cities if c != city[i]]
        if others:
            city[i] = others[int(rng.integers(0, len(others)))]

    # треки: из исходного каталога или новые
    new_t = rng.random(n) < cfg.new_track_share
    n_nt = int(new_t.sum())
    track, artist, genre = (np.empty(n, dtype=object) for _ in range(3))
    ci = rng.choice(len(prof.cat_track), size=n - n_nt, p=prof.cat_w)
    track[~new_t], artist[~new_t], genre[~new_t] = prof.cat_track[ci], prof.cat_artist[ci], prof.cat_genre[ci]
    if n_nt:
        artists = [f"{FIRST[rng.integers(len(FIRST))]} {LAST[rng.integers(len(LAST))]}" for _ in range(max(1, n_nt // 3))]
        track[new_t] = [f"{ADJ[rng.integers(len(ADJ))]} {NOUN[rng.integers(len(NOUN))]} {rng.integers(1, 1000)}"
                        for _ in range(n_nt)]
        artist[new_t] = [artists[i] for i in rng.integers(0, len(artists), size=n_nt)]
        genre[new_t] = rng.choice(prof.genres, size=n_nt, p=prof.genre_w)

    base = rng.choice(prof.secs, size=n)
    shifted = base + rng.integers(-cfg.time_jitter_sec, cfg.time_jitter_sec + 1, size=n)
    sec = np.where(shifted // 3600 == base // 3600, shifted, base)         # час не меняется
    df = pd.DataFrame({"userID": user, "Track": track, "artist": artist, "genre": genre, "City": city,
                       "time": _hhmmss(sec), "Day": day, "dt": dt})
    return df, {"new_users_rows": n_new, "new_track_rows": n_nt}


def _inject(df: pd.DataFrame, cfg: GenConfig, rng: np.random.Generator):
    """Вносит проблемы качества. Возвращает число реально изменённых записей по каждой проблеме."""
    n, out = len(df), {}

    def pick(p):
        return rng.random(n) < p

    for col, p, key in (("Track", cfg.miss_track, "miss_track"), ("artist", cfg.miss_artist, "miss_artist"),
                        ("genre", cfg.miss_genre, "miss_genre")):
        m = pick(p) & df[col].notna().to_numpy()
        df.loc[m, col] = np.nan
        out[key] = int(m.sum())
    for col in ("Track", "artist"):
        m = pick(cfg.whitespace) & df[col].notna().to_numpy()
        kind = rng.integers(0, 3, size=n)
        df.loc[m, col] = [(" " + v) if k == 0 else (v + " ") if k == 1 else (" " + v + " ")
                          for v, k in zip(df.loc[m, col], kind[m])]
        out["ws_" + col.lower()] = int(m.sum())
    if cfg.truncate_userid:
        s = df["userID"].astype(str)
        st = s.str.lstrip("0")
        m = ((st != s) & (st != "")).to_numpy()
        df.loc[m, "userID"] = st[m]
        out["truncated_userid"] = int(m.sum())
    else:
        out["truncated_userid"] = 0
    for col, p, key, bad in (("userID", cfg.invalid_userid, "invalid_userid", None),
                             ("City", cfg.invalid_city, "invalid_city", None),
                             ("Day", cfg.invalid_day, "invalid_day", BAD_DAYS),
                             ("time", cfg.invalid_time, "invalid_time", BAD_TIMES)):
        m = pick(p)
        df.loc[m, col] = np.nan if bad is None else [bad[i] for i in rng.integers(0, len(bad), size=int(m.sum()))]
        out[key] = int(m.sum())
    return out


def generate_batch(cfg: GenConfig, prof: SourceProfile):
    """Одна партия. Возвращает (DataFrame в формате OUT_COLS, meta-словарь)."""
    if cfg.n_rows < 1:
        raise ValueError("n_rows должно быть больше 0")
    if cfg.dt_end < cfg.dt_start:
        raise ValueError("dt_end раньше dt_start")
    rng = np.random.default_rng([cfg.seed, cfg.batch_id])
    n_dup = int(round(cfg.n_rows * cfg.full_duplicates))
    n = cfg.n_rows - n_dup
    df, extra = _base_rows(cfg, prof, rng, n)
    injected = _inject(df, cfg, rng)
    if n_dup:
        df = pd.concat([df, df.iloc[rng.choice(n, size=n_dup, replace=n_dup > n)]], ignore_index=True)
    df = df.sample(frac=1, random_state=int(rng.integers(0, 2 ** 31 - 1))).reset_index(drop=True)
    injected["full_duplicates"] = n_dup
    df.insert(0, "src_index", [f"S{cfg.batch_id}-{i:07d}" for i in range(len(df))])
    df["batch_id"] = cfg.batch_id
    df = df[OUT_COLS]
    _, log = q.clean_reference(df)
    exp = log.drop(columns=q.KEY).sum().astype(int).to_dict()
    meta = {"kind": "synthetic", "batch_id": cfg.batch_id, "rows": len(df),
            "dt_min": str(df["dt"].min()), "dt_max": str(df["dt"].max()),
            "config": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(cfg).items()},
            "generated": {**extra, **injected,
                          "duplicates_actual": int(df.drop(columns=["src_index"]).duplicated().sum())},
            "expected_ods_standalone": exp}
    return df, meta


def make_correction(base: pd.DataFrame, prof: SourceProfile, batch_id: int, share: float = 0.05, seed: int = 42):
    """Партия-исправление: часть уже загруженных записей приходит повторно с изменёнными значениями.
    Ключ события (userID, time, dt) сохраняется; меняются жанр, трек+исполнитель или город.
    В ODS новая версия (большее batch_id) заменяет старую."""
    rng = np.random.default_rng([seed, batch_id, 7])
    clean, _ = q.clean_reference(base)
    clean = clean.drop_duplicates(subset=q.EVENT_KEY, keep=False)             # события с однозначным ключом
    k = max(1, int(len(clean) * share))
    pick = clean.iloc[rng.choice(len(clean), size=min(k, len(clean)), replace=False)].reset_index(drop=True)
    kind = rng.integers(0, 3, size=len(pick))                                 # 0 жанр, 1 трек+исполнитель, 2 город
    for i in np.flatnonzero(kind == 0):
        pick.at[i, "genre"] = rng.choice([g for g in prof.genres if g != pick.at[i, "genre"]])
    for i in np.flatnonzero(kind == 1):
        j = int(rng.integers(0, len(prof.cat_track)))
        pick.at[i, "track"], pick.at[i, "artist"], pick.at[i, "genre"] = (
            prof.cat_track[j], prof.cat_artist[j], prof.cat_genre[j])
    for i in np.flatnonzero(kind == 2):
        pick.at[i, "city"] = next((c for c in prof.cities if c != pick.at[i, "city"]), pick.at[i, "city"])
    out = pd.DataFrame({
        "src_index": [f"S{batch_id}-{i:07d}" for i in range(len(pick))], "userID": pick["user_id"],
        "Track": pick["track"], "artist": pick["artist"], "genre": pick["genre"], "City": pick["city"],
        "time": pick["listen_time"], "Day": pick["day_of_week"], "dt": pick["dt"], "batch_id": batch_id})[OUT_COLS]
    meta = {"kind": "correction", "batch_id": batch_id, "rows": len(out),
            "dt_min": str(out["dt"].min()), "dt_max": str(out["dt"].max()),
            "based_on_batch": int(base["batch_id"].iloc[0]),
            "generated": {"changed_genre": int((kind == 0).sum()), "changed_track_artist": int((kind == 1).sum()),
                          "changed_city": int((kind == 2).sum())}}
    return out, meta


# ---------------------------------------------------------------- сохранение и серия партий
def save_batch(df: pd.DataFrame, meta: dict, out_dir: Path = SYNTH_DIR) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"batch_{meta['batch_id']:02d}.csv"
    df.to_csv(path, index=False)
    path.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def read_batch(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])


def generate_series(prof: SourceProfile, n_batches: int = 2, rows: int = 15000, start: date = SYNTH_START,
                    days: int = 28, preset: str = "source", seed: int = 42, correction: bool = True,
                    first_batch_id: int = 1, out_dir: Path = SYNTH_DIR, **overrides) -> list[Path]:
    """n_batches последовательных партий по `days` дней каждая + (опционально) партия-исправление первой партии."""
    paths, first_df = [], None
    for i in range(n_batches):
        bid = first_batch_id + i
        s = start + timedelta(days=i * days)
        cfg = make_config(preset, n_rows=rows, dt_start=s, dt_end=s + timedelta(days=days - 1),
                          batch_id=bid, seed=seed, **overrides)
        df, meta = generate_batch(cfg, prof)
        paths.append(save_batch(df, meta, out_dir))
        first_df = df if first_df is None else first_df
    if correction and first_df is not None:
        df, meta = make_correction(first_df, prof, first_batch_id + n_batches, seed=seed)
        paths.append(save_batch(df, meta, out_dir))
    return paths
