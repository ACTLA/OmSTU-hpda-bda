# OmSTU-hpda-bda-labs

Лабораторные работы по дисциплинам **«Высокопроизводительная обработка и анализ данных» (ВОиАД)** и
**«Администрирование больших данных» (АБД)**. Обе дисциплины развивают одно приложение `music_app`
на наборе данных `music_tracks`.

## Структура

```
music_app/        приложение (Streamlit + PostgreSQL)
  app/            main.py, common.py, views/ — страницы
  modules/        analysis.py (EDA), db.py (БД и загрузка), config.py
  sql/            DDL и функции слоёв: 01_… 05_*.sql
  data/           music_project.csv (исходный набор)
  reports/        сохранённая сводка EDA
docs/
  hpda/lab01/     ВОиАД, лабораторная №1
  bda/lab01/      АБД, лабораторная №1
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

## Лабораторные работы

Приложение единое для обеих дисциплин (требование заданий). Код разделён по дисциплинам:

| Дисциплина | Лабораторные | Код |
|---|---|---|
| ВОиАД | [№1](docs/hpda/lab01/README.md) | `modules/analysis.py`, `app/views/eda.py` |
| АБД | [№1](docs/bda/lab01/README.md) | `modules/db.py`, `sql/`, `app/views/pipeline.py`, `app/views/dwh.py` |
