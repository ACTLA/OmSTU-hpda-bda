import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from app.common import kpi_row, plot, style_fig
from modules import compare_data as cd
from modules import db, monitor as mon, transfer as tr

st.title("Анализ и сопоставление данных")
st.caption("Все показатели считаются по фактическим данным слоёв SOURCE → STAGING → ODS → DWH, журналу качества "
           "ods.dq_log и витринам в двух СУБД. Загрузки выполняются командой `music_app/cli.py`, здесь — только анализ.")

# ---------- подключение ----------
try:
    token = db.state_token()
    info = mon.batches_info()
except Exception as e:
    st.error(f"Хранилище недоступно или не обновлено до ЛР2: {type(e).__name__}: {e}")
    st.info("Проверьте PostgreSQL и `.env`, затем выполните `python music_app/cli.py init` "
            "(или кнопку «Создать БД, схемы, таблицы и функции» на странице «Загрузка данных»).")
    st.stop()
if info.empty:
    st.warning("В SOURCE нет данных. Загрузите исходный CSV и партии: `python music_app/cli.py demo`.")
    st.stop()


@st.cache_data(show_spinner="Запрос к хранилищу…")
def cached(tok, name, args):
    return getattr(mon, name)(*args)


def q(name, *args):
    return cached(token, name, tuple(args))


# ---------- боковая панель ----------
sb = st.sidebar
sb.markdown("### Параметры анализа")
if sb.button("🔄 Обновить данные"):
    st.cache_data.clear()
    st.rerun()
labels = {int(r.batch_id): ("0 — исходный CSV" if r.batch_id == 0 else f"{int(r.batch_id)} — синтетика")
          for r in info.itertuples()}
sel_b = sb.multiselect("Партии загрузки", list(labels), default=list(labels), format_func=labels.get)
dmin, dmax = pd.to_datetime(info["dt_min"]).min().date(), pd.to_datetime(info["dt_max"]).max().date()
rng = sb.date_input("Период dt", (dmin, dmax), min_value=dmin, max_value=dmax)
top_n = sb.slider("Топ-N значений", 5, 30, 12)
freq = sb.selectbox("Детализация по dt", list(mon.FREQ), index=1)
if not sel_b:
    st.warning("Выберите хотя бы одну партию.")
    st.stop()
if not isinstance(rng, (tuple, list)) or len(rng) != 2:
    st.info("Выберите начало и конец периода dt.")
    st.stop()
d1, d2 = str(rng[0]), str(rng[1])
B = tuple(sorted(sel_b))
sb.caption("Фильтры партий и dt применяются ко всем разделам. Витрины хранятся без разреза по партиям, "
           "поэтому для них действует только период dt.")

# ---------- расчёты ----------
lc = q("layer_counts", B, d1, d2)
tot = lc.groupby("layer")["rows"].sum().to_dict() if len(lc) else {}
dq = q("dq_by_batch", B, d1, d2)
dq_tot = dq.drop(columns=["batch_id", "dates", "loaded_at"]).sum() if len(dq) else None
cov = q("dates_by_layer", B, d1, d2)
mart = q("mart_pg", d1, d2)
facts = q("facts_in_period", d1, d2)

@st.cache_data(show_spinner="Сверяю витрины в двух СУБД…")
def target_compare(tok, t_tok, a, b):
    t = tr.get_target()
    return (tr.compare_metrics(a, b, t),
            t.query(f"SELECT {', '.join(tr.MART_COLS)} FROM {t.table} WHERE {t.where(a, b)}"))


target, t_err, tgt_name, cmp_df, tg_df = None, None, "целевая СУБД", None, None
try:
    target = tr.get_target()
    tgt_name = target.name
    t_tok = "|".join(map(str, target.query(f"SELECT count(*), sum(listens_cnt) FROM {target.table}").iloc[0]))
    cmp_df, tg_df = target_compare(token, t_tok, d1, d2)
except Exception as e:
    t_err = f"{type(e).__name__}: {e}"

# ---------- 1. Общие показатели ----------
st.header("1. Общие показатели состояния хранилища")
kpi_row([("Партий", f"{len(B)} из {len(info)}", f"dt {d1} … {d2}"),
         ("SOURCE", f"{tot.get('SOURCE', 0):,}", "записей"), ("STAGING", f"{tot.get('STAGING', 0):,}", "записей"),
         ("ODS", f"{tot.get('ODS', 0):,}", "после очистки"), ("DWH: факты", f"{facts:,}", "по периоду dt"),
         ("Витрина PostgreSQL", f"{len(mart):,}", "строк: дата × город × жанр")])
kpi_row([("Витрина " + tgt_name, f"{len(tg_df):,}" if tg_df is not None else "—", "строк" if tg_df is not None else "недоступна"),
         ("Дат dt: STAGING / ODS", f"{cov['stg']} / {cov['ods']}", "покрытие периода"),
         ("Отклонено", f"{int(dq_tot[['rej_userid', 'rej_city', 'rej_day', 'rej_time']].sum()):,}" if dq_tot is not None else "—", "по журналу качества"),
         ("Удалено дубликатов", f"{int(dq_tot['dup_removed']):,}" if dq_tot is not None else "—", "по журналу качества"),
         ("Заменено версий", f"{int(dq_tot['superseded']):,}" if dq_tot is not None else "—", "новая партия заменила старую")])
st.dataframe(info.assign(партия=info["batch_id"].map(labels))[["партия", "src_rows", "stg_rows", "ods_rows", "dt_min", "dt_max"]]
             .rename(columns={"src_rows": "SOURCE", "stg_rows": "STAGING", "ods_rows": "ODS", "dt_min": "dt с", "dt_max": "dt по"}),
             width="stretch", hide_index=True)

# ---------- 2. Слои ----------
st.header("2. Количество записей по слоям SOURCE, STAGING и ODS")
lp = cd.layers_pivot(lc, list(B))
st.dataframe(lp, width="stretch", hide_index=True)
long = lp.melt(id_vars="Партия", value_vars=["SOURCE", "STAGING", "ODS"], var_name="Слой", value_name="Записей")
long["Партия"] = long["Партия"].map(lambda b: labels.get(int(b), str(b)))
plot(style_fig(px.bar(long, x="Партия", y="Записей", color="Слой", barmode="group", title="Записи по слоям и партиям"), 340))
if lp["STAGING − SOURCE"].abs().sum() == 0:
    st.success("SOURCE → STAGING: числа совпадают во всех партиях — перенос выполняется as is.")
else:
    st.warning("STAGING не совпадает с SOURCE: выполните stg.load_staging() (шаг «SOURCE → STAGING»).")

st.subheader("Почему записей в ODS меньше, чем в STAGING")
if dq_tot is None:
    st.warning("Журнал качества пуст за выбранные партии и период: ODS не загружен (ods.load_ods).")
else:
    wf = cd.waterfall_steps(dq_tot)
    fig = go.Figure(go.Waterfall(x=wf["Этап"], y=wf["Записей"], measure=wf["measure"], text=wf["Записей"].map("{:,}".format),
                                 connector={"line": {"color": "#888"}}))
    fig.update_layout(title="Путь записей STAGING → ODS (по журналу качества)", showlegend=False)
    plot(style_fig(fig, 380))
    st.caption("Исключение записей выполняется по первому сработавшему правилу: userID → City → Day → time. "
               "Дубликаты считаются после очистки значений (обрезка пробелов, дополнение userID), поэтому их может "
               "быть больше, чем «сырых» повторов. «Заменены новой версией» — события (userID + time + dt), "
               "повторно пришедшие в более поздней партии.")
    with st.expander("Журнал качества по партиям (ods.dq_log)"):
        st.dataframe(dq, width="stretch", hide_index=True)
    with st.expander("История запусков ods.load_ods (повторная загрузка не должна менять rows_ods)"):
        st.dataframe(q("run_log", 30), width="stretch", hide_index=True)

st.subheader("Таблицы DWH и уровень детализации")
rel = cd.dwh_status(q("dwh_relations"))
st.dataframe(rel, width="stretch", hide_index=True)
st.caption("Строки измерений не обязаны совпадать с SOURCE/ODS: это числа различных значений. Совпадать должны только факты "
           "(одна строка факта = одна запись ODS). Таблица показана по всему хранилищу, без фильтров.")

# ---------- 3. Качество до и после ----------
st.header("3. Качество данных до и после очистки")
ba, mismatch = None, []
if dq_tot is not None:
    stg_q, ods_q = q("stg_quality", B, d1, d2).iloc[0].astype(float), q("ods_quality", B, d1, d2).iloc[0].astype(float)
    ba = cd.before_after(stg_q, ods_q, dq_tot.astype(float))
    st.dataframe(ba.drop(columns=["Доля STAGING, %", "Доля ODS, %"]), width="stretch", hide_index=True)
    mismatch = ba[ba["Сверка с журналом"] == "✗ расхождение"]["Проблема"].tolist()
    if mismatch:
        st.error("Замер по STAGING не совпал с журналом: " + ", ".join(mismatch) +
                 ". Вероятно, STAGING или SOURCE менялись после последней загрузки ODS — перезапустите ods.load_ods.")
    else:
        st.success("Замеры по STAGING совпадают с журналом ods.dq_log: количество исправленных и отклонённых записей "
                   "берётся из фактически сохранённого результата очистки.")
    pl = ba[ba["Доля STAGING, %"].notna()].melt(id_vars="Проблема", value_vars=["Доля STAGING, %", "Доля ODS, %"],
                                                var_name="Этап", value_name="Доля записей, %")
    pl["Этап"] = pl["Этап"].map({"Доля STAGING, %": "до очистки (STAGING)", "Доля ODS, %": "после очистки (ODS)"})
    plot(style_fig(px.bar(pl, x="Проблема", y="Доля записей, %", color="Этап", barmode="group",
                          title="Доля проблемных записей до и после очистки"), 400))
    st.caption("Для пропусков Track/artist/genre «после» — доля значений 'unknown': информация осталась неизвестной, но "
               "запись сохранена и значение явно помечено. У исключаемых и исправляемых проблем остаток в ODS равен нулю.")
    rj = q("reject_counts", B, d1, d2)
    if len(rj):
        with st.expander("Отклонённые записи: причины и примеры"):
            rj["reason"] = rj["reason"].map(cd.REASONS)
            st.dataframe(rj.rename(columns={"reason": "Причина", "rows": "Записей"}), width="stretch", hide_index=True)
            st.dataframe(q("reject_examples", B, d1, d2, 200), width="stretch", hide_index=True)
else:
    st.info("Раздел появится после загрузки ODS: нужен журнал качества.")

# ---------- 4. Динамика по dt ----------
st.header("4. Изменение объёмов данных по атрибуту dt")
rp = q("rows_by_period", B, d1, d2, freq)
mode = st.radio("Разрез", ["по слоям", "по партиям (STAGING)", "по партиям (ODS)"], horizontal=True)
if mode == "по слоям":
    f = rp.groupby(["layer", "period"], as_index=False)["rows"].sum()
    fig = px.bar(f, x="period", y="rows", color="layer", barmode="group", labels={"period": f"dt ({freq})", "rows": "Записей", "layer": "Слой"})
else:
    layer = "STAGING" if "STAGING" in mode else "ODS"
    f = rp[rp["layer"] == layer].assign(Партия=lambda d: d["batch_id"].map(lambda b: labels.get(int(b), str(b))))
    fig = px.bar(f, x="period", y="rows", color="Партия", labels={"period": f"dt ({freq})", "rows": "Записей"})
plot(style_fig(fig.update_layout(title=f"Записей по dt, детализация: {freq}"), 380))
st.caption("Партии занимают разные периоды dt, поэтому столбцы разных партий не накладываются; исключение — партия-исправление, "
           "которая приходит за уже загруженные даты и заменяет версии событий (в ODS её строки вытесняют строки исходной партии).")

# ---------- 5. Исходные и синтетические ----------
st.header("5. Исходный и синтетический наборы")
syn_b = tuple(b for b in B if b != 0)
orig = q("fetch_src", (0,), dmin, dmax) if 0 in labels else pd.DataFrame()
syn = q("fetch_src", syn_b, d1, d2) if syn_b else pd.DataFrame()
synth_ctx = None
if orig.empty or syn.empty:
    st.info("Для сравнения нужны исходная партия 0 и хотя бы одна выбранная синтетическая партия "
            "(`python music_app/cli.py generate`, затем `load`).")
else:
    st.caption("Исходный набор берётся целиком (партия 0), синтетический — выбранные партии в пределах периода dt. "
               "Значения читаются из SOURCE в том виде, в котором они были загружены (до очистки).")
    split = st.checkbox("Показывать каждую синтетическую партию отдельно", value=True)
    name0 = "Исходный (партия 0)"
    sets = {name0: orig}
    if split:
        for b in syn_b:
            part = syn[syn["batch_id"] == b]
            if len(part):
                sets[f"Партия {b}"] = part
    else:
        sets["Синтетика"] = syn
    prof = cd.profile_table(sets, ref_name=name0)
    st.dataframe(prof.reset_index(names="Показатель"), width="stretch", hide_index=True)
    st.caption("Заполненность — доли пропусков; уникальность — числа различных значений; аномалии — усечённые userID, "
               "пробелы, дубликаты, некорректные time/Day. Партия-исправление не содержит намеренных проблем: она "
               "повторяет часть записей первой партии с изменёнными значениями.")
    feat = st.selectbox("Признак для сравнения распределений", ["genre", "City", "Day", "artist", "Track", "час прослушивания"])
    if feat == "час прослушивания":
        hs, tvd = cd.hour_shares(orig, syn)
        fig = px.bar(hs.melt(id_vars="Час", var_name="Набор", value_name="Доля, %"), x="Час", y="Доля, %", color="Набор", barmode="group")
        tbl = hs
    else:
        tbl, tvd = cd.category_shares(orig[feat], syn[feat], top_n)
        fig = px.bar(tbl.melt(id_vars="Значение", value_vars=["Исходный, %", "Синтетический, %"], var_name="Набор", value_name="Доля, %"),
                     x="Доля, %", y="Значение", color="Набор", barmode="group", orientation="h").update_yaxes(autorange="reversed")
    c1, c2 = st.columns(2)
    c1.metric("Расстояние полной вариации (TVD)", f"{tvd:.3f}")
    c2.metric("Пересечение распределений (1 − TVD)", f"{1 - tvd:.1%}")
    plot(style_fig(fig.update_layout(title=f"Распределение: {feat}"), 120 + 28 * len(tbl) if feat != "час прослушивания" else 380))
    st.dataframe(tbl, width="stretch", hide_index=True)
    if feat in ("artist", "Track"):
        st.caption("Для признаков с огромным числом значений распределения почти не пересекаются по определению "
                   "(каждый трек встречается 1–2 раза), поэтому смотрите на долю треков вне исходного набора в таблице профиля.")
    g_tbl, g_tvd = cd.category_shares(orig["genre"], syn["genre"], 5)
    c_tbl, c_tvd = cd.category_shares(orig["City"], syn["City"], 5)
    _, h_tvd = cd.hour_shares(orig, syn)
    synth_ctx = {"genre_overlap": 1 - g_tvd, "city_overlap": 1 - c_tvd, "hour_overlap": 1 - h_tvd}

# ---------- 6. Витрина ----------
st.header("6. Аналитическая витрина на основе DWH")
st.caption("Витрина `dwh.mart_listening_daily`: гранулярность — дата × город × жанр; показатели — прослушивания, уникальные "
           "пользователи/треки/исполнители, прослушивания без исполнителя, среднее на пользователя, доля жанра в городе за день.")
mart_listens = int(mart["listens_cnt"].sum()) if len(mart) else 0
kpi_row([("Строк витрины", f"{len(mart):,}", "в периоде dt"), ("Σ прослушиваний", f"{mart_listens:,}", "listens_cnt"),
         ("Фактов в DWH", f"{facts:,}", "тот же период"),
         ("Сверка с фактами", "✓" if mart_listens == facts else "✗", "Σ listens_cnt = число фактов")])
if len(mart):
    DIMS = {"genre_name": "жанр", "city_name": "город", "day_of_week": "день недели", "month_key": "месяц"}
    dim = st.selectbox("Группировка", list(DIMS), format_func=DIMS.get)
    meas = st.selectbox("Показатель", ["listens_cnt", "unique_users", "unique_tracks", "unique_artists", "unknown_artist_cnt"])
    g = mart.groupby(dim, as_index=False)[meas].sum().sort_values(meas, ascending=False)
    g = g.head(top_n) if dim == "genre_name" else g.sort_values(dim)
    g[dim] = g[dim].astype(str)
    plot(style_fig(px.bar(g, x=dim, y=meas, title=f"{meas} по: {dim} (сумма по строкам витрины)"), 340))
    daily = mart.groupby(["dt", "city_name"], as_index=False)["listens_cnt"].sum()
    plot(style_fig(px.line(daily, x="dt", y="listens_cnt", color="city_name", markers=True, title="Прослушивания по датам и городам"), 340))
    st.dataframe(mart.head(500), width="stretch", hide_index=True)
    st.caption("Суммы уникальных (unique_*) по строкам не равны числу уникальных по всему периоду: различные значения не аддитивны.")

# ---------- 7. Итоговые выводы ----------
st.header("7. Итоговые выводы о качестве данных и корректности обработки")
ctx = {"layers": tot, "dq_tot": dq_tot, "ods_total": tot.get("ODS", 0), "mismatch": mismatch, "ba": ba, "coverage": cov,
       "rel": rel, "facts_period": facts, "mart_listens": mart_listens, "mart_cmp": cmp_df, "target_name": tgt_name,
       "target_error": t_err or "", "synth": synth_ctx}
row_summary = None
if tg_df is not None and len(mart) + len(tg_df) > 0:
    row_summary, bad_rows, daily_cmp = tr.compare_rows(mart, tg_df)
    ctx["row_summary"] = row_summary
for line in cd.build_conclusions(ctx):
    st.markdown("- " + line)

# ---------- 8. Проверка согласованности витрин ----------
st.header("8. Проверка согласованности аналитических витрин")
st.caption(f"Витрина переносится из PostgreSQL в {tgt_name} командой `cli.py transfer`. Показатели считаются одним и тем же "
           f"запросом в обеих СУБД за период {d1} … {d2}.")
if t_err:
    st.error("Целевая СУБД недоступна или витрина ещё не перенесена: " + t_err)
    st.info("Запустите ClickHouse (`docker compose up -d` в music_app/) или укажите `TARGET_DBMS=sqlite` в `.env`, затем "
            "`python music_app/cli.py transfer`.")
else:
    st.write(f"Типы данных: {target.types_note}. Дробные показатели сравниваются с допуском (Decimal ↔ float).")
    show = cmp_df.rename(columns={"PostgreSQL": "Исходная СУБД (PostgreSQL)", tgt_name: f"Целевая СУБД ({tgt_name})"})
    st.dataframe(show[["Проверяемый показатель", "Способ расчёта", "Исходная СУБД (PostgreSQL)", f"Целевая СУБД ({tgt_name})",
                       "Расхождение", "Допуск", "Результат", "Вывод"]], width="stretch", hide_index=True)
    n_bad = int((cmp_df["Результат"] != "Совпадает").sum())
    (st.success if n_bad == 0 else st.error)(
        f"Совпадает {len(cmp_df) - n_bad} из {len(cmp_df)} показателей." if n_bad == 0
        else f"Не совпадает {n_bad} из {len(cmp_df)} показателей — повторите перенос (`cli.py transfer`).")
    if row_summary:
        st.subheader("Построчное сравнение (ключ: dt, город, жанр)")
        kpi_row([("Строк в PostgreSQL", f"{row_summary['rows_pg']:,}", ""), (f"Строк в {tgt_name}", f"{row_summary['rows_target']:,}", ""),
                 ("Только в одной СУБД", f"{row_summary['only_in_pg'] + row_summary['only_in_target']:,}",
                  f"PG: {row_summary['only_in_pg']:,} · {tgt_name}: {row_summary['only_in_target']:,}"),
                 ("Строк с расхождением значений", f"{row_summary['rows_with_diff']:,}", "с учётом допусков")])
        pc = pd.DataFrame(row_summary["per_column"]).T.reset_index().rename(columns={"index": "Колонка"})
        st.dataframe(pc, width="stretch", hide_index=True)
        if len(bad_rows):
            st.warning("Примеры строк с расхождениями")
            st.dataframe(bad_rows.head(100), width="stretch", hide_index=True)
        dc = daily_cmp.melt(id_vars="dt", value_vars=["прослушиваний_pg", "прослушиваний_tg"], var_name="СУБД", value_name="Прослушиваний")
        dc["СУБД"] = dc["СУБД"].map({"прослушиваний_pg": "PostgreSQL", "прослушиваний_tg": tgt_name})
        plot(style_fig(px.bar(dc, x="dt", y="Прослушиваний", color="СУБД", barmode="group", title="Σ listens_cnt по датам в двух СУБД"), 340))
        bad_days = daily_cmp[~daily_cmp["совпадает"]]
        if len(bad_days):
            st.error("Даты с расхождением:")
            st.dataframe(bad_days, width="stretch", hide_index=True)
        else:
            st.success("По каждой дате число строк и Σ listens_cnt совпадают в обеих СУБД.")
