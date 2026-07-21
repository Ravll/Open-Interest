"""
월별 CSV 파티션 저장소 (collector 전용).

파일 배치:
    data/<symbol>/bars_<YYYY-MM>.csv      5분봉 병합 테이블
    data/<symbol>/funding_<YYYY-MM>.csv   펀딩비(8h)
    data/_meta.json                       심볼별 백필 시작점(데이터셋 생일)
    data/_runlog.csv                      실행별 수집 로그(append)
    data/_last_run_summary.txt            직전 실행 요약(워크플로 커밋 메시지용)

설계:
- PRIMARY KEY (symbol, open_time)를 CSV에서 dict 병합(dedup)으로 강제한다.
- 항상 open_time 오름차순으로 기록해 git diff를 안정화한다.
- 월별 분할로 과거 파티션은 불변이 되어 커밋 churn을 현재 월 파일로 한정한다.
"""
from __future__ import annotations

import csv
import json
import os
from typing import Iterable

from netls import month_key

BARS_COLUMNS = [
    "symbol", "open_time", "datetime_utc", "datetime_kst",
    "open", "high", "low", "close", "volume", "quote_volume", "trades",
    "taker_buy_base", "taker_buy_quote",
    "sum_oi", "sum_oi_value", "cvd_delta", "oi_delta",
    "net_long_delta", "net_short_delta",
    "top_acc_ls_ratio", "top_pos_ls_ratio", "global_acc_ls_ratio", "taker_ls_vol_ratio",
]

FUNDING_COLUMNS = [
    "symbol", "funding_time", "datetime_utc", "datetime_kst",
    "funding_rate", "mark_price",
]

RUNLOG_COLUMNS = [
    "run_started_utc", "symbol", "domain", "rows_written",
    "range_start_ms", "range_end_ms", "missing_bars", "elapsed_sec", "note",
]


class CSVStore:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = data_dir
        os.makedirs(data_dir, exist_ok=True)

    # ---- 경로 --------------------------------------------------------------
    def _symbol_dir(self, symbol: str) -> str:
        d = os.path.join(self.data_dir, symbol)
        os.makedirs(d, exist_ok=True)
        return d

    def _bars_path(self, symbol: str, mkey: str) -> str:
        return os.path.join(self._symbol_dir(symbol), f"bars_{mkey}.csv")

    def _funding_path(self, symbol: str, mkey: str) -> str:
        return os.path.join(self._symbol_dir(symbol), f"funding_{mkey}.csv")

    def _list_partitions(self, symbol: str, prefix: str) -> list[str]:
        d = os.path.join(self.data_dir, symbol)
        if not os.path.isdir(d):
            return []
        files = [f for f in os.listdir(d) if f.startswith(prefix) and f.endswith(".csv")]
        return sorted(files)

    # ---- 마지막 타임스탬프 (증분 백필의 앵커) -------------------------------
    def last_bar_time(self, symbol: str) -> int | None:
        return self._last_time(symbol, "bars_", "open_time")

    def last_funding_time(self, symbol: str) -> int | None:
        return self._last_time(symbol, "funding_", "funding_time")

    def _last_time(self, symbol: str, prefix: str, col: str) -> int | None:
        parts = self._list_partitions(symbol, prefix)
        if not parts:
            return None
        # 정렬된 마지막 파티션의 마지막 데이터 행이 최대값(항상 오름차순 기록).
        path = os.path.join(self.data_dir, symbol, parts[-1])
        last = None
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                last = row
        return int(last[col]) if last else None

    def existing_bar_times(self, symbol: str, since_ms: int) -> set[int]:
        """since_ms 이후로 이미 저장된 open_time 집합 (중복 방지·gap 판정용)."""
        result: set[int] = set()
        for fname in self._list_partitions(symbol, "bars_"):
            path = os.path.join(self.data_dir, symbol, fname)
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    t = int(row["open_time"])
                    if t >= since_ms:
                        result.add(t)
        return result

    # ---- 쓰기 (월별 병합, dedup, 정렬) -------------------------------------
    def upsert_bars(self, symbol: str, rows: list[dict]) -> int:
        return self._upsert(symbol, rows, "open_time", BARS_COLUMNS, self._bars_path)

    def upsert_funding(self, symbol: str, rows: list[dict]) -> int:
        return self._upsert(symbol, rows, "funding_time", FUNDING_COLUMNS, self._funding_path)

    def _upsert(self, symbol: str, rows: list[dict], key: str,
                columns: list[str], path_fn) -> int:
        if not rows:
            return 0
        by_month: dict[str, list[dict]] = {}
        for r in rows:
            by_month.setdefault(month_key(int(r[key])), []).append(r)

        written = 0
        for mkey, new_rows in by_month.items():
            path = path_fn(symbol, mkey)
            merged: dict[int, dict] = {}
            if os.path.exists(path):
                with open(path, newline="", encoding="utf-8") as f:
                    for row in csv.DictReader(f):
                        merged[int(row[key])] = row
            for r in new_rows:
                k = int(r[key])
                if k not in merged:
                    written += 1
                merged[k] = {c: r.get(c, "") for c in columns}
            tmp = path + ".tmp"
            with open(tmp, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=columns)
                w.writeheader()
                for k in sorted(merged):
                    w.writerow(merged[k])
            os.replace(tmp, path)  # 원자적 교체
        return written

    # ---- 메타 (데이터셋 생일) ----------------------------------------------
    def _meta_path(self) -> str:
        return os.path.join(self.data_dir, "_meta.json")

    def load_meta(self) -> dict:
        p = self._meta_path()
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return {}

    def save_meta(self, meta: dict) -> None:
        tmp = self._meta_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, self._meta_path())

    def set_meta_once(self, symbol: str, key: str, value) -> bool:
        """해당 (symbol,key)가 없을 때만 기록(생일은 최초 1회 고정). 기록 시 True."""
        meta = self.load_meta()
        node = meta.setdefault(symbol, {})
        if key in node:
            return False
        node[key] = value
        self.save_meta(meta)
        return True

    # ---- 실행 로그 ---------------------------------------------------------
    def append_runlog(self, entries: list[dict]) -> None:
        if not entries:
            return
        path = os.path.join(self.data_dir, "_runlog.csv")
        exists = os.path.exists(path)
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=RUNLOG_COLUMNS)
            if not exists:
                w.writeheader()
            for e in entries:
                w.writerow({c: e.get(c, "") for c in RUNLOG_COLUMNS})

    def write_summary(self, text: str) -> None:
        with open(os.path.join(self.data_dir, "_last_run_summary.txt"), "w",
                  encoding="utf-8") as f:
            f.write(text)
