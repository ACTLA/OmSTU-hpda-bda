"""Профилирование источника music_tracks. Чистые функции без Streamlit:
модуль переиспользуется приложением и (при необходимости) загрузчиками АБД."""
import re

import pandas as pd

from .config import DAY_ORDER, INDEX_COL

TIME_RE = r"^(\d{1,2}):(\d{2}):(\d{2})$"
USERID_RE = r"^[0-9A-Fa-f]{8}$"
SEMANTIC = {
    "userID": "идентификатор (hex, 8 симв.)", "Track": "название трека", "artist": "исполнитель",
    "genre": "жанр (категория)", "City": "город (категория)", "time": "время ЧЧ:ММ:СС",
    "Day": "день недели (категория)",
}
FILL_COLS = ["Track", "artist", "genre"]  # пропуск можно заменить 'unknown'
POT_KEYS = ["userID", "Track", "artist", "Day"]


def load_raw(path) -> pd.DataFrame:
    """Читает CSV как есть: все колонки строками, без автоприведения типов."""
    df = pd.read_csv(path, dtype=str)
    df.columns = df.columns.str.strip()
    return df


def split_index(raw: pd.DataFrame):
    has = INDEX_COL in raw.columns
    return (raw.drop(columns=[INDEX_COL]) if has else raw), has


def _blank(s: pd.Series) -> pd.Series:
    return s.isna() | s.str.strip().eq("")


def column_profile(df: pd.DataFrame) -> pd.DataFrame:
    n, rows = len(df), []
    for c in df.columns:
        s = df[c]
        na, bl = int(s.isna().sum()), int(s.str.strip().eq("").sum())
        miss, nun, vc = na + bl, int(s.nunique()), s.value_counts()
        rows.append({
            "Колонка": c, "Смысловой тип": SEMANTIC.get(c, "—"), "Тип pandas": str(s.dtype),
            "Заполнено": n - miss, "NULL": na, "Пустые строки": bl, "Пропущено": miss,
            "Пропусков, %": round(miss / n * 100, 2), "Уникальных": nun,
            "Уникальных, %": round(nun / n * 100, 2),
            "Самое частое": vc.index[0] if len(vc) else None, "Частота": int(vc.iloc[0]) if len(vc) else 0,
        })
    return pd.DataFrame(rows)


def norm_key(x: str) -> str:
    return re.sub(r"[\W_]+", "", x.lower())


def variants(s: pd.Series) -> pd.DataFrame:
    """Группы значений, различающихся только регистром/пробелами/знаками."""
    cols = ["Ключ", "Варианты", "Видов", "Записей", "Канонический (самый частый)"]
    vc = s.dropna().value_counts()
    if vc.empty:
        return pd.DataFrame(columns=cols)
    d = vc.rename_axis("raw").reset_index(name="n")
    d["key"] = d["raw"].map(norm_key)
    g = d.groupby("key").agg(Варианты=("raw", lambda x: " | ".join(map(repr, x))), Видов=("raw", "size"),
                             Записей=("n", "sum"), Канон=("raw", "first"))
    g = g[g["Видов"] > 1].sort_values("Записей", ascending=False).reset_index()
    return g.rename(columns={"key": "Ключ", "Канон": "Канонический (самый частый)"})[cols]


def time_check(df: pd.DataFrame) -> dict:
    t = df["time"]
    present = ~_blank(t)
    parts = t.str.strip().str.extract(TIME_RE).astype(float)
    fmt = parts[0].notna()
    rng = (parts[0] < 24) & (parts[1] < 60) & (parts[2] < 60)
    return {
        "missing": int((~present).sum()), "bad_format": int((present & ~fmt).sum()),
        "out_of_range": int((fmt & ~rng).sum()), "not_padded": int((t.str.len() == 7).sum()),
        "hours": parts[0].where(fmt & rng),
    }


def userid_check(df: pd.DataFrame) -> dict:
    u = df["userID"]
    present = ~_blank(u)
    ok = u.str.strip().str.match(USERID_RE).fillna(False).astype(bool)
    bad = present & ~ok
    return {"bad": int(bad.sum()), "lens": u[present].str.len().value_counts().sort_index(),
            "examples": u[bad].drop_duplicates().head(10).tolist()}


def duplicates(df: pd.DataFrame) -> dict:
    full = df.duplicated()
    sub = df.duplicated(subset=POT_KEYS)
    return {"full": int(full.sum()), "potential": int((sub & ~full).sum())}


def contradictions(df: pd.DataFrame) -> dict:
    a = df.dropna(subset=["Track", "artist", "genre"]).groupby(["Track", "artist"])["genre"].nunique()
    b = df.dropna(subset=["userID", "City"]).groupby("userID")["City"].nunique()
    tg = a[a > 1].sort_values(ascending=False)
    return {"track_multi_genre": int(len(tg)), "track_total": int(len(a)),
            "tg_examples": tg.head(15).reset_index(name="Жанров"),
            "user_multi_city": int((b > 1).sum()), "user_total": int(len(b))}


def run_profile(raw: pd.DataFrame) -> dict:
    df, has_idx = split_index(raw)
    n = len(df)
    cp = column_profile(df)
    d = df["Day"].dropna()
    P = {
        "n_rows": n, "n_cols": df.shape[1], "has_index_col": has_idx,
        "memory_mb": round(raw.memory_usage(deep=True).sum() / 1024**2, 2),
        "col_profile": cp, "missing_cells": int(cp["Пропущено"].sum()),
        "complete_rows": int((~df.apply(_blank).any(axis=1)).sum()),
        "dups": duplicates(df), "time": time_check(df), "userid": userid_check(df),
        "variants": {c: variants(df[c]) for c in ["genre", "City", "Day"]},
        "bad_day": int((~d.isin(DAY_ORDER)).sum()),
        "whitespace": {c: int((df[c].dropna() != df[c].dropna().str.strip()).sum()) for c in df.columns},
        "contra": contradictions(df),
        "genre_raw": df["genre"].fillna("<NULL>").value_counts(),
    }
    P["issues"] = detect_issues(P)
    return P


def detect_issues(P: dict) -> pd.DataFrame:
    n, rows = P["n_rows"], []

    def add(problem, cols, crit, cnt, unit, action, why):
        if cnt > 0:
            share = round(cnt / n * 100, 2) if unit == "записей" else None
            rows.append({"Проблема": problem, "Признаки": cols, "Критерий выявления": crit, "Найдено": int(cnt),
                         "Единица": unit, "Доля, %": share, "Способ обработки": action, "Обоснование": why})

    if P["has_index_col"]:
        add("Служебная колонка индекса", INDEX_COL, "Первая колонка CSV без имени в заголовке", 1, "колонка",
            "Не переносить в ODS", "Артефакт сохранения индекса, смысловой нагрузки не несёт")
    for _, r in P["col_profile"].iterrows():
        c = r["Колонка"]
        if c in FILL_COLS:
            add(f"Пропуск в {c}", c, "NULL, пустая строка или только пробелы", r["Пропущено"], "записей",
                "Заполнить значением 'unknown'",
                "Событие прослушивания сохраняется для остальных агрегатов; 'unknown' — отдельная категория")
        else:
            add(f"Пропуск в {c}", c, "NULL, пустая строка или только пробелы", r["Пропущено"], "записей",
                "Исключить запись", "Без этого поля событие нельзя корректно описать")
    for c, k in P["whitespace"].items():
        add(f"Лишние пробелы в {c}", c, "Значение отличается от значения после strip()", k, "записей",
            "Обрезать пробелы по краям", "Иначе одинаковые значения считаются разными")
    add("Полные дубликаты", "все поля", "Совпадают все поля, включая userID и time", P["dups"]["full"], "записей",
        "Оставить одну запись", "Повторная загрузка одного и того же события")
    add("Потенциальные дубликаты", "userID, Track, artist, Day",
        "Совпадают userID, Track, artist, Day, но запись не полный дубль", P["dups"]["potential"], "записей",
        "Оставить без изменений", "Повторное прослушивание трека тем же пользователем — отдельное событие")
    t = P["time"]
    add("Некорректный формат времени", "time", "Не соответствует ЧЧ:ММ:СС", t["bad_format"], "записей",
        "Исключить запись", "Время нельзя восстановить")
    add("Время вне диапазона", "time", "Час ≥ 24, минуты или секунды ≥ 60", t["out_of_range"], "записей",
        "Исключить запись", "Недопустимое значение времени")
    add("Время без ведущего нуля", "time", "Формат Ч:ММ:СС (длина 7)", t["not_padded"], "записей",
        "Привести к типу TIME", "Единый формат хранения")
    u = P["userid"]
    add("Усечённый userID", "userID", "Длина меньше 8 символов", u["bad"], "записей",
        "Дополнить нулями слева до 8 символов",
        f"Ожидаемая доля при потере ведущих нулей у 8-символьных hex-ID — около 6,25% (1/16); "
        f"фактическая — {u['bad'] / n * 100:.2f}%")
    add("Недопустимое значение Day", "Day", "Не из списка Monday…Sunday", P["bad_day"], "записей",
        "Исключить запись", "День недели должен быть из справочника")
    for c, v in P["variants"].items():
        add(f"Несогласованная категория {c}", c, "Несколько написаний одного значения (регистр, знаки, пробелы)",
            int(v["Записей"].sum()) if len(v) else 0, "записей",
            "Привести к каноническому (самому частому) написанию", "Корректная группировка в измерениях")
    ct = P["contra"]
    add("Трек с несколькими жанрами", "Track, artist, genre", "Для одной пары (Track, artist) больше одного genre",
        ct["track_multi_genre"], "треков", "Оставить без изменений",
        "Жанр хранится в таблице фактов, поэтому информация не теряется")
    add("Пользователь из нескольких городов", "userID, City", "Один userID встречается с больше чем одним City",
        ct["user_multi_city"], "пользователей", "Оставить без изменений",
        "Пользователь мог слушать из разных городов; город хранится в таблице фактов")
    return pd.DataFrame(rows)


def build_conclusions(P: dict) -> list:
    n, cp = P["n_rows"], P["col_profile"]
    miss = cp[cp["Пропущено"] > 0]
    out = [f"Источник: **{n:,}** записей, **{P['n_cols']}** атрибутов"
           + (" (+ служебная колонка индекса)." if P["has_index_col"] else ".")]
    out.append("Пропуски: " + (", ".join(f"`{r['Колонка']}` — {r['Пропущено']:,} ({r['Пропусков, %']}%)"
                                         for _, r in miss.iterrows()) if len(miss) else "не обнаружены.") )
    out.append(f"Записей без единого пропуска: **{P['complete_rows']:,}** ({P['complete_rows']/n*100:.2f}%).")
    out.append(f"Полных дубликатов: **{P['dups']['full']:,}**; потенциальных (тот же пользователь, трек, "
               f"исполнитель и день, но запись не полный дубль): **{P['dups']['potential']:,}**.")
    out.append("Гранулярность источника — **событие прослушивания** (пользователь + время + день). "
               "Поэтому дедупликация по паре Track+artist недопустима: она удаляет разные события.")
    t = P["time"]
    out.append(f"Время: некорректный формат — {t['bad_format']:,}, вне диапазона — {t['out_of_range']:,}, "
               f"без ведущего нуля — {t['not_padded']:,}. userID не по формату: {P['userid']['bad']:,}.")
    cat = {c: len(v) for c, v in P["variants"].items()}
    out.append("Групп несогласованных написаний категорий: " + ", ".join(f"`{c}` — {k}" for c, k in cat.items()) + ".")
    out.append("Набор пригоден для построения ODS/DWH после применения правил очистки.")
    return out


def build_summary(P: dict) -> dict:
    rec = lambda d, k=40: d.head(k).to_dict("records")
    return {
        "rows": P["n_rows"], "cols": P["n_cols"], "has_index_col": P["has_index_col"],
        "complete_rows": P["complete_rows"], "memory_mb": P["memory_mb"], "dups": P["dups"],
        "columns": rec(P["col_profile"]),
        "time": {k: v for k, v in P["time"].items() if k != "hours"},
        "hour_distribution": {int(k): int(v) for k, v in P["time"]["hours"].value_counts().sort_index().items()},
        "userid": {"bad": P["userid"]["bad"], "examples": P["userid"]["examples"],
                   "length_distribution": {int(k): int(v) for k, v in P["userid"]["lens"].items()}},
        "bad_day": P["bad_day"], "whitespace": P["whitespace"],
        "variants": {c: rec(v, 25) for c, v in P["variants"].items()},
        "genre_values_raw_top40": {str(k): int(v) for k, v in P["genre_raw"].head(40).items()},
        "genre_values_raw_rare_tail": {str(k): int(v) for k, v in P["genre_raw"].tail(25).items()},
        "contradictions": {k: v for k, v in P["contra"].items() if k != "tg_examples"},
        "contradiction_examples": rec(P["contra"]["tg_examples"], 10),
        "issues": rec(P["issues"], 60),
    }
