import json

import pandas as pd
import plotly.express as px
import streamlit as st

from app.common import get_profile, get_raw, kpi_row, plot, style_fig
from modules import analysis as an
from modules.config import DATA_PATH, DAY_ORDER, INDEX_COL, REPORTS_DIR, SQL_DIR

st.title("Исследовательский анализ данных")
if not DATA_PATH.exists():
    st.error(f"Файл не найден: {DATA_PATH}")
    st.stop()

raw, P = get_raw(), get_profile()
df = raw.drop(columns=[INDEX_COL], errors="ignore")
cp, t, dups = P["col_profile"], P["time"], P["dups"]
n = P["n_rows"]

# ---------- боковая панель ----------
sb = st.sidebar
sb.markdown("### Параметры анализа")
cities = sorted(df["City"].dropna().unique())
sel_city = sb.multiselect("Город", cities, default=cities)
days = [d for d in DAY_ORDER if d in set(df["Day"].dropna())] + sorted(set(df["Day"].dropna()) - set(DAY_ORDER))
sel_day = sb.multiselect("День недели", days, default=days)
top_n = sb.slider("Топ-N значений", 5, 50, 15)
rare_thr = sb.number_input("Порог редкого жанра (записей ≤)", 1, 1000, 30)
prof_cols = sb.multiselect("Колонки в таблице профиля", list(df.columns), default=list(df.columns))
sb.caption("Фильтры города и дня влияют только на раздел «Распределения». Остальные разделы считаются по всему источнику.")

f = df
if set(sel_city) != set(cities):
    f = f[f["City"].isin(sel_city)]
if set(sel_day) != set(days):
    f = f[f["Day"].isin(sel_day)]

# ---------- 1. Общие показатели ----------
st.header("1. Общие показатели источника")
kpi_row([
    ("Записей", f"{n:,}", f"{P['memory_mb']} МБ в памяти"),
    ("Атрибутов", P["n_cols"], "+ служебный индекс" if P["has_index_col"] else ""),
    ("Пропущено ячеек", f"{P['missing_cells']:,}", f"{P['missing_cells']/(n*P['n_cols'])*100:.2f}% от всех ячеек"),
    ("Записей без пропусков", f"{P['complete_rows']/n*100:.1f}%", f"{P['complete_rows']:,}"),
    ("Полных дубликатов", f"{dups['full']:,}", f"{dups['full']/n*100:.2f}%"),
    ("Потенц. дубликатов", f"{dups['potential']:,}", "userID+Track+artist+Day"),
])
with st.expander("Первые 100 строк исходных данных"):
    st.dataframe(raw.head(100), width="stretch")

# ---------- 2. Структура ----------
st.header("2. Структура, типы и заполненность")
st.dataframe(cp[cp["Колонка"].isin(prof_cols)], width="stretch", hide_index=True, column_config={
    "Пропусков, %": st.column_config.ProgressColumn("Пропусков, %", min_value=0, max_value=100, format="%.2f"),
})
metric = st.radio("Показатель на графике", ["Пропусков, %", "Уникальных, %"], horizontal=True)
plot(style_fig(px.bar(cp[cp["Колонка"].isin(prof_cols)], x="Колонка", y=metric, text=metric,
                                 title=metric + " по колонкам"), 320), width="stretch")

# ---------- 3. Распределения ----------
st.header("3. Распределения")
st.caption(f"Выборка: {len(f):,} из {n:,} записей")
tab_c, tab_n = st.tabs(["Категориальные признаки", "Числовой признак: час прослушивания"])
with tab_c:
    col = st.selectbox("Признак", ["genre", "City", "Day", "artist", "Track"])
    vc = f[col].fillna("<NULL>").value_counts().head(top_n).rename_axis(col).reset_index(name="Записей")
    plot(style_fig(px.bar(vc, x="Записей", y=col, orientation="h", title=f"Топ-{top_n}: {col}")
                              .update_yaxes(autorange="reversed"), 120 + 26 * len(vc)), width="stretch")
    st.dataframe(vc, width="stretch", hide_index=True)
with tab_n:
    h = pd.DataFrame({"hour": t["hours"].loc[f.index], "Day": f["Day"]}).dropna()
    plot(style_fig(px.histogram(h, x="hour", color="Day", nbins=24, barmode="group",
                                           category_orders={"Day": DAY_ORDER}, title="Прослушивания по часам"))
                    , width="stretch")
    st.dataframe(h["hour"].describe().round(2).rename("Значение").to_frame(), width="stretch")

# ---------- 4. Некорректные форматы и аномалии ----------
st.header("4. Некорректные форматы и аномальные значения")
a, b, c = st.tabs(["time", "userID", "Day / City / пробелы"])
with a:
    kpi_row([("Пропуски", f"{t['missing']:,}", ""), ("Не ЧЧ:ММ:СС", f"{t['bad_format']:,}", ""),
             ("Вне диапазона", f"{t['out_of_range']:,}", ""), ("Без ведущего нуля", f"{t['not_padded']:,}", "Ч:ММ:СС")])
with b:
    u = P["userid"]
    st.metric("userID не из 8 hex-символов", f"{u['bad']:,}")
    plot(style_fig(px.bar(u["lens"].rename_axis("Длина").reset_index(name="Записей"), x="Длина",
                                     y="Записей", title="Распределение длин userID"), 300), width="stretch")
    if u["examples"]:
        st.write("Примеры:", u["examples"])
with c:
    st.write(f"Недопустимых значений `Day`: **{P['bad_day']:,}**")
    ws = pd.Series(P["whitespace"], name="Записей с лишними пробелами").rename_axis("Колонка").reset_index()
    st.dataframe(ws, width="stretch", hide_index=True)

# ---------- 5. Дубликаты и противоречия ----------
st.header("5. Дубликаты и потенциально противоречивые значения")
ct = P["contra"]
kpi_row([("Полные дубликаты", f"{dups['full']:,}", "совпадают все поля"),
         ("Потенциальные", f"{dups['potential']:,}", "тот же пользователь/трек/день"),
         ("Треков с >1 жанром", f"{ct['track_multi_genre']:,}", f"из {ct['track_total']:,}"),
         ("Пользователей в >1 городе", f"{ct['user_multi_city']:,}", f"из {ct['user_total']:,}")])
if len(ct["tg_examples"]):
    with st.expander("Примеры треков с несколькими жанрами"):
        st.dataframe(ct["tg_examples"], width="stretch", hide_index=True)

# ---------- 6. Несогласованные категории ----------
st.header("6. Несогласованные категориальные значения")
cat = st.selectbox("Колонка", list(P["variants"]))
v = P["variants"][cat]
if len(v):
    st.dataframe(v, width="stretch", hide_index=True)
else:
    st.success(f"В колонке {cat} не найдено значений, отличающихся только регистром, пробелами или знаками")
if cat == "genre":
    rare = P["genre_raw"][P["genre_raw"] <= rare_thr].rename_axis("genre").reset_index(name="Записей")
    st.subheader(f"Редкие значения genre (≤ {rare_thr} записей) — кандидаты на ручную проверку")
    st.dataframe(rare, width="stretch", hide_index=True)

# ---------- 7-8. Проблемы и алгоритм ----------
iss = P["issues"]
st.header("7. Выявленные проблемы качества данных")
st.dataframe(iss.drop(columns=["Обоснование"]).assign(**{"Доля, %": iss["Доля, %"].map(lambda v: "—" if pd.isna(v) else f"{v:.2f}")}),
             width="stretch", hide_index=True)

st.header("8. Предлагаемый алгоритм очистки данных")
st.caption("Порядок: обрезка пробелов → дополнение userID → заполнение пропусков 'unknown' → исключение некорректных записей "
           "(userID, City, Day, time) → удаление полных дубликатов → приведение типов.")
for i, r in iss.iterrows():
    with st.expander(f"{i+1}. {r['Проблема']} — {r['Найдено']:,} {r['Единица']}"):
        st.markdown(f"**Признак(и):** {r['Признаки']}  \n**Условие:** {r['Критерий выявления']}  \n"
                    f"**Способ обработки:** {r['Способ обработки']}  \n**Обоснование:** {r['Обоснование']}")
with st.expander("Реализация: SQL-процедура ods.load_ods(p_sdt, p_edt)"):
    st.code((SQL_DIR / "03_load_ods.sql").read_text(encoding="utf-8"), language="sql")

# ---------- 9. Выводы ----------
st.header("9. Итоговые выводы")
for line in an.build_conclusions(P):
    st.markdown("- " + line)

# ---------- 10. Экспорт ----------
st.header("10. Сводка для сохранения")
txt = json.dumps(an.build_summary(P), ensure_ascii=False, indent=1, default=str)
c1, c2, _ = st.columns([1.3, 1, 4])
if c1.button("💾 Сохранить в reports/"):
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / "eda_summary.json").write_text(txt, encoding="utf-8")
    st.success(f"Сохранено: {REPORTS_DIR / 'eda_summary.json'}")
c2.download_button("⬇ Скачать JSON", txt, "eda_summary.json", "application/json")
with st.expander("Показать сводку (можно скопировать)"):
    st.code(txt, language="json")
