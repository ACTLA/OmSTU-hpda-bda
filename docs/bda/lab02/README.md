# АБД · Лабораторная работа №2

**Тема:** организация хранения и управления данными.
**Основа:** хранилище из [лабораторной №1](../lab01/README.md): `SOURCE → STAGING → ODS → DWH`.

## Что реализовано

| Требование задания | Где |
|---|---|
| Генератор синтетических данных (объём, период `dt`, партии, сохранение в CSV) | `music_app/modules/generator.py` → `generate_batch()`, `generate_series()`, `save_batch()` |
| Генерация по структуре и распределениям `music_tracks` | `build_profile()`: пользователи, каталог треков, жанры, города, дни недели, время |
| Изменение существующих записей | `make_correction()`: партия-исправление; в ODS новая версия события заменяет старую |
| Намеренные проблемы качества | `GenConfig` и профили `clean` / `source` / `dirty` (пропуски, пробелы, усечённые `userID`, дубликаты, брак `Day`/`time`/`City`/`userID`) |
| Не менее двух партий с разными периодами `dt` | `cli.py generate`: по умолчанию партии 1 и 2 (07.07–03.08 и 04.08–31.08.2025) + партия-исправление 3 |
| Поступление без пересоздания хранилища | `db.load_batch()`: удаляет и заново копирует только свою партию (`batch_id`) |
| SOURCE → STAGING (функция без параметров, as is) | `sql/02_load_staging.sql` → `stg.load_staging()` (теперь переносит и `batch_id`) |
| STAGING → ODS по `p_sdt`, `p_edt`, очистка, повторный запуск | `sql/03_load_ods.sql` → `ods.load_ods()`; временные таблицы `ON COMMIT DROP` |
| Журнал качества | `ods.dq_log` (по дате и партии), `ods.dq_reject` (отклонённые записи), `ods.load_run_log` (запуски) |
| Витрина: назначение, показатели, детализация | `sql/06_mart.sql` → `dwh.mart_listening_daily` |
| Первоначальная и повторная загрузка витрины | `dwh.load_mart(p_sdt, p_edt)`: период удаляется и считается заново |
| Вторая СУБД (ClickHouse; запасной вариант — SQLite) | `music_app/modules/transfer.py`, `music_app/docker-compose.yml` |
| Перенос и повторный перенос | `transfer.transfer_mart()`: период в целевой таблице удаляется и вставляется заново |
| Проверка соответствия (число записей, показатели) | `transfer.compare_metrics()` (14 показателей), `transfer.compare_rows()` (построчно) |
| Проверка повторной загрузки, сверка с эталоном | `etl.rerun_check()`, `etl.check_against_reference()`, команда `cli.py check` |

## Витрина `dwh.mart_listening_daily`

Назначение: динамика интереса к жанрам по городам и дням, доля прослушиваний без исполнителя.
Гранулярность: **дата × город × жанр**. Показатели: `listens_cnt`, `unique_users`, `unique_tracks`,
`unique_artists` (без `unknown`), `unknown_artist_cnt`, `avg_listens_per_user`, `city_day_share`.

Типы при переносе (PostgreSQL → ClickHouse): `DATE → Date`, `TEXT → LowCardinality(String)`,
`BIGINT → UInt64`, `NUMERIC(12,4)` / `NUMERIC(10,6) → Decimal(12,4)` / `Decimal(10,6)`.
В ClickHouse таблица — `MergeTree`, партиции по месяцам, сортировка по `(dt, city_name, genre_name)`.
Дробные показатели сравниваются с допуском 1e-6, остальные — строго.

## Правила версий и журнал

Событие прослушивания определяется ключом `(user_id, listen_time, dt)`. Если ключ пришёл в нескольких партиях,
в ODS остаётся версия с большим `batch_id`. Журнал `ods.dq_log` пересоздаётся за период вместе с ODS, поэтому
всегда соответствует фактическому результату очистки. Баланс: `rows_stg − отклонено − dup_removed − superseded = rows_ods`.

## Запуск

```
docker compose -f music_app/docker-compose.yml up -d          # ClickHouse (или TARGET_DBMS=sqlite в .env)
pipenv run python music_app/cli.py init                        # схемы, таблицы, функции (идемпотентно)
pipenv run python music_app/cli.py demo                        # исходные данные + 2 партии + исправление + витрина + перенос
pipenv run python music_app/cli.py check                       # повторная загрузка, эталон pandas, сверка двух СУБД
```

По шагам: `original` → `generate [--rows N --batches K --preset dirty --set miss_artist=0.2]` → `load` → `transfer` → `check`.
Автоматические проверки без БД: `python music_app/tests/check_lab2.py`.
