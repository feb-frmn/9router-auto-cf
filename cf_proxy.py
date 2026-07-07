#!/usr/bin/env python3
"""
cf_proxy.py — Cloudflare Workers AI Inference Proxy (Anti-Ban + Neuron Tracking)

OpenAI-compatible proxy that rotates across all harvested CF accounts.
Auto-skips accounts on 429, proactively skips accounts that exhausted
their 10,000 free daily neurons.

Features:
  - Round-robin account rotation (race-safe)
  - Neuron budget estimation per model (proactive skip before 429)
  - 429 auto-cooldown (90s for rate limit, 00:00 UTC for daily limit)
  - OpenAI-compatible API (/v1/chat/completions, /v1/models)
  - Streaming support (SSE)
  - CF passthrough (/ai/run/:model)
  - Dashboard at http://localhost:8750

Usage:
  python3 cf_proxy.py                           # start proxy
  python3 cf_proxy.py --port 9000               # custom port
  python3 cf_proxy.py --key mysecret            # API key auth
  python3 cf_proxy.py --import-9router          # import from 9Router DB

9Router integration:
  Base URL: http://127.0.0.1:8750/v1
  API Key: (whatever you set with --key, or empty)
"""

import os, sys, json, time, threading, argparse, sqlite3
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

SCRIPT_DIR = Path(__file__).resolve().parent
KEY_FILE   = SCRIPT_DIR / "cf_keys.txt"
DB_FILE    = SCRIPT_DIR / "data" / "accounts.db"
NINEROUTER_DB = Path.home() / ".9router" / "db" / "data.sqlite"

# ─── Neuron Estimation ─────────────────────────────────────────────────────────
# Rates from CF pricing (neurons per 1M tokens)
# https://developers.cloudflare.com/workers-ai/platform/pricing/

NEURON_FREE_DAILY = 10000

RATES = {
    "@cf/meta/llama-3.2-1b-instruct": {"in": 2457, "out": 18252},
    "@cf/meta/llama-3.2-3b-instruct": {"in": 4625, "out": 30475},
    "@cf/meta/llama-3.1-8b-instruct-fp8-fast": {"in": 4119, "out": 34868},
    "@cf/meta/llama-3.1-8b-instruct-awq": {"in": 4119, "out": 34868},
    "@cf/meta/llama-3.1-70b-instruct": {"in": 26668, "out": 204805},
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": {"in": 26668, "out": 204805},
    "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b": {"in": 45170, "out": 443756},
    "@cf/mistral/mistral-7b-instruct-v0.1": {"in": 10000, "out": 17300},
    "@cf/mistralai/mistral-small-3.1-24b-instruct": {"in": 31876, "out": 50488},
    "@cf/qwen/qwen2.5-coder-32b-instruct": {"in": 60000, "out": 90909},
}

# Unknown models: use 70b-class (high = skip early, don't overrun)
DEFAULT_RATE = {"in": 26668, "out": 204805}
_warned_models = set()

def estimate_neurons(model, prompt_tokens=0, completion_tokens=0):
    rate = RATES.get(model)
    if not rate:
        rate = DEFAULT_RATE
        if model not in _warned_models:
            print(f"  [WARN] Unknown model '{model}' — using 70b-class fallback")
            _warned_models.add(model)
    return (prompt_tokens / 1e6) * rate["in"] + (completion_tokens / 1e6) * rate["out"]

def today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ─── Database ──────────────────────────────────────────────────────────────────

def init_db(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(db_path), check_same_thread=False)
    db.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE,
            api_key TEXT NOT NULL,
            account_id TEXT NOT NULL,
            is_active INTEGER DEFAULT 1,
            cooldown_until REAL DEFAULT 0,
            neurons_today REAL DEFAULT 0,
            neurons_day TEXT DEFAULT '',
            requests_today INTEGER DEFAULT 0,
            last_used REAL DEFAULT 0,
            error_count INTEGER DEFAULT 0
        )
    """)
    db.commit()
    return db

def import_from_file(db):
    """Import accounts from cf_keys.txt into DB."""
    if not KEY_FILE.exists():
        print("❌ cf_keys.txt not found. Run cf_farmer.py first.")
        return 0
    imported = 0
    skipped = 0
    for line in KEY_FILE.read_text().splitlines():
        parts = line.strip().split("|")
        if len(parts) != 4:
            continue
        name, base_url, api_key, models = parts
        # Extract account_id from base_url
        m = extract_account_id(base_url)
        if not m:
            continue
        try:
            db.execute(
                "INSERT OR IGNORE INTO accounts (name, api_key, account_id) VALUES (?, ?, ?)",
                (name, api_key, m)
            )
            if db.total_changes > 0:
                imported += 1
            else:
                skipped += 1
        except sqlite3.IntegrityError:
            skipped += 1
    db.commit()
    print(f"📋 Imported {imported} accounts from cf_keys.txt ({skipped} skipped)")
    return imported

def import_from_9router(db):
    """Import from 9Router SQLite DB."""
    if not NINEROUTER_DB.exists():
        print(f"❌ 9Router DB not found: {NINEROUTER_DB}")
        return 0
    src = sqlite3.connect(str(NINEROUTER_DB))
    try:
        rows = src.execute(
            "SELECT name, data FROM providerConnections WHERE provider = 'cloudflare-ai'"
        ).fetchall()
    except Exception as e:
        print(f"❌ 9Router DB error: {e}")
        return 0
    finally:
        src.close()
    imported = 0
    skipped = 0
    for name, data_json in rows:
        try:
            data = json.loads(data_json)
        except Exception:
            skipped += 1
            continue
        api_key = data.get("apiKey", "")
        account_id = data.get("providerSpecificData", {}).get("accountId", "")
        if not api_key or not account_id:
            skipped += 1
            continue
        try:
            db.execute(
                "INSERT OR IGNORE INTO accounts (name, api_key, account_id) VALUES (?, ?, ?)",
                (name, api_key, account_id)
            )
            if db.total_changes > 0:
                imported += 1
            else:
                skipped += 1
        except sqlite3.IntegrityError:
            skipped += 1
    db.commit()
    print(f"📋 Imported {imported} accounts from 9Router ({skipped} skipped)")
    return imported

def extract_account_id(base_url):
    import re
    m = re.search(r'/accounts/([a-f0-9]+)/', base_url)
    return m.group(1) if m else None

# ─── Account Pool ──────────────────────────────────────────────────────────────

class AccountPool:
    def __init__(self, db, cooldown429=90, reserve=250):
        self.db = db
        self.cooldown429 = cooldown429
        self.reserve = reserve
        self._cursor = 0
        self._lock = threading.Lock()
        self._reserved = {}  # account_id → reserved neurons
        self._reserved_day = today_utc()

    def _roll_day(self):
        today = today_utc()
        if self._reserved_day != today:
            self._reserved.clear()
            self._reserved_day = today

    def _effective_neurons(self, row):
        committed = row[7] if row[8] == today_utc() else 0  # neurons_today if same day
        return committed + self._reserved.get(row[0], 0)

    def get_available(self):
        """Get next available account (round-robin, race-safe)."""
        with self._lock:
            self._roll_day()
            now = time.time()
            # Get all eligible accounts
            rows = self.db.execute("""
                SELECT * FROM accounts
                WHERE is_active = 1 AND cooldown_until < ?
                ORDER BY id
            """, (now,)).fetchall()
            # Filter by neuron budget
            eligible = [r for r in rows if self._effective_neurons(r) < NEURON_FREE_DAILY]
            if not eligible:
                return None
            # Round-robin
            idx = self._cursor % len(eligible)
            self._cursor = (idx + 1) % len(eligible)
            row = eligible[idx]
            # Reserve neurons
            self._reserved[row[0]] = self._reserved.get(row[0], 0) + self.reserve
            return {"id": row[0], "name": row[1], "api_key": row[2], "account_id": row[3]}

    def release(self, account_id):
        with self._lock:
            cur = self._reserved.get(account_id, 0)
            if cur > self.reserve:
                self._reserved[account_id] = cur - self.reserve
            else:
                self._reserved.pop(account_id, None)

    def mark_success(self, account_id, model, usage):
        """Update neuron counter after successful request."""
        with self._lock:
            today = today_utc()
            prompt = usage.get("prompt_tokens", 0) if usage else 0
            completion = usage.get("completion_tokens", 0) if usage else 0
            neurons = estimate_neurons(model, prompt, completion)
            row = self.db.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
            if not row:
                return
            same_day = row[8] == today
            next_neurons = (row[7] if same_day else 0) + neurons
            next_reqs = (row[9] if same_day else 0) + 1
            self.db.execute("""
                UPDATE accounts SET last_used = ?, error_count = 0,
                neurons_today = ?, neurons_day = ?, requests_today = ?
                WHERE id = ?
            """, (time.time(), next_neurons, today, next_reqs, account_id))
            self.db.commit()

    def mark_429(self, account_id, error_code=None):
        """Handle 429 — cooldown or mark daily limit exhausted."""
        with self._lock:
            if error_code == 4006:
                # Daily neuron limit exhausted — pin to cap, skip until 00:00 UTC
                today = today_utc()
                self.db.execute(
                    "UPDATE accounts SET neurons_today = ?, neurons_day = ? WHERE id = ?",
                    (NEURON_FREE_DAILY, today, account_id)
                )
                self.db.commit()
                print(f"  [429] Account #{account_id} daily limit (4006) — skip until 00:00 UTC")
            else:
                # Rate limit — cooldown 90s
                until = time.time() + self.cooldown429
                self.db.execute(
                    "UPDATE accounts SET cooldown_until = ?, error_count = error_count + 1 WHERE id = ?",
                    (until, account_id)
                )
                self.db.commit()
                print(f"  [429] Account #{account_id} rate-limited — cooldown {self.cooldown429}s")

    def stats(self):
        with self._lock:
            today = today_utc()
            total = self.db.execute("SELECT COUNT(*) FROM accounts WHERE is_active = 1").fetchone()[0]
            rows = self.db.execute("SELECT * FROM accounts WHERE is_active = 1").fetchall()
            available = sum(1 for r in rows if r[5] < time.time()
                           and self._effective_neurons(r) < NEURON_FREE_DAILY)
            used = sum(r[7] if r[8] == today else 0 for r in rows)
            reqs = sum(r[9] if r[8] == today else 0 for r in rows)
            capacity = total * NEURON_FREE_DAILY
            return {
                "total": total, "available": available, "cooldown": total - available,
                "neurons_used_today": round(used),
                "neurons_capacity_today": capacity,
                "neurons_remaining_today": max(0, round(capacity - used)),
                "requests_today": reqs,
            }

# ─── CF API Calls ──────────────────────────────────────────────────────────────

def cf_chat_url(account_id):
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"

def cf_run_url(account_id, model):
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}"

def cf_models_url(account_id):
    return f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/models/search"

def call_cf(url, api_key, body, stream=False, timeout=120):
    """Call CF API, return (status, json/text, usage)."""
    data = json.dumps(body).encode() if body else None
    req = Request(url, data=data, headers={
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }, method="POST")
    try:
        with urlopen(req, timeout=timeout) as resp:
            status = resp.status
            if stream:
                # For streaming, return raw response for piping
                return status, resp, None
            raw = resp.read().decode()
            try:
                j = json.loads(raw)
                usage = j.get("usage") or (j.get("result", {}) or {}).get("usage")
                return status, j, usage
            except json.JSONDecodeError:
                return status, raw, None
    except HTTPError as e:
        raw = e.read().decode()
        # Try to extract error code
        error_code = None
        try:
            j = json.loads(raw)
            errors = j.get("errors", [])
            if errors:
                error_code = errors[0].get("code")
        except Exception:
            pass
        return e.code, raw, None, error_code
    except URLError as e:
        return 0, str(e), None, None

# ─── HTTP Server ───────────────────────────────────────────────────────────────

pool = None
api_key_auth = ""

class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # suppress default logging

    def _send_json(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _check_auth(self):
        if not api_key_auth:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {api_key_auth}"

    def do_GET(self):
        if not self._check_auth() and self.path.startswith(("/v1", "/api", "/health", "/ai")):
            self._send_json(401, {"error": "Unauthorized"})
            return

        if self.path == "/health":
            self._send_json(200, pool.stats())
            return

        if self.path == "/api/stats":
            s = pool.stats()
            # Add per-account details
            rows = pool.db.execute("SELECT * FROM accounts WHERE is_active = 1 ORDER BY id").fetchall()
            accounts = []
            for r in rows:
                accounts.append({
                    "id": r[0], "name": r[1], "account_id": r[3][:12] + "...",
                    "neurons_today": round(r[7]) if r[8] == today_utc() else 0,
                    "neurons_remaining": max(0, NEURON_FREE_DAILY - (r[7] if r[8] == today_utc() else 0)),
                    "requests_today": r[9] if r[8] == today_utc() else 0,
                    "cooldown": r[5] > time.time(),
                    "last_used": r[10],
                })
            self._send_json(200, {**s, "accounts": accounts})
            return

        if self.path == "/v1/models":
            # Return static model list
            models = []
            for m in ["@cf/zai-org/glm-5.2", "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b",
                       "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/qwen/qwen2.5-coder-32b-instruct",
                       "@cf/qwen/qwq-32b"]:
                models.append({"id": m, "object": "model", "owned_by": "cloudflare"})
            self._send_json(200, {"object": "list", "data": models})
            return

        # Dashboard (simple HTML)
        if self.path == "/" or self.path == "/dashboard":
            self._send_dashboard()
            return

        self._send_json(404, {"error": "Not found"})

    def do_POST(self):
        if not self._check_auth() and self.path.startswith(("/v1", "/api", "/ai")):
            self._send_json(401, {"error": "Unauthorized"})
            return

        # Read body
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            body = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            self._send_json(400, {"error": "Invalid JSON"})
            return

        if self.path == "/api/import":
            # Import from 9Router
            n = import_from_9router(pool.db)
            self._send_json(200, {"imported": n, "total": pool.stats()["total"]})
            return

        if self.path == "/v1/chat/completions":
            self._handle_chat(body)
            return

        if self.path.startswith("/ai/run/"):
            model = self.path.split("/ai/run/", 1)[1]
            self._handle_run(model, body)
            return

        self._send_json(404, {"error": "Not found"})

    def _handle_chat(self, body):
        model = body.get("model")
        if not model:
            self._send_json(400, {"error": "model required"})
            return
        is_stream = body.get("stream", False)
        max_retries = 5

        for attempt in range(max_retries):
            account = pool.get_available()
            if not account:
                self._send_json(503, {"error": "No available accounts", "pool": pool.stats()})
                return

            url = cf_chat_url(account["account_id"])
            released = False

            try:
                if is_stream:
                    # For streaming, we need to pipe through
                    result = call_cf(url, account["api_key"], body, stream=True, timeout=300)
                    if isinstance(result, tuple) and len(result) == 4:
                        status, text, _, err_code = result
                        if status == 429:
                            pool.mark_429(account["id"], err_code)
                            continue
                        self._send_json(status, {"error": text[:500]})
                        continue
                    # result = (status, response_obj, usage)
                    status, resp, usage = result
                    # Actually stream the response
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    try:
                        while True:
                            chunk = resp.read(4096)
                            if not chunk:
                                break
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    except Exception:
                        pass
                    pool.mark_success(account["id"], model, usage)
                    pool.release(account["id"])
                    released = True
                    return
                else:
                    result = call_cf(url, account["api_key"], body, timeout=120)
                    if len(result) == 4:
                        status, text, _, err_code = result
                        if status == 429:
                            pool.mark_429(account["id"], err_code)
                            continue
                        self._send_json(status, {"error": text[:500] if isinstance(text, str) else text})
                        continue
                    status, j, usage = result
                    pool.mark_success(account["id"], model, usage)
                    pool.release(account["id"])
                    released = True
                    self._send_json(status, j)
                    return

            except Exception as e:
                print(f"  [ERR] Account {account['name']}: {e}")
                continue
            finally:
                if not released:
                    pool.release(account["id"])

        self._send_json(502, {"error": "All retries failed", "pool": pool.stats()})

    def _handle_run(self, model, body):
        max_retries = 5
        for attempt in range(max_retries):
            account = pool.get_available()
            if not account:
                self._send_json(503, {"error": "No available accounts"})
                return
            url = cf_run_url(account["account_id"], model)
            released = False
            try:
                result = call_cf(url, account["api_key"], body, timeout=120)
                if len(result) == 4:
                    status, text, _, err_code = result
                    if status == 429:
                        pool.mark_429(account["id"], err_code)
                        continue
                    self._send_json(status, {"error": text[:500]})
                    continue
                status, j, usage = result
                pool.mark_success(account["id"], model, usage)
                pool.release(account["id"])
                released = True
                self._send_json(status, j)
                return
            except Exception as e:
                print(f"  [ERR] Account {account['name']}: {e}")
                continue
            finally:
                if not released:
                    pool.release(account["id"])
        self._send_json(502, {"error": "All retries failed"})

    def _send_dashboard(self):
        s = pool.stats()
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>CF Proxy Dashboard</title>
<meta http-equiv="refresh" content="5">
<style>
body {{ font-family: monospace; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
h1 {{ color: #00d4ff; }}
.card {{ background: #16213e; padding: 15px; border-radius: 8px; margin: 10px 0; display: inline-block; min-width: 200px; }}
.stat {{ font-size: 24px; font-weight: bold; color: #00ff88; }}
.label {{ color: #888; font-size: 12px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 20px; }}
th, td {{ border: 1px solid #333; padding: 8px; text-align: left; }}
th {{ background: #0f3460; }}
</style></head><body>
<h1>☁️ CF Proxy Dashboard</h1>
<div class="card"><div class="label">Total Accounts</div><div class="stat">{s['total']}</div></div>
<div class="card"><div class="label">Available Now</div><div class="stat">{s['available']}</div></div>
<div class="card"><div class="label">Cooldown</div><div class="stat">{s['cooldown']}</div></div>
<div class="card"><div class="label">Neurons Used Today</div><div class="stat">{s['neurons_used_today']:,}</div></div>
<div class="card"><div class="label">Neurons Remaining</div><div class="stat">{s['neurons_remaining_today']:,}</div></div>
<div class="card"><div class="label">Requests Today</div><div class="stat">{s['requests_today']}</div></div>
<p><a href="/api/stats" style="color:#00d4ff">View JSON stats →</a></p>
</body></html>"""
        body = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    global pool, api_key_auth

    parser = argparse.ArgumentParser(description="CF Workers AI Inference Proxy")
    parser.add_argument("--port", type=int, default=8750, help="Listen port (default: 8750)")
    parser.add_argument("--host", default="0.0.0.0", help="Listen host")
    parser.add_argument("--key", default="", help="API key for auth (empty = no auth)")
    parser.add_argument("--import-9router", action="store_true", help="Import from 9Router DB")
    parser.add_argument("--import-file", action="store_true", help="Import from cf_keys.txt")
    parser.add_argument("--cooldown", type=int, default=90, help="429 cooldown seconds")
    args = parser.parse_args()

    api_key_auth = args.key

    print(r"""
  ╔═══════════════════════════════════════════════╗
  ║  CF Workers AI Proxy — Neuron Tracker         ║
  ║  ☕ https://saweria.co/febfrmn                 ║
  ╚═══════════════════════════════════════════════╝
""")

    # Init DB + pool
    db = init_db(DB_FILE)
    pool = AccountPool(db, cooldown429=args.cooldown)

    # Import accounts
    if args.import_9router:
        import_from_9router(db)
    elif args.import_file:
        import_from_file(db)
    else:
        # Auto-import from cf_keys.txt if DB is empty
        count = pool.stats()["total"]
        if count == 0:
            if KEY_FILE.exists():
                import_from_file(db)
            elif NINEROUTER_DB.exists():
                import_from_9router(db)

    s = pool.stats()
    if s["total"] == 0:
        print("❌ No accounts loaded. Run cf_farmer.py first or use --import-9router")
        sys.exit(1)

    print(f"📋 {s['total']} accounts loaded ({s['available']} available)")
    print(f"🔑 Auth: {'enabled' if api_key_auth else 'disabled'}")
    print(f"🌐 Listening on http://{args.host}:{args.port}")
    print(f"   Dashboard: http://127.0.0.1:{args.port}/")
    print(f"   Chat API:  http://127.0.0.1:{args.port}/v1/chat/completions")
    print(f"   Models:    http://127.0.0.1:{args.port}/v1/models")
    print()

    server = HTTPServer((args.host, args.port), ProxyHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\nStopping...")
        server.shutdown()

if __name__ == "__main__":
    main()
