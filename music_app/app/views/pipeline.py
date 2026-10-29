from datetime import timedelta

import streamlit as st

from app.common import kpi_row
from modules import db
from modules.config import DATA_PATH, DT_START, DT_WEEKS

st.title("Загрузка данных: CSV → STAGING → ODS")
st.caption("Подключение берётся из файла .env (см. .env.example). Каждый шаг можно запускать повторно.")


def run(label, fn, *args):
    try:
        with st.spinner(label):
            return fn(*args)
    except Exception as e:
        hint = " Частая причина: неверный пароль в .env или не запущен сервер PostgreSQL." \
            if isinstance(e, UnicodeDecodeError) else ""
        st.error(f"{type(e).__name__}: {e}{hint}")
        return None


with st.container(border=True):
    st.subheader("0. База данных")
    p = db.conn_params()
    st.write(f"`{p['user']}@{p['host']}:{p['port']}/{p['dbname']}`")
    if st.button("Создать БД, схемы, таблицы и функции"):
        if run("Создаю…", db.init_schema) is None:
            pass
        else:
            st.success("Готово: схемы src/stg/ods/dwh, таблицы и функции созданы")

c1, c2 = st.columns(2)
with c1, st.container(border=True):
    st.subheader("1. CSV → src")
    st.caption("Все поля текстом, как в файле, + синтетическое поле dt.")
    if st.button("Загрузить CSV в src", disabled=not DATA_PATH.exists()):
        n = run("Загружаю CSV…", db.load_source)
        if n is not None:
            st.success(f"Загружено строк: {n:,}")
with c2, st.container(border=True):
    st.subheader("2. src → STAGING")
    st.caption("Функция stg.load_staging() без параметров, перенос as is.")
    if st.button("Выполнить stg.load_staging()"):
        n = run("Переношу…", db.run_staging)
        if n is not None:
            st.success(f"Перенесено строк: {n:,}")

with st.container(border=True):
    st.subheader("3. STAGING → ODS (инкремент с очисткой)")
    st.caption("Функция ods.load_ods(p_sdt, p_edt): удаляет период в ODS и грузит его заново. "
               "Повторный запуск с теми же датами дублей не создаёт.")
    d1, d2 = st.columns(2)
    sdt = d1.date_input("p_sdt (начало)", DT_START)
    edt = d2.date_input("p_edt (конец)", DT_START + timedelta(weeks=DT_WEEKS) - timedelta(days=1))
    if st.button("Выполнить ods.load_ods(p_sdt, p_edt)"):
        n = run("Загружаю в ODS…", db.run_ods, sdt, edt)
        if n is not None:
            st.success(f"В ODS за период {sdt} … {edt} загружено строк: {n:,}")

st.header("Состояние слоёв и проверочные запросы")
sel = st.selectbox("Проверочный запрос", list(db.CHECKS))
st.code(db.CHECKS[sel], language="sql")
res = run("Считаю…", db.query, db.CHECKS[sel])
if res is not None:
    st.dataframe(res, width="stretch", hide_index=True)
