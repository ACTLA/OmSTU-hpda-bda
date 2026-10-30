# OmSTU-hpda-bda-labs

Лабораторные работы по дисциплинам **«Высокопроизводительная обработка и анализ данных» (ВОиАД)** и
**«Администрирование больших данных» (АБД)**. Обе дисциплины развивают одно приложение `music_app`
на наборе данных `music_tracks`.

## Структура

```
music_app/        приложение (Streamlit + PostgreSQL)
  app/            main.py, common.py, views/ — страницы
  modules/        analysis.py (EDA), db.py (БД и загрузка), config.py,
                  generator.py, quality.py, etl.py, transfer.py, monitor.py, compare_data.py (ЛР2)
  sql/            DDL и функции слоёв: 01_… 06_*.sql
  cli.py          командная строка ЛР2: генерация, загрузка партий, перенос, проверки
  tests/          проверки ЛР2 без БД (python music_app/tests/check_lab2.py)
  data/           music_project.csv (исходный набор)
  reports/        сохранённая сводка EDA
docs/
  hpda/lab01/     ВОиАД, лабораторная №1
  bda/lab01/      АБД, лабораторная №1
  hpda/lab02/     ВОиАД, лабораторная №2
  bda/lab02/      АБД, лабораторная №2
```

## Запуск

Требуется Python 3.10+ и PostgreSQL (пользователь `postgres`).

```
pip install -r music_app/requirements.txt
copy music_app\.env.example music_app\.env     (впишите пароль PostgreSQL в .env)
streamlit run music_app/app/main.py
```
Запускать из корня репозитория (там лежит `.streamlit/config.toml` с темой).

CSV должен лежать в `music_app/data/music_project.csv`.
Порядок работы со страницей «Загрузка данных»: создать БД → загрузить CSV → STAGING → ODS;
затем на странице «Хранилище (DWH)» загрузить ODS → DWH.

Для ЛР2 нужна вторая СУБД: ClickHouse (`docker compose -f music_app/docker-compose.yml up -d`)
или SQLite без установки (`TARGET_DBMS=sqlite` в `.env`). Новые строки `.env` — в `.env.example`.
Загрузка синтетики и витрины: `python music_app/cli.py demo`, проверка: `python music_app/cli.py check`.

## Лабораторные работы

Приложение единое для обеих дисциплин (требование заданий). Код разделён по дисциплинам:

| Дисциплина | Лабораторные | Код |
|---|---|---|
| ВОиАД | [№1](docs/hpda/lab01/README.md) | `modules/analysis.py`, `app/views/eda.py` |
| АБД | [№1](docs/bda/lab01/README.md) | `modules/db.py`, `sql/`, `app/views/pipeline.py`, `app/views/dwh.py` |
| ВОиАД | [№2](docs/hpda/lab02/README.md) | `app/views/compare.py`, `modules/monitor.py`, `modules/compare_data.py` |
| АБД | [№2](docs/bda/lab02/README.md) | `modules/generator.py`, `modules/etl.py`, `modules/transfer.py`, `sql/06_mart.sql`, `cli.py` |
