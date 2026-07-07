#!/usr/bin/env python3
"""
cf_farmer.py — Cloudflare Workers AI Token Harvester (Pure API, No Browser)

Flow (5-8 seconds per account):
  1. Read email|password from akun.txt
  2. POST /api/v4/login → session cookie
  3. GET /api/v4/accounts → account_id
  4. GET /api/v4/user/tokens/permission_groups → Workers AI perm IDs
  5. POST /api/v4/user/tokens → cfut_ token
  6. Save to cf_keys.txt

No browser! No email verification wait! Pure HTTP API.
Anti-detection: random UA, session cookies, optional proxy.
Supports: Gmail, GSuite, custom domain emails (must be verified beforehand).

Usage:
  python3 cf_farmer.py                           # harvest all from akun.txt
  python3 cf_farmer.py --proxy http://ip:port    # global proxy
  python3 cf_farmer.py --only user@email.com    # single account
  python3 cf_farmer.py --delay 3               # delay between accounts (default 3s)
  python3 cf_farmer.py --clean                  # reset cf_keys.txt

akun.txt format:
  email|password
  email|password|http://proxy:port   (optional per-account proxy)
"""

import os, sys, time, json, random, string, argparse
from pathlib import Path

try:
    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    print("Install: pip install requests"); sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
AKUN_FILE   = SCRIPT_DIR / "akun.txt"
RESULT_FILE = SCRIPT_DIR / "cf_keys.txt"

API_BASE = "https://dash.cloudflare.com/api/v4"

MODELS = '["@cf/zai-org/glm-5.2","@cf/deepseek-ai/deepseek-r1-distill-qwen-32b","@cf/meta/llama-3.3-70b-instruct-fp8-fast","@cf/qwen/qwen2.5-coder-32b-instruct","@cf/qwen/qwq-32b"]'

BANNER = r"""
  ╔═══════════════════════════════════════════════╗
  ║  CF Workers AI Farmer — Pure API (No Browser) ║
  ║  ☕ https://saweria.co/febfrmn                  ║
  ╚═══════════════════════════════════════════════╝
"""

# ─── Anti-Detection ────────────────────────────────────────────────────────────

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

def random_ua():
    return random.choice(USER_AGENTS)

def make_session(proxy=None):
    """Create a requests session with anti-detection headers."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": random_ua(),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "sec-ch-ua": '"Chromium";v="124", "Not(A:Brand";v="24", "Google Chrome";v="124"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "Origin": "https://dash.cloudflare.com",
        "Referer": "https://dash.cloudflare.com/",
    })
    retry = Retry(total=2, backoff_factor=0.5, status_forcelist=[429, 500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry, pool_connections=1, pool_maxsize=1)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
        s.verify = False
    return s

# ─── Account Management ────────────────────────────────────────────────────────

def read_accounts():
    if not AKUN_FILE.exists():
        return []
    accounts = []
    for line in AKUN_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) >= 2:
            acc = {"email": parts[0].strip(), "password": parts[1].strip()}
            if len(parts) >= 3 and parts[2].strip():
                acc["proxy"] = parts[2].strip()
            accounts.append(acc)
    return accounts

def get_harvested():
    if not RESULT_FILE.exists():
        return set()
    ids = set()
    for line in RESULT_FILE.read_text().splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and "/accounts/" in parts[1]:
            ids.add(parts[1].split("/accounts/")[1].split("/")[0])
    return ids

# ─── CF API (Pure HTTP, Fast) ──────────────────────────────────────────────────

def cf_login(session, email, password):
    """Login to CF. Returns True if session cookie obtained."""
    try:
        resp = session.post(
            f"{API_BASE}/login",
            json={"email": email, "password": password},
            timeout=15,
        )
        data = resp.json()
        if data.get("success"):
            return True
        errors = data.get("errors", [])
        for err in errors:
            print(f"    {err.get('message', '')}")
        return False
    except Exception as e:
        print(f"    Login error: {e}")
        return False

def get_account_id(session):
    """Get CF account ID."""
    try:
        resp = session.get(f"{API_BASE}/accounts", timeout=15)
        data = resp.json()
        if data.get("success"):
            accounts = data.get("result", [])
            if accounts:
                return accounts[0].get("id", "")
        return None
    except Exception:
        return None

def get_permission_groups(session):
    """Get Workers AI permission group IDs."""
    try:
        resp = session.get(f"{API_BASE}/user/tokens/permission_groups", timeout=15)
        data = resp.json()
        if data.get("success"):
            groups = data.get("result", [])
            return [
                {"id": g["id"], "name": g.get("name", "")}
                for g in groups
                if "workers ai" in g.get("name", "").lower()
            ]
        return []
    except Exception:
        return []

def create_token(session, account_id, perm_ids):
    """Create a Workers AI API token."""
    payload = {
        "name": f"cf-ai-{int(time.time())}",
        "policies": [{
            "effect": "allow",
            "permission_groups": [{"id": p["id"]} for p in perm_ids],
            "resources": {f"com.cloudflare.api.account.{account_id}": "*"}
        }]
    }
    try:
        resp = session.post(f"{API_BASE}/user/tokens", json=payload, timeout=15)
        data = resp.json()
        if data.get("success"):
            return data.get("result", {}).get("value", "")
        return None
    except Exception:
        return None

def save_token(account_id, token):
    base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
    with open(RESULT_FILE, "a") as f:
        f.write(f"cloudflare_{account_id[:6]}|{base_url}|{token}|{MODELS}\n")

# ─── Main Harvest (Fast: 5-8s per account) ─────────────────────────────────────

def harvest_one(account, index, total, global_proxy=None):
    """Harvest one CF account: login → account_id → perms → token. ~5-8s."""
    email = account["email"]
    password = account["password"]
    proxy = account.get("proxy") or global_proxy

    print(f"\n{'='*55}")
    print(f" [{index}/{total}] {email}")
    print(f"{'='*55}")

    t_start = time.time()
    harvested = get_harvested()
    session = make_session(proxy)

    try:
        # Step 1: Login (1-2s)
        print(" [1] Login...")
        if not cf_login(session, email, password):
            raise Exception("Login failed")
        time.sleep(0.3)

        # Step 2: Account ID (1s)
        print(" [2] Account ID...")
        account_id = get_account_id(session)
        if not account_id:
            raise Exception("No account ID")
        if account_id in harvested:
            print(" [SKIP] Already harvested")
            return True
        print(f"    ID: {account_id}")
        time.sleep(0.2)

        # Step 3: Permission groups (1s)
        print(" [3] Permissions...")
        perm_ids = get_permission_groups(session)
        if not perm_ids:
            raise Exception("No Workers AI permissions")
        print(f"    {len(perm_ids)} groups")
        time.sleep(0.2)

        # Step 4: Create token (1-2s)
        print(" [4] Token...")
        token = create_token(session, account_id, perm_ids)
        if not token:
            raise Exception("Token creation failed")
        print(f"    {token[:25]}...")

        # Step 5: Save
        save_token(account_id, token)
        elapsed = time.time() - t_start
        print(f" [DONE] {elapsed:.1f}s")
        return True

    except Exception as e:
        print(f" [ERROR] {e}")
        return False
    finally:
        session.close()

# ─── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="CF Workers AI Token Harvester (Pure API)")
    parser.add_argument("--proxy", help="Global proxy (http://user:pass@ip:port)")
    parser.add_argument("--only", help="Harvest single account (email)")
    parser.add_argument("--delay", type=int, default=3, help="Delay between accounts (seconds, default 3)")
    parser.add_argument("--clean", action="store_true", help="Delete cf_keys.txt and exit")
    args = parser.parse_args()

    print(BANNER)

    if args.clean:
        if RESULT_FILE.exists():
            RESULT_FILE.unlink()
            print(f"Deleted {RESULT_FILE}")
        else:
            print(f"{RESULT_FILE} not found")
        sys.exit(0)

    accounts = read_accounts()
    if args.only:
        accounts = [a for a in accounts if a["email"] == args.only]
    if not accounts:
        print("Usage:")
        print("  python3 cf_farmer.py                     # harvest from akun.txt")
        print("  python3 cf_farmer.py --only user@x.com   # single account")
        print("  python3 cf_farmer.py --proxy http://...   # with proxy")
        print("  python3 cf_farmer.py --clean              # reset cf_keys.txt")
        print("\nakun.txt format:")
        print("  email|password")
        print("  email|password|http://proxy:port  (optional proxy)")
        sys.exit(1)

    existing = get_harvested()
    if existing:
        print(f"cf_keys.txt has {len(existing)} keys. Duplicates auto-skipped.\n")

    print(f"Mode: Pure API (No Browser) — {len(accounts)} accounts")
    print(f"Proxy: {args.proxy or 'per-account or none'}")
    print(f"Speed: ~5-8s per account")
    print()

    success = 0
    t_total = time.time()
    for i, acc in enumerate(accounts, 1):
        ok = harvest_one(acc, i, len(accounts), global_proxy=args.proxy)
        if ok:
            success += 1
        if i < len(accounts):
            delay = random.randint(args.delay, args.delay + 3)
            time.sleep(delay)

    elapsed = time.time() - t_total
    print(f"\n{'='*55}")
    print(f" DONE: {success}/{len(accounts)} in {elapsed:.0f}s")
    print(f"{'='*55}")
    print(f"\nNext: python3 cf_proxy.py    (start inference proxy)")
    print(f"     python3 test_demo.py     (run audit tests)")


if __name__ == "__main__":
    main()
