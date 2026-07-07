#!/usr/bin/env python3
"""
cf_farmer.py — Cloudflare Workers AI Token Harvester (Anti-Ban Edition)

Features:
  - Google OAuth login (Gmail + GSuite custom domains)
  - Anti-ban: randomized fingerprint, human-like delays, trace removal
  - Proxy support (per-account or global)
  - Auto-dedup (skip already-harvested accounts)
  - Single file, zero external config

Usage:
  python3 cf_farmer.py                           # harvest all accounts
  python3 cf_farmer.py --proxy http://ip:port    # global proxy
  python3 cf_farmer.py --only user@email.com    # single account
  python3 cf_farmer.py --clean                   # reset cf_keys.txt
  python3 cf_farmer.py --delay 30                # custom delay (seconds)

akun.txt format:
  email|password
  email|password|proxy_url        (per-account proxy, optional)
"""

import os, sys, time, random, json, hashlib, shutil, argparse, re
from pathlib import Path

try:
    from DrissionPage import ChromiumPage, ChromiumOptions
except ImportError:
    print("Install: pip install DrissionPage"); sys.exit(1)

SCRIPT_DIR = Path(__file__).resolve().parent
AKUN_FILE  = SCRIPT_DIR / "akun.txt"
RESULT_FILE = SCRIPT_DIR / "cf_keys.txt"
PROFILE_ROOT = SCRIPT_DIR / ".chrome_profiles"

MODELS = '["@cf/zai-org/glm-5.2","@cf/deepseek-ai/deepseek-r1-distill-qwen-32b","@cf/meta/llama-3.3-70b-instruct-fp8-fast","@cf/qwen/qwen2.5-coder-32b-instruct","@cf/qwen/qwq-32b"]'

# ─── Anti-Ban Utilities ────────────────────────────────────────────────────────

# Realistic user agents (rotated per account)
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Window sizes for fingerprint randomization
WINDOW_SIZES = ["1280,720", "1366,768", "1440,900", "1536,864", "1920,1080"]

def random_fingerprint():
    """Generate randomized browser fingerprint per account."""
    return {
        "user_agent": random.choice(USER_AGENTS),
        "window_size": random.choice(WINDOW_SIZES),
        "timezone": random.choice(["America/New_York", "Europe/London", "Asia/Tokyo", "Australia/Sydney"]),
    }

def human_type(ele, text):
    """Type like a human with variable delays."""
    ele.clear()
    for char in text:
        ele.input(char)
        time.sleep(random.uniform(0.03, 0.15))

def human_click(page, ele):
    """Click like a human with mouse movement."""
    try:
        page.actions.move_to(ele)
        time.sleep(random.uniform(0.2, 0.6))
    except Exception:
        pass
    ele.click()

def human_delay(min_s=1.0, max_s=3.0):
    """Random delay to mimic human behavior."""
    time.sleep(random.uniform(min_s, max_s))

def wipe_profile_traces(profile_dir):
    """Remove fingerprint traces from browser profile after harvest."""
    if not profile_dir.exists():
        return
    # Delete cache, cookies, history, and other tracking artifacts
    trace_paths = [
        profile_dir / "Default" / "Cache",
        profile_dir / "Default" / "Code Cache",
        profile_dir / "Default" / "Cookies",
        profile_dir / "Default" / "History",
        profile_dir / "Default" / "Login Data",
        profile_dir / "Default" / "Web Data",
        profile_dir / "Default" / "Network" / "Cookies",
        profile_dir / "Default" / "Sessions",
        profile_dir / "Default" / "Local Storage",
        profile_dir / "First Run",
        profile_dir / "Local State",
    ]
    for p in trace_paths:
        try:
            if p.is_file():
                p.unlink()
            elif p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
        except Exception:
            pass

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
    """Get set of already-harvested account IDs from cf_keys.txt."""
    if not RESULT_FILE.exists():
        return set()
    ids = set()
    for line in RESULT_FILE.read_text().splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and "/accounts/" in parts[1]:
            ids.add(parts[1].split("/accounts/")[1].split("/")[0])
    return ids

# ─── CF API (via browser session) ──────────────────────────────────────────────

def get_wa_permission_ids(page):
    """Fetch Workers AI permission group IDs from CF API."""
    result = page.run_js("""
        return (async () => {
            const resp = await fetch('/api/v4/user/tokens/permission_groups');
            return await resp.json();
        })();
    """)
    if not result or not result.get('success'):
        return []
    # Return only id — name is for logging, not sent to API
    return [{"id": g["id"], "name": g.get("name", "")}
            for g in result.get('result', [])
            if 'workers ai' in g.get('name', '').lower()]

def create_token_via_api(page, account_id, perm_ids):
    """Create API token via CF internal API. Only sends id field (anti-ban)."""
    # CF API expects [{"id": "..."}] — extra fields like name can cause 400
    clean_perms = [{"id": p["id"]} for p in perm_ids]
    payload = json.dumps({
        "name": f"cf-ai-{int(time.time())}",
        "policies": [{
            "effect": "allow",
            "permission_groups": clean_perms,
            "resources": {f"com.cloudflare.api.account.{account_id}": "*"}
        }]
    })
    result = page.run_js("""
        return (async () => {
            const payload = JSON.parse(%s);
            const resp = await fetch('/api/v4/user/tokens', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(payload)
            });
            return await resp.json();
        })();
    """ % json.dumps(payload))
    if result and result.get('success') and result.get('result', {}).get('value'):
        return result['result']['value']
    return None

# ─── Harvest Flow ──────────────────────────────────────────────────────────────

def harvest_one(account, index, total, global_proxy=None):
    """Harvest a single CF account. Returns True on success."""
    email = account["email"]
    password = account["password"]
    proxy = account.get("proxy") or global_proxy

    print(f"\n{'='*55}")
    print(f" [{index}/{total}] {email}")
    if proxy:
        print(f" Proxy: {proxy[:40]}...")
    print(f"{'='*55}")

    # Anti-ban: randomize fingerprint per account
    fp = random_fingerprint()

    # Profile per account — fresh each time
    profile_hash = hashlib.md5(f"{email}_{index}".encode()).hexdigest()[:8]
    profile_dir = PROFILE_ROOT / f"acc_{profile_hash}"
    profile_dir.mkdir(parents=True, exist_ok=True)

    co = ChromiumOptions()
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument(f"--window-size={fp['window_size']}")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument(f"--user-data-dir={profile_dir}")
    co.set_argument(f"--user-agent={fp['user_agent']}")
    # Anti-detection: hide automation signals
    co.set_argument("--disable-extensions")
    co.set_argument("--disable-popup-blocking")
    co.set_argument("--disable-default-apps")
    co.set_argument("--disable-infobars")
    co.set_argument("--disable-notifications")
    if proxy:
        co.set_argument(f"--proxy-server={proxy}")
    co.set_local_port(9200 + index)

    main_page = page = ChromiumPage(co)

    try:
        # ── [0] Clear any previous session ──
        print(" [0] Clear session...")
        try:
            page.get("https://dash.cloudflare.com/logout")
            human_delay(2.0, 4.0)
        except Exception:
            pass
        try:
            page.set.cookies.clear()
        except Exception:
            pass
        try:
            page.get("https://accounts.google.com/Logout")
            human_delay(2.0, 4.0)
            page.set.cookies.clear()
        except Exception:
            pass

        # ── [1] Open CF login ──
        print(" [1] Open Cloudflare login...")
        page.get("https://dash.cloudflare.com/login")
        human_delay(4.0, 7.0)

        # Check if already logged in (shouldn't happen after clear)
        if "dash.cloudflare.com/" in page.url and "/login" not in page.url:
            print("    Session still active — clearing again")
            try:
                page.set.cookies.clear()
            except Exception:
                pass
            page.get("https://dash.cloudflare.com/login")
            human_delay(3.0, 5.0)

        if "dash.cloudflare.com/" in page.url and "/login" not in page.url:
            print("    Already logged in")
        else:
            # ── [2] Click Continue with Google ──
            print(" [2] Google OAuth...")
            google_btn = page.ele('@text():Google', timeout=10)
            if not google_btn:
                raise Exception("Google button not found")

            tabs_before = set(page.tab_ids)
            human_click(page, google_btn)
            human_delay(3.0, 5.0)

            # Find Google OAuth tab
            new_tab_id = None
            for _ in range(6):
                time.sleep(1)
                new_tabs = set(page.tab_ids) - tabs_before
                if new_tabs:
                    new_tab_id = new_tabs.pop()
                    break
            if not new_tab_id:
                for tid in page.tab_ids:
                    try:
                        t = page.get_tab(tid)
                        if "accounts.google.com" in (t.url or ""):
                            new_tab_id = tid
                            break
                    except Exception:
                        continue
            if not new_tab_id:
                raise Exception("Google OAuth tab not found")

            tab = page.get_tab(new_tab_id)
            print(f" [3] Google tab: {tab.url[:60]}...")
            human_delay(2.0, 4.0)

            # Account Chooser
            if "accountchooser" in (tab.url or ""):
                print("    Account Chooser → another account")
                btn = tab.ele("@text():Use another account", timeout=3)
                if btn:
                    human_click(tab, btn)
                    human_delay(2.0, 4.0)

            # Input email
            print(f" [4] Login: {email}")
            tab.wait.ele_displayed("#identifierId", timeout=15)
            human_type(tab.ele('#identifierId'), email)
            human_delay(0.5, 1.0)
            human_click(tab, tab.ele('#identifierNext'))
            human_delay(4.0, 6.0)

            # Input password
            pw_ele = tab.ele('@type=password', timeout=10)
            if not pw_ele:
                raise Exception("Password field not found")
            human_type(pw_ele, password)
            human_delay(0.5, 1.0)
            human_click(tab, tab.ele('#passwordNext'))
            human_delay(6.0, 9.0)

            # Workspace TOS (GSuite accounts)
            cur = tab.url or ""
            if "workspacetermsofservice" in cur or "speedbump" in cur:
                print("    Handle Workspace TOS...")
                btn = tab.ele('tag:button@@text():I understand', timeout=5)
                if btn:
                    human_click(tab, btn)
                    human_delay(4.0, 7.0)

            # Google consent screen
            for _ in range(5):
                time.sleep(2)
                cur = tab.url or ""
                if "accounts.google.com" not in cur:
                    break
                for sel in ['@text():Allow', '@text():Continue', '@text():Accept']:
                    btn = tab.ele(sel, timeout=1)
                    if btn:
                        human_click(tab, btn)
                        time.sleep(2)
                        break

            # Wait for redirect to CF dashboard
            print(" [5] Waiting for CF dashboard redirect...")
            human_delay(5.0, 8.0)

        # ── [6] Extract Account ID ──
        account_id = None
        for tid in page.tab_ids:
            try:
                t = page.get_tab(tid)
                url = t.url or ""
                if "dash.cloudflare.com/" in url:
                    parts = url.split("dash.cloudflare.com/")
                    if len(parts) > 1:
                        aid = parts[1].split("/")[0].split("?")[0]
                        if len(aid) == 32 and all(c in '0123456789abcdef' for c in aid):
                            account_id = aid
                            page = t
                            break
            except Exception:
                continue

        if not account_id:
            page.get("https://dash.cloudflare.com/")
            human_delay(4.0, 6.0)
            parts = page.url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                aid = parts[1].split("/")[0].split("?")[0]
                if len(aid) == 32 and all(c in '0123456789abcdef' for c in aid):
                    account_id = aid

        if not account_id:
            raise Exception(f"Failed to get Account ID. URL: {page.url}")

        print(f" [6] Account ID: {account_id}")

        # Dedup check
        harvested = get_harvested()
        if account_id in harvested:
            print(f" [SKIP] Already harvested")
            return True

        # ── [7] Create API Token ──
        print(" [7] Fetch Workers AI permissions...")
        perm_ids = get_wa_permission_ids(page)
        if not perm_ids:
            raise Exception("Failed to get permission groups")
        print(f"    {len(perm_ids)} permissions: {[p['name'] for p in perm_ids]}")

        print(" [8] Create API token...")
        token = create_token_via_api(page, account_id, perm_ids)

        if token:
            base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
            with open(RESULT_FILE, "a") as f:
                f.write(f"cloudflare_{account_id[:6]}|{base_url}|{token}|{MODELS}\n")
            print(f" [SUCCESS] Token: {token[:25]}...")
            print(f" [SAVED] → {RESULT_FILE}")
            return True
        else:
            print(" [FAILED] Token creation failed")
            return False

    except Exception as e:
        print(f" [ERROR] {email}: {e}")
        return False
    finally:
        # ── [9] Anti-ban: wipe all traces ──
        try:
            main_page.quit()
        except Exception:
            pass
        # Wipe profile traces (cookies, cache, history)
        wipe_profile_traces(profile_dir)
        # Random delay between accounts (anti-pattern detection)
        human_delay(1.0, 2.0)


# ─── Main ──────────────────────────────────────────────────────────────────────

BANNER = r"""
  ╔═══════════════════════════════════════════════╗
  ║  Cloudflare Workers AI Farmer — Anti-Ban     ║
  ║  ☕ https://saweria.co/febfrmn                ║
  ╚═══════════════════════════════════════════════╝
"""

def main():
    parser = argparse.ArgumentParser(description="CF Workers AI Token Harvester")
    parser.add_argument("--proxy", help="Global proxy (http://user:pass@ip:port)")
    parser.add_argument("--only", help="Harvest single account (email)")
    parser.add_argument("--delay", type=int, default=15, help="Delay between accounts (seconds)")
    parser.add_argument("--clean", action="store_true", help="Delete cf_keys.txt and exit")
    parser.add_argument("--keep-profiles", action="store_true", help="Don't wipe profiles (debug only)")
    args = parser.parse_args()

    print(BANNER)

    if args.clean:
        if RESULT_FILE.exists():
            RESULT_FILE.unlink()
            print(f"✅ {RESULT_FILE} deleted.")
        else:
            print(f"ℹ️ {RESULT_FILE} not found.")
        sys.exit(0)

    accounts = read_accounts()
    if args.only:
        accounts = [a for a in accounts if a["email"] == args.only]
    if not accounts:
        print("Isi akun.txt (email|password atau email|password|proxy)")
        sys.exit(1)

    # Show existing tokens
    existing = get_harvested()
    if existing:
        print(f"⚠️  cf_keys.txt has {len(existing)} keys. Duplicates auto-skipped.")
        print(f"   Fresh run? python3 cf_farmer.py --clean\n")

    print(f"Total accounts: {len(accounts)}")
    print(f"Proxy: {args.proxy or 'per-account or none'}")
    print(f"Anti-ban: fingerprint randomization + trace removal + human delays")
    print()

    success = 0
    for i, acc in enumerate(accounts, 1):
        ok = harvest_one(acc, i, len(accounts), global_proxy=args.proxy)
        if ok:
            success += 1
        if i < len(accounts):
            delay = random.randint(args.delay, args.delay + 10)
            print(f"\nWaiting {delay}s...")
            time.sleep(delay)

    print(f"\n{'='*55}")
    print(f" DONE: {success}/{len(accounts)} succeeded")
    print(f"{'='*55}")
    print(f"\nNext: python3 cf_proxy.py    (start inference proxy)")
    print(f"     python3 test_demo.py     (run audit tests)")


if __name__ == "__main__":
    main()
