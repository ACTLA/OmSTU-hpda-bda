"""Чистые функции (pandas, без БД и Streamlit) для страницы «Анализ и сопоставление данных»:
профилирование и сравнение наборов, таблица «до/после очистки», сверка с журналом, выводы."""
import numpy as np
import pandas as pd

from . import analysis as an
from .config import DAY_ORDER

CSV_COLS = ["userID", "Track", "artist", "genre", "City", "time", "Day"]
REASONS = {"bad_userid": "нет userID", "bad_city": "нет City", "bad_day": "недопустимый Day",
           "bad_time": "некорректный time"}


def _clean_text(s: pd.Series) -> pd.Series:
    s = s.astype("object").str.strip(" ")
    return s.where(s.notna() & (s != ""), np.nan)


def _pct(k, n) -> float:
    return round(k / n * 100, 2) if n else 0.0


# ---------------------------------------------------------------- профиль набора
def dataset_profile(df: pd.DataFrame, ref: pd.DataFrame | None = None) -> dict:
    """Характеристики набора (заполненность, уникальность, аномалии). ref — исходный набор для оценки новизны."""
    d = df[CSV_COLS]
    n = len(d)
    u0 = _clean_text(d["userID"])
    u = u0.where(u0.isna() | (u0.str.len() >= 8), u0.str.pad(8, side="left", fillchar="0"))
    tm = an.time_check(d)
    ws = ((d["Track"].notna() & (d["Track"].astype("object") != _clean_text(d["Track"]).fillna("")))
          | (d["artist"].notna() & (d["artist"].astype("object") != _clean_text(d["artist"]).fillna(""))))
    row = {
        "Записей": n,
        "Уникальных пользователей": int(u.nunique()),
        "Уникальных треков (Track)": int(_clean_text(d["Track"]).nunique()),
        "Уникальных исполнителей": int(_clean_text(d["artist"]).nunique()),
        "Жанров": int(_clean_text(d["genre"]).nunique()),
        "Городов": int(_clean_text(d["City"]).nunique()),
        "Дней недели": int(d["Day"].nunique()),
        "Прослушиваний на пользователя": round(n / max(u.nunique(), 1), 2),
        "Пропуски Track, %": _pct(int(_clean_text(d["Track"]).isna().sum()), n),
        "Пропуски artist, %": _pct(int(_clean_text(d["artist"]).isna().sum()), n),
        "Пропуски genre, %": _pct(int(_clean_text(d["genre"]).isna().sum()), n),
        "userID короче 8 символов, %": _pct(int((u0.notna() & (u0.str.len() < 8)).sum()), n),
        "Пробелы по краям Track/artist, %": _pct(int(ws.sum()), n),
        "Полные дубликаты, %": _pct(an.duplicates(d)["full"], n),
        "Некорректное время, %": _pct(tm["missing"] + tm["bad_format"] + tm["out_of_range"], n),
        "Недопустимый Day, %": _pct(int((~d["Day"].isin(DAY_ORDER)).sum()), n),
    }
    if "dt" in df.columns:
        row["Различных дат dt"] = int(df["dt"].nunique())
    if ref is not None:
        rr = pd.DataFrame({"t": _clean_text(ref["Track"]), "a": _clean_text(ref["artist"]).fillna("")}).dropna(subset=["t"])
        rt = set(zip(rr["t"], rr["a"]))
        ru = _clean_text(ref["userID"])
        ru = set(ru.dropna().str.pad(8, side="left", fillchar="0"))
        pairs = list(zip(_clean_text(d["Track"]), _clean_text(d["artist"]).fillna("")))
        known = np.array([(t in rt) for t in pairs], dtype=bool) | _clean_text(d["Track"]).isna().to_numpy()
        row["Треки вне исходного набора, %"] = _pct(int((~known).sum()), n)
        row["Пользователи вне исходного набора, %"] = _pct(int((~u.isin(ru) & u.notna()).sum()), n)
    return row


def profile_table(sets: dict, ref_name: str | None = None) -> pd.DataFrame:
    """sets: имя набора -> DataFrame. Результат: строки — показатели, столбцы — наборы."""
    ref = sets.get(ref_name) if ref_name else None
    cols = {name: dataset_profile(df, ref if name != ref_name else None) for name, df in sets.items()}
    return pd.DataFrame(cols)


# ---------------------------------------------------------------- распределения
def category_shares(a: pd.Series, b: pd.Series, top_n: int = 15):
    """Доли значений в двух наборах. Возвращает (таблица топ-N, TVD — расстояние полной вариации 0..1)."""
    pa = a.fillna("<NULL>").astype(str).str.strip(" ").value_counts(normalize=True)
    pb = b.fillna("<NULL>").astype(str).str.strip(" ").value_counts(normalize=True)
    idx = pa.index.union(pb.index)
    pa, pb = pa.reindex(idx, fill_value=0.0), pb.reindex(idx, fill_value=0.0)
    tvd = float(0.5 * (pa - pb).abs().sum())
    top = (pa + pb).sort_values(ascending=False).head(top_n).index
    out = pd.DataFrame({"Значение": top, "Исходный, %": (pa[top] * 100).round(2).to_numpy(),
                        "Синтетический, %": (pb[top] * 100).round(2).to_numpy()})
    out["Разница, п.п."] = (out["Синтетический, %"] - out["Исходный, %"]).round(2)
    return out, tvd


def hour_shares(a: pd.DataFrame, b: pd.DataFrame):
    """Распределение часа прослушивания (числовой признак) в двух наборах."""
    ha = an.time_check(a[CSV_COLS])["hours"].dropna().astype(int)
    hb = an.time_check(b[CSV_COLS])["hours"].dropna().astype(int)
    pa = ha.value_counts(normalize=True).reindex(range(24), fill_value=0.0)
    pb = hb.value_counts(normalize=True).reindex(range(24), fill_value=0.0)
    out = pd.DataFrame({"Час": range(24), "Исходный, %": (pa.to_numpy() * 100).round(2),
                        "Синтетический, %": (pb.to_numpy() * 100).round(2)})
    return out, float(0.5 * (pa - pb).abs().sum())


# ---------------------------------------------------------------- слои и журнал качества
def layers_pivot(lc: pd.DataFrame, batches_all: list) -> pd.DataFrame:
    """Записей по слоям и партиям: строки — партии, столбцы — SOURCE / STAGING / ODS."""
    p = lc.pivot_table(index="batch_id", columns="layer", values="rows", aggfunc="sum", fill_value=0)
    for c in ("SOURCE", "STAGING", "ODS"):
        if c not in p:
            p[c] = 0
    p = p[["SOURCE", "STAGING", "ODS"]].reindex(sorted(set(p.index) | set(batches_all)), fill_value=0).astype(int)
    p["STAGING − SOURCE"] = p["STAGING"] - p["SOURCE"]
    p["ODS − STAGING"] = p["ODS"] - p["STAGING"]
    p.index.name = "Партия"
    return p.reset_index()


def waterfall_steps(tot: pd.Series) -> pd.DataFrame:
    """Путь записей STAGING → ODS по журналу качества (суммы по выбранным партиям)."""
    steps = [("STAGING", int(tot["rows_stg"]), "total"),
             ("нет userID", -int(tot["rej_userid"]), "relative"), ("нет City", -int(tot["rej_city"]), "relative"),
             ("недопустимый Day", -int(tot["rej_day"]), "relative"), ("некорректный time", -int(tot["rej_time"]), "relative"),
             ("полные дубликаты", -int(tot["dup_removed"]), "relative"),
             ("заменены новой версией", -int(tot["superseded"]), "relative"), ("ODS", int(tot["rows_ods"]), "total")]
    return pd.DataFrame(steps, columns=["Этап", "Записей", "measure"])


def before_after(stg: pd.Series, ods: pd.Series, dq: pd.Series) -> pd.DataFrame:
    """Качество до и после очистки + сверка замера по STAGING с журналом ods.dq_log.
    stg — замер по STAGING (monitor.stg_quality), ods — замер по ODS (monitor.ods_quality), dq — суммы журнала."""
    n_stg, n_ods = int(stg["rows"]), int(ods["rows"])
    rows = [  # проблема, замер STAGING, журнал, остаток в ODS, обработка, точное ли сравнение
        ("Нет userID", stg["miss_userid"], dq["rej_userid"], 0, "запись исключена", True),
        ("Нет City", stg["rej_city"], dq["rej_city"], 0, "запись исключена", True),
        ("Недопустимый Day", stg["rej_day"], dq["rej_day"], 0, "запись исключена", True),
        ("Некорректный time", stg["rej_time"], dq["rej_time"], 0, "запись исключена", True),
        ("userID короче 8 символов", stg["short_userid"], dq["fix_userid_pad"], ods["bad_len_userid"],
         "дополнен нулями слева", True),
        ("Пробелы по краям Track", stg["ws_track"], dq["fix_trim_track"], ods["ws_any"], "обрезаны", True),
        ("Пробелы по краям artist", stg["ws_artist"], dq["fix_trim_artist"], ods["ws_any"], "обрезаны", True),
        ("Пропуск Track", stg["miss_track"], dq["unk_track"], ods["unk_track"], "заменён на 'unknown'", True),
        ("Пропуск artist", stg["miss_artist"], dq["unk_artist"], ods["unk_artist"], "заменён на 'unknown'", True),
        ("Пропуск genre", stg["miss_genre"], dq["unk_genre"], ods["unk_genre"], "заменён на 'unknown'", True),
        ("Полные дубликаты", stg["full_dups"], dq["dup_removed"], ods["full_dups"], "оставлена одна запись", False),
        ("Версии события из разных партий", np.nan, dq["superseded"], ods["multi_version_events"],
         "оставлена версия поздней партии", False),
    ]
    out = pd.DataFrame(rows, columns=["Проблема", "Найдено в STAGING (замер)", "Обработано (журнал)",
                                      "Остаётся в ODS", "Обработка", "_exact"])
    out["Доля STAGING, %"] = out["Найдено в STAGING (замер)"].map(lambda v: _pct(v, n_stg) if pd.notna(v) else np.nan)
    out["Доля ODS, %"] = out["Остаётся в ODS"].map(lambda v: _pct(v, n_ods))

    def status(r):
        if not r["_exact"]:
            return "— определения различаются"
        return "✓ совпадает" if int(r["Найдено в STAGING (замер)"]) == int(r["Обработано (журнал)"]) else "✗ расхождение"

    out["Сверка с журналом"] = out.apply(status, axis=1)
    return out.drop(columns="_exact")


def dwh_status(rel: pd.DataFrame) -> pd.DataFrame:
    """Сопоставление строк DWH с ODS с учётом уровня детализации таблиц."""
    out = rel.rename(columns={"tbl": "Таблица", "grain": "Гранулярность", "rows": "Строк в DWH",
                              "ods_equiv": "Эквивалент в ODS", "rule": "Ожидание"}).copy()
    out["Эквивалент в ODS"] = out["Эквивалент в ODS"].astype(int)
    out["Строк в DWH"] = out["Строк в DWH"].astype(int)
    out["Статус"] = np.where(
        out["Ожидание"] == "равно", np.where(out["Строк в DWH"] == out["Эквивалент в ODS"], "✓", "✗"),
        np.where(out["Строк в DWH"] >= out["Эквивалент в ODS"], "✓", "✗"))
    out["Пояснение"] = np.where(
        out["Ожидание"] == "равно", "один факт на запись ODS (число строк должно совпасть)",
        "строк столько же, сколько различных значений; справочники только пополняются, поэтому ≥ distinct в ODS")
    return out


# ---------------------------------------------------------------- выводы
def build_conclusions(ctx: dict) -> list:
    """Итоговые выводы формируются из фактических результатов (ничего не заготовлено заранее)."""
    out = []
    lay = ctx.get("layers", {})
    if lay:
        s, g, o = lay.get("SOURCE", 0), lay.get("STAGING", 0), lay.get("ODS", 0)
        mark = "✅" if s == g else "⚠️"
        out.append(f"{mark} SOURCE → STAGING: {s:,} → {g:,}. Перенос выполняется as is, числа "
                   f"{'совпадают' if s == g else 'различаются — STAGING не актуален, нужно выполнить stg.load_staging()'}.")
        out.append(f"ℹ️ STAGING → ODS: {g:,} → {o:,} ({_pct(g - o, g)}% записей не попали в ODS). Причины ниже по журналу качества.")
    tot = ctx.get("dq_tot")
    if tot is not None and int(tot["rows_stg"]) > 0:
        rej = int(tot[["rej_userid", "rej_city", "rej_day", "rej_time"]].sum())
        out.append(f"ℹ️ Журнал качества: отклонено {rej:,}, удалено полных дубликатов {int(tot['dup_removed']):,}, "
                   f"заменено новой версией {int(tot['superseded']):,}; исправлено усечённых userID {int(tot['fix_userid_pad']):,}, "
                   f"пробелов {int(tot['fix_trim_track'] + tot['fix_trim_artist']):,}, пропусков → 'unknown' "
                   f"{int(tot['unk_track'] + tot['unk_artist'] + tot['unk_genre']):,}.")
        balance = (int(tot["rows_stg"]) - rej - int(tot["dup_removed"]) - int(tot["superseded"]) == int(tot["rows_ods"]))
        out.append(("✅" if balance else "❌") + " Баланс журнала: STAGING − отклонено − дубликаты − заменённые = ODS "
                    + ("сходится." if balance else "не сходится."))
        if "ods_total" in ctx:
            ok = ctx["ods_total"] == int(tot["rows_ods"])
            out.append(("✅" if ok else "❌") + f" Журнал и фактический ODS: {int(tot['rows_ods']):,} и {ctx['ods_total']:,} — "
                        + ("совпадают." if ok else "расхождение, журнал не соответствует таблице ODS."))
    if ctx.get("mismatch"):
        out.append("❌ Замер по STAGING расходится с журналом по показателям: " + ", ".join(ctx["mismatch"]) + ".")
    elif ctx.get("ba") is not None:
        out.append("✅ Замеры по STAGING совпадают с журналом качества по всем показателям с однозначным определением.")
    cov = ctx.get("coverage")
    if cov and cov["stg"] != cov["ods"]:
        out.append(f"⚠️ В STAGING {cov['stg']} дат dt, в ODS — {cov['ods']}: часть периода не загружена в ODS.")
    rel = ctx.get("rel")
    if rel is not None:
        bad = rel[rel["Статус"] == "✗"]["Таблица"].tolist()
        out.append(("✅ Строки DWH согласованы с ODS с учётом гранулярности (факты = записи ODS, измерения = различные значения)."
                    if not bad else "❌ Не выполнены соотношения DWH ↔ ODS: " + ", ".join(bad) + "."))
    if ctx.get("facts_period") is not None:
        ok = ctx["facts_period"] == ctx["mart_listens"]
        out.append(("✅" if ok else "❌") + f" Витрина в PostgreSQL: Σ listens_cnt = {ctx['mart_listens']:,}, "
                    f"фактов DWH за период = {ctx['facts_period']:,}.")
    m = ctx.get("mart_cmp")
    if m is None:
        out.append("⚠️ Целевая СУБД недоступна — согласованность витрин не проверена: " + ctx.get("target_error", ""))
    else:
        bad = m[m["Результат"] != "Совпадает"]["Проверяемый показатель"].tolist()
        out.append(("✅" if not bad else "❌") + f" Перенос витрины PostgreSQL → {ctx['target_name']}: "
                    + (f"все {len(m)} показателей совпадают." if not bad else "расхождения: " + ", ".join(bad) + "."))
        rs = ctx.get("row_summary")
        if rs:
            out.append(("✅" if rs["ok"] else "❌") + f" Построчное сравнение: {rs['rows_pg']:,} строк в PostgreSQL, "
                        f"{rs['rows_target']:,} в {ctx['target_name']}, только в одной из СУБД — "
                        f"{rs['only_in_pg'] + rs['only_in_target']:,}, расхождений значений — {rs['rows_with_diff']:,}.")
    if ctx.get("synth"):
        s = ctx["synth"]
        out.append(f"ℹ️ Исходные и синтетические данные: пересечение распределений жанров — {s['genre_overlap']:.1%}, "
                   f"городов — {s['city_overlap']:.1%}, часа прослушивания — {s['hour_overlap']:.1%}; "
                   "доли пропусков и дубликатов в синтетике задаются параметрами генератора и проверяются журналом.")
    return out
