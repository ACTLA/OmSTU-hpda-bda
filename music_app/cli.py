"""Командная строка ЛР2 (АБД): генерация синтетики, загрузка партий, витрина, перенос, проверки.

Запуск из корня репозитория:   pipenv run python music_app/cli.py <команда> [параметры]
  init                         создать БД, схемы, таблицы и функции (идемпотентно)
  original                     загрузить исходный CSV (партия 0) и прогнать по слоям
  generate [--rows N ...]      сгенерировать партии в music_app/data/synthetic/
  load [файлы]                 загрузить партии (по умолчанию все batch_*.csv) и прогнать их периоды по слоям
  transfer [--sdt D --edt D]   перенести витрину в целевую СУБД
  check [--sdt D --edt D]      повторная загрузка, сверка с эталоном pandas, сверка витрин двух СУБД
  status                       объёмы по партиям
  demo                         весь сценарий ЛР2 целиком
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from modules import analysis as an  # noqa: E402
from modules import db, etl, generator as gen, transfer as tr  # noqa: E402
from modules.config import DATA_PATH, SYNTH_DIR, SYNTH_START  # noqa: E402


def _d(s):
    return date.fromisoformat(s)


def _overrides(items):
    out = {}
    for it in items or []:
        k, v = it.split("=", 1)
        out[k] = (v.lower() in ("1", "true", "yes")) if k == "truncate_userid" else float(v) if "." in v else int(v)
    return out


def cmd_generate(a):
    prof = gen.build_profile(an.load_raw(DATA_PATH))
    paths = gen.generate_series(prof, n_batches=a.batches, rows=a.rows, start=a.start, days=a.days, preset=a.preset,
                                seed=a.seed, correction=not a.no_correction, first_batch_id=a.first_id,
                                **_overrides(a.set))
    for p in paths:
        m = json.loads(p.with_suffix(".meta.json").read_text(encoding="utf-8"))
        print(f"{p.name}: {m['rows']:,} строк, dt {m['dt_min']} … {m['dt_max']}, {m['kind']}; {m['generated']}")


def cmd_load(a):
    files = [Path(f) for f in a.files] or etl.batch_files()
    if not files:
        sys.exit(f"Нет файлов партий в {SYNTH_DIR}. Сначала: generate")
    for f in files:
        etl.load_batch_file(f, with_transfer=not a.no_transfer)


def _period(a):
    if a.sdt and a.edt:
        return a.sdt, a.edt
    s, e = etl.full_period()
    return a.sdt or s, a.edt or e


def cmd_transfer(a):
    s, e = _period(a)
    print(tr.transfer_mart(s, e))


def cmd_check(a):
    s, e = _period(a)
    print(f"Период проверки: {s} … {e}")
    r = etl.rerun_check(s, e)
    print("1) Повторная загрузка не меняет данные:", "ДА" if r["ok"] else f"НЕТ, изменилось: {r['diff']}")
    ref = etl.check_against_reference(s, e)
    print(f"2) Эталон pandas: ODS {ref['ods_ref']:,} (эталон) / {ref['ods_db']:,} (БД), расхождений содержимого ODS: "
          f"{ref['ods_rows_mismatch']}, расходящиеся колонки журнала: {ref['log_columns_mismatch'] or 'нет'}, "
          f"витрина: {'совпадает' if ref['mart']['ok'] else ref['mart']}")
    print("   ->", "СОВПАДАЕТ" if ref["ok"] else "РАСХОЖДЕНИЕ")
    t = tr.get_target()
    m = tr.compare_metrics(s, e, t)
    print(f"3) Витрина PostgreSQL ↔ {t.name}:")
    print(m[["Проверяемый показатель", "PostgreSQL", t.name, "Расхождение", "Результат"]].to_string(index=False))
    pg = db.query("SELECT * FROM dwh.mart_listening_daily WHERE dt BETWEEN %s AND %s", (s, e))[tr.MART_COLS]
    tg = t.query(f"SELECT {', '.join(tr.MART_COLS)} FROM {t.table} WHERE {t.where(s, e)}")
    summ, _, _ = tr.compare_rows(pg, tg)
    print("   построчно:", {k: v for k, v in summ.items() if k != "per_column"})
    ok = r["ok"] and ref["ok"] and (m["Результат"] == "Совпадает").all() and summ["ok"]
    print("ИТОГ:", "ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ" if ok else "ЕСТЬ РАСХОЖДЕНИЯ")
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("original").add_argument("--no-transfer", action="store_true")
    g = sub.add_parser("generate")
    g.add_argument("--batches", type=int, default=2, help="число последовательных партий")
    g.add_argument("--rows", type=int, default=15000, help="записей в каждой партии")
    g.add_argument("--start", type=_d, default=SYNTH_START, help="начало dt первой партии (ГГГГ-ММ-ДД)")
    g.add_argument("--days", type=int, default=28, help="длина периода одной партии, дней")
    g.add_argument("--preset", choices=list(gen.PRESETS), default="source", help="доли проблем качества")
    g.add_argument("--seed", type=int, default=42)
    g.add_argument("--first-id", type=int, default=1)
    g.add_argument("--no-correction", action="store_true", help="не создавать партию-исправление первой партии")
    g.add_argument("--set", action="append", help="переопределить параметр, напр. --set miss_artist=0.2")
    ld = sub.add_parser("load")
    ld.add_argument("files", nargs="*")
    ld.add_argument("--no-transfer", action="store_true")
    for name in ("transfer", "check"):
        s = sub.add_parser(name)
        s.add_argument("--sdt", type=_d)
        s.add_argument("--edt", type=_d)
    sub.add_parser("status")
    dm = sub.add_parser("demo")
    dm.add_argument("--rows", type=int, default=15000)
    dm.add_argument("--preset", choices=list(gen.PRESETS), default="source")
    a = ap.parse_args()
    {"init": lambda: db.init_schema(), "original": lambda: etl.load_original(not a.no_transfer),
     "generate": lambda: cmd_generate(a), "load": lambda: cmd_load(a), "transfer": lambda: cmd_transfer(a),
     "check": lambda: cmd_check(a), "status": lambda: print(etl.status_table().to_string(index=False)),
     "demo": lambda: etl.demo(rows=a.rows, preset=a.preset)}[a.cmd]()


if __name__ == "__main__":
    main()
