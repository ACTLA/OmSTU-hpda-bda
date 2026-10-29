from datetime import timedelta

import pandas as pd
import plotly.express as px
import streamlit as st

from app.common import plot, style_fig
from modules import db
from modules.config import DT_START, DT_WEEKS

st.title("Хранилище данных (DWH)")


def run(label, fn, *args):
    try:
        with st.spinner(label):
            return fn(*args)
    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")
        return None


def tbl(title, cols, color):
    rows = "".join(f'<TR><TD ALIGN="LEFT"><FONT COLOR="#000000" POINT-SIZE="11">{c}</FONT></TD></TR>' for c in cols)
    return (f'<<TABLE BORDER="0" CELLBORDER="1" CELLSPACING="0" CELLPADDING="3" COLOR="#6b7280">'
            f'<TR><TD BGCOLOR="{color}"><FONT COLOR="#000000"><B>{title}</B></FONT></TD></TR>{rows}</TABLE>>')


ARCH = """digraph A { rankdir=LR; bgcolor="transparent"; nodesep=0.4;
 node [shape=box, style="rounded,filled", fillcolor="#1f2430", color="#A86F52", fontcolor="#ECE9E4", fontsize=11];
 edge [color="#374151", fontcolor="#111827", fontsize=10];
 csv [label="CSV\\nmusic_project.csv", shape=note];
 src [label="src.music_tracks\\n(всё текстом + dt)"];
 stg [label="stg.music_tracks\\n(as is)"];
 ods [label="ods.music_tracks\\n(очищено, типы)"];
 dwh [label="dwh\\nснежинка: факт + измерения", fillcolor="#2a2f3d"];
 app [label="Streamlit\\nEDA · конвейер · DWH", shape=component];
 csv -> src [label="Python: load_source()\\n+ генерация dt"];
 src -> stg [label="stg.load_staging()"];
 stg -> ods [label="ods.load_ods(p_sdt, p_edt)\\nочистка"];
 ods -> dwh [label="dwh.load_dwh(p_sdt, p_edt)"];
 csv -> app [label="EDA", style=dashed];
 dwh -> app [label="запросы", style=dashed];
}"""

FACT, DIM, SUB = "#E3A27A", "#9CCBCF", "#EBD79F"
SCHEMA = f"""digraph S {{ rankdir=LR; bgcolor="transparent"; nodesep=0.35; ranksep=0.9;
 node [shape=plaintext]; edge [color="#374151", arrowsize=0.8];
 fact [label={tbl("fact_listening", ["PK listening_id", "FK date_key", "FK time_key", "FK user_key", "FK track_key", "FK genre_key", "FK city_key"], FACT)}];
 date [label={tbl("dim_date", ["PK date_key", "dt", "FK month_key", "day_of_week", "weekday_num"], DIM)}];
 month [label={tbl("dim_month", ["PK month_key", "year_num", "month_num", "month_name"], SUB)}];
 time [label={tbl("dim_time", ["PK time_key", "listen_time", "hour_num", "FK daypart_key"], DIM)}];
 part [label={tbl("dim_daypart", ["PK daypart_key", "daypart_name"], SUB)}];
 track [label={tbl("dim_track", ["PK track_key", "track_name", "FK artist_key"], DIM)}];
 artist [label={tbl("dim_artist", ["PK artist_key", "artist_name"], SUB)}];
 genre [label={tbl("dim_genre", ["PK genre_key", "genre_name"], DIM)}];
 city [label={tbl("dim_city", ["PK city_key", "city_name"], DIM)}];
 user [label={tbl("dim_user", ["PK user_key", "user_id"], DIM)}];
 fact -> date; fact -> time; fact -> track; fact -> genre; fact -> city; fact -> user;
 date -> month; time -> part; track -> artist;
}}"""

t1, t2, t3 = st.tabs(["Архитектура", "Схема DWH (снежинка)", "Загрузка и запросы"])
with t1:
    st.graphviz_chart(ARCH)
    st.caption("Слои: источник → STAGING → ODS → DWH. Очистка выполняется внутри ods.load_ods().")
with t2:
    view = st.radio("Расположение", ["Слева направо", "Радиальное (снежинка)"], horizontal=True)
    if view.startswith("Радиальное"):
        # ручные координаты в дюймах (x, y): факт в центре, подизмерения дальше от центра
        POS = {"fact": (0, 0), "date": (-4.8, 3), "month": (-7.8, 3), "time": (0, 4.8), "part": (0, 7.6),
               "track": (4.8, 3), "artist": (7.8, 3), "genre": (-4.5, -3.5), "city": (0, -4.5), "user": (4.5, -3.5)}
        dot = SCHEMA.replace("rankdir=LR;", "layout=neato; splines=true;")
        for name, (x, y) in POS.items():
            dot = dot.replace(f"\n {name} [label=", f'\n {name} [pos="{x},{y}!", label=')
        st.graphviz_chart(dot)
    else:
        st.graphviz_chart(SCHEMA)
    st.caption("Оранжевый — таблица фактов (гранулярность: одно прослушивание). Бирюзовый — измерения, жёлтый — "
               "вынесенные подизмерения (признак «снежинки»).")
with t3:
    with st.container(border=True):
        st.subheader("ODS → DWH")
        st.caption("dwh.load_dwh(p_sdt, p_edt): пополняет измерения, пересоздаёт факты за период. Повторный запуск дублей не создаёт.")
        d1, d2 = st.columns(2)
        sdt = d1.date_input("p_sdt (начало)", DT_START, key="dwh_s")
        edt = d2.date_input("p_edt (конец)", DT_START + timedelta(weeks=DT_WEEKS) - timedelta(days=1), key="dwh_e")
        if st.button("Выполнить dwh.load_dwh(p_sdt, p_edt)"):
            n = run("Загружаю в DWH…", db.run_dwh, sdt, edt)
            if n is not None:
                st.success(f"В факты за период загружено строк: {n:,}")
    st.header("Проверочные и аналитические запросы")
    sel = st.selectbox("Запрос", list(db.DWH_CHECKS))
    st.code(db.DWH_CHECKS[sel], language="sql")
    res = run("Считаю…", db.query, db.DWH_CHECKS[sel])
    if res is not None:
        st.dataframe(res, width="stretch", hide_index=True)
        if len(res) > 1 and res.shape[1] >= 2 and pd.api.types.is_numeric_dtype(res.iloc[:, -1]) \
                and "city_name" not in res.columns and sel != "Строк в таблицах DWH":
            x = res.columns[-2]
            plot(style_fig(px.bar(res, x=x, y=res.columns[-1]), 320), width="stretch")
