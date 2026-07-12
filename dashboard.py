"""
관리자용 로컬 대시보드 (표준 라이브러리만; 추가 설치 불필요).

실행:
    python dashboard.py            # 브라우저 자동 오픈 (http://127.0.0.1:8787)
    dashboard.bat  더블클릭        # (윈도우) 위와 동일

기능:
  - 심볼별 수집 현황(봉 수·기간·결측 추정·마지막 수집·데이터셋 생일)
  - 심볼 추가/삭제 → config.json 자동 반영 (추가 시 vision에 실제 데이터 있는지 검증)
  - [GitHub 반영] 설정 커밋·푸시 → 다음 자동 수집(Actions)에 심볼 변경 적용
  - 최근 지표 미니 차트, 수집 로그
  - [지금 수집(로컬)] collector.py를 백그라운드로 실행

보안: 127.0.0.1(자기 PC)에만 바인딩. 외부에서 접근 불가.
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "config.json")
PORT = 8787

# 로컬 수집 백그라운드 작업 상태
_job = {"running": False, "symbol": None, "output": "", "done": False, "returncode": None}
_job_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 설정 읽기/쓰기 (구조·주석 보존)
# ---------------------------------------------------------------------------
def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, CONFIG_PATH)


# ---------------------------------------------------------------------------
# vision 심볼 검증 (추가 시 실제 데이터 존재 확인)
# ---------------------------------------------------------------------------
def validate_symbol(symbol: str) -> tuple[bool, str]:
    import datetime as dt
    base = dt.datetime.now(dt.timezone.utc).date()
    days = [(base - dt.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(1, 7)]
    v = "https://data.binance.vision/data/futures/um/daily"
    for d in days:
        for kind, url in [
            ("klines", f"{v}/klines/{symbol}/5m/{symbol}-5m-{d}.zip"),
            ("metrics", f"{v}/metrics/{symbol}/{symbol}-metrics-{d}.zip"),
        ]:
            req = urllib.request.Request(url, method="HEAD",
                                         headers={"User-Agent": "netls-dashboard/1.0"})
            try:
                with urllib.request.urlopen(req, timeout=15) as r:
                    if r.status == 200 and kind == "metrics":
                        return True, f"검증 OK (vision에 {symbol} 데이터 존재, 기준일 {d})"
            except urllib.error.HTTPError:
                continue
            except Exception:  # noqa: BLE001
                continue
    return False, f"{symbol}: 최근 6일 vision 덤프에서 확인 실패(심볼명 오타이거나 미지원)"


# ---------------------------------------------------------------------------
# 데이터 현황 스캔
# ---------------------------------------------------------------------------
def _count_and_bounds(path: str, ts_col: int):
    """CSV 데이터행 수, 첫/마지막 타임스탬프."""
    n = 0
    first = last = None
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        next(reader, None)  # header
        for row in reader:
            if not row:
                continue
            n += 1
            t = int(row[ts_col])
            if first is None:
                first = t
            last = t
    return n, first, last


def scan_symbol(data_dir: str, symbol: str) -> dict:
    d = os.path.join(data_dir, symbol)
    info = {"symbol": symbol, "bars": 0, "first_ms": None, "last_ms": None,
            "funding": 0, "last_funding_ms": None, "gaps_est": None, "has_data": False}
    if not os.path.isdir(d):
        return info
    bars_files = sorted(f for f in os.listdir(d) if f.startswith("bars_") and f.endswith(".csv"))
    total = 0
    first = last = None
    for i, fn in enumerate(bars_files):
        n, fr, la = _count_and_bounds(os.path.join(d, fn), 1)
        total += n
        if fr is not None and first is None:
            first = fr
        if la is not None:
            last = la
    info["bars"] = total
    info["first_ms"] = first
    info["last_ms"] = last
    if total:
        info["has_data"] = True
    if first is not None and last is not None and total:
        expected = (last - first) // (5 * 60 * 1000) + 1
        info["gaps_est"] = max(0, expected - total)

    f_files = sorted(f for f in os.listdir(d) if f.startswith("funding_") and f.endswith(".csv"))
    ftotal = 0
    flast = None
    for fn in f_files:
        n, _, la = _count_and_bounds(os.path.join(d, fn), 1)
        ftotal += n
        if la is not None:
            flast = la
    info["funding"] = ftotal
    info["last_funding_ms"] = flast
    return info


def read_meta(data_dir: str) -> dict:
    p = os.path.join(data_dir, "_meta.json")
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {}


def read_runlog(data_dir: str, tail: int = 20) -> list[dict]:
    p = os.path.join(data_dir, "_runlog.csv")
    if not os.path.exists(p):
        return []
    with open(p, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return rows[-tail:][::-1]


def read_series(data_dir: str, symbol: str, n: int = 288) -> list[dict]:
    d = os.path.join(data_dir, symbol)
    if not os.path.isdir(d):
        return []
    bars_files = sorted(f for f in os.listdir(d) if f.startswith("bars_") and f.endswith(".csv"))
    if not bars_files:
        return []
    rows: list[dict] = []
    # 최신 파일부터 역순으로 채워 n개 확보
    for fn in reversed(bars_files):
        with open(os.path.join(d, fn), newline="", encoding="utf-8") as f:
            part = list(csv.DictReader(f))
        rows = part + rows
        if len(rows) >= n:
            break
    rows = rows[-n:]
    out = []
    for r in rows:
        def fv(k):
            v = r.get(k, "")
            return float(v) if v not in ("", None) else None
        out.append({
            "t": int(r["open_time"]), "kst": r["datetime_kst"],
            "close": fv("close"), "cvd": fv("cvd_delta"),
            "oi_delta": fv("oi_delta"),
            "net_long": fv("net_long_delta"), "net_short": fv("net_short_delta"),
        })
    return out


def build_status() -> dict:
    cfg = load_config()
    data_dir = os.path.join(ROOT, cfg.get("data_dir", "data"))
    configured = cfg.get("symbols", [])
    # 설정 심볼 + 데이터만 있는(설정에서 뺀) 심볼 모두 표시
    seen = set(configured)
    extra = []
    if os.path.isdir(data_dir):
        for name in sorted(os.listdir(data_dir)):
            if os.path.isdir(os.path.join(data_dir, name)) and name not in seen:
                extra.append(name)
    statuses = [scan_symbol(data_dir, s) for s in configured]
    statuses += [dict(scan_symbol(data_dir, s), only_data=True) for s in extra]
    return {
        "source": cfg.get("source"),
        "backfill_start": cfg.get("vision", {}).get("backfill_start_date"),
        "configured_symbols": configured,
        "symbols": statuses,
        "meta": read_meta(data_dir),
        "runlog": read_runlog(data_dir),
        "data_dir": data_dir,
    }


# ---------------------------------------------------------------------------
# git 커밋·푸시 (설정 반영)
# ---------------------------------------------------------------------------
def git_push_config() -> tuple[bool, str]:
    def run(args):
        return subprocess.run(["git"] + args, cwd=ROOT, capture_output=True,
                              text=True, timeout=60)
    try:
        run(["add", "config.json"])
        st = run(["diff", "--cached", "--quiet"])
        if st.returncode == 0:
            return True, "변경 없음 (config.json 이미 반영됨)"
        run(["commit", "-m", "config: 대시보드에서 심볼 목록 변경"])
        run(["pull", "--rebase", "--autostash", "origin", "master"])
        push = run(["push", "origin", "master"])
        if push.returncode != 0:
            return False, f"push 실패:\n{push.stderr or push.stdout}"
        return True, "GitHub 반영 완료. 다음 자동 수집부터 적용됩니다."
    except Exception as e:  # noqa: BLE001
        return False, f"git 오류: {e}"


# ---------------------------------------------------------------------------
# 로컬 수집 (백그라운드)
# ---------------------------------------------------------------------------
def start_local_collect(symbol: str | None):
    def worker():
        try:
            args = [sys.executable, os.path.join(ROOT, "collector.py")]
            if symbol:
                args += ["--symbol", symbol]
            env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
            proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                                  env=env, timeout=3600)
            with _job_lock:
                _job["output"] = (proc.stdout or "") + (proc.stderr or "")
                _job["returncode"] = proc.returncode
        except Exception as e:  # noqa: BLE001
            with _job_lock:
                _job["output"] = f"실행 오류: {e}"
                _job["returncode"] = -1
        finally:
            with _job_lock:
                _job["running"] = False
                _job["done"] = True

    with _job_lock:
        if _job["running"]:
            return False
        _job.update(running=True, symbol=symbol, output="", done=False, returncode=None)
    threading.Thread(target=worker, daemon=True).start()
    return True


# ---------------------------------------------------------------------------
# HTTP 핸들러
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 조용히
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text):
        body = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/":
            return self._html(PAGE)
        if u.path == "/api/status":
            return self._json(build_status())
        if u.path == "/api/series":
            q = parse_qs(u.query)
            sym = (q.get("symbol", [""])[0] or "").upper()
            n = int(q.get("n", ["288"])[0])
            cfg = load_config()
            data_dir = os.path.join(ROOT, cfg.get("data_dir", "data"))
            return self._json({"symbol": sym, "series": read_series(data_dir, sym, n)})
        if u.path == "/api/collect/status":
            with _job_lock:
                return self._json(dict(_job))
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        body = self._read_body()
        if u.path == "/api/symbol/add":
            sym = (body.get("symbol") or "").strip().upper()
            if not sym.isalnum():
                return self._json({"ok": False, "msg": "심볼명 형식 오류(영숫자만)."})
            ok, msg = validate_symbol(sym)
            if not ok:
                return self._json({"ok": False, "msg": msg})
            cfg = load_config()
            syms = cfg.setdefault("symbols", [])
            if sym in syms:
                return self._json({"ok": False, "msg": f"{sym}는 이미 목록에 있음."})
            syms.append(sym)
            save_config(cfg)
            return self._json({"ok": True, "msg": f"{sym} 추가됨. {msg}", "symbols": syms})
        if u.path == "/api/symbol/remove":
            sym = (body.get("symbol") or "").strip().upper()
            cfg = load_config()
            syms = cfg.setdefault("symbols", [])
            if sym not in syms:
                return self._json({"ok": False, "msg": f"{sym}는 목록에 없음."})
            syms.remove(sym)
            save_config(cfg)
            return self._json({"ok": True,
                               "msg": f"{sym} 제거됨(수집 중단). 기존 데이터 파일은 보존됨.",
                               "symbols": syms})
        if u.path == "/api/git/push":
            ok, msg = git_push_config()
            return self._json({"ok": ok, "msg": msg})
        if u.path == "/api/collect":
            sym = (body.get("symbol") or "").strip().upper() or None
            started = start_local_collect(sym)
            return self._json({"ok": started,
                               "msg": "수집 시작됨" if started else "이미 실행 중"})
        return self._json({"error": "not found"}, 404)


def main():
    no_browser = "--no-browser" in sys.argv
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    url = f"http://127.0.0.1:{PORT}"
    print(f"NetLS 관리자 대시보드 실행 중 → {url}")
    print("종료하려면 이 창에서 Ctrl+C 를 누르세요.")
    if not no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n종료합니다.")
        server.shutdown()


# HTML 페이지는 별도 파일에서 로드(가독성). 없으면 최소 안내.
try:
    with open(os.path.join(ROOT, "dashboard.html"), encoding="utf-8") as _f:
        PAGE = _f.read()
except OSError:
    PAGE = "<h1>dashboard.html 이 없습니다.</h1>"


if __name__ == "__main__":
    main()
