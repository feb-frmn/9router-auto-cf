#!/usr/bin/env python3
"""
cf_farmer.py — Cloudflare Workers AI Token Harvester

Flow:
  1. Read email|password from akun.txt
  2. Sign up Cloudflare with that email + password
  3. Wait for email verification (user verifies or bot detects redirect)
  4. Create Workers AI API token
  5. Save to cf_keys.txt

Also supports:
  - Google OAuth (Gmail/GSuite) — auto-detect
  - Existing CF accounts (email+password login)
  - Per-account or global proxy
  - Anti-ban: fingerprint randomization, trace removal, human delays

Usage:
  python3 cf_farmer.py                           # signup all accounts in akun.txt
  python3 cf_farmer.py --proxy http://ip:port    # global proxy
  python3 cf_farmer.py --only user@email.com    # single account
  python3 cf_farmer.py --delay 30               # custom delay
  python3 cf_farmer.py --clean                   # reset cf_keys.txt

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

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

WINDOW_SIZES = ["1280,720", "1366,768", "1440,900", "1536,864", "1920,1080"]

def random_fingerprint():
    return {
        "user_agent": random.choice(USER_AGENTS),
        "window_size": random.choice(WINDOW_SIZES),
        "timezone": random.choice(["America/New_York", "Europe/London", "Asia/Tokyo", "Australia/Sydney"]),
    }

def human_type(ele, text):
    ele.clear()
    for char in text:
        ele.input(char)
        time.sleep(random.uniform(0.03, 0.15))

def human_click(page, ele):
    try:
        page.actions.move_to(ele)
        time.sleep(random.uniform(0.2, 0.6))
    except Exception:
        pass
    ele.click()

def human_delay(min_s=1.0, max_s=3.0):
    time.sleep(random.uniform(min_s, max_s))

def wipe_profile_traces(profile_dir):
    if not profile_dir.exists():
        return
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
    result = page.run_js("""
        return (async () => {
            const resp = await fetch('/api/v4/user/tokens/permission_groups');
            return await resp.json();
        })();
    """)
    if not result or not result.get('success'):
        return []
    return [{"id": g["id"], "name": g.get("name", "")}
            for g in result.get('result', [])
            if 'workers ai' in g.get('name', '').lower()]

def create_token_via_api(page, account_id, perm_ids):
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

# ─── Browser Setup ─────────────────────────────────────────────────────────────

def setup_browser(index, fp, proxy=None):
    profile_hash = hashlib.md5(f"{index}_{time.time()}".encode()).hexdigest()[:8]
    profile_dir = PROFILE_ROOT / f"acc_{profile_hash}"
    profile_dir.mkdir(parents=True, exist_ok=True)

    co = ChromiumOptions()
    co.set_argument("--headless=new")
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument(f"--window-size={fp['window_size']}")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument(f"--user-data-dir={profile_dir}")
    co.set_argument(f"--user-agent={fp['user_agent']}")
    co.set_argument("--disable-extensions")
    co.set_argument("--disable-popup-blocking")
    co.set_argument("--disable-default-apps")
    co.set_argument("--disable-infobars")
    co.set_argument("--disable-notifications")
    if proxy:
        co.set_argument(f"--proxy-server={proxy}")
    co.set_local_port(9200 + index)

    return ChromiumPage(co), profile_dir

# ─── Extract Account ID ────────────────────────────────────────────────────────

def extract_account_id(page):
    for tid in page.tab_ids:
        try:
            t = page.get_tab(tid)
            url = t.url or ""
            if "dash.cloudflare.com/" in url:
                parts = url.split("dash.cloudflare.com/")
                if len(parts) > 1:
                    aid = parts[1].split("/")[0].split("?")[0]
                    if len(aid) == 32 and all(c in '0123456789abcdef' for c in aid):
                        return aid, t
        except Exception:
            continue
    page.get("https://dash.cloudflare.com/")
    human_delay(4.0, 6.0)
    parts = (page.url or "").split("dash.cloudflare.com/")
    if len(parts) > 1:
        aid = parts[1].split("/")[0].split("?")[0]
        if len(aid) == 32 and all(c in '0123456789abcdef' for c in aid):
            return aid, page
    return None, page

# ─── CF Signup ─────────────────────────────────────────────────────────────────

def cf_signup(page, email, password):
    """Sign up a new CF account. Returns True if signup form submitted."""
    print(f" [2] CF Signup: {email}")
    page.get("https://dash.cloudflare.com/sign-up")
    human_delay(4.0, 7.0)

    email_field = None
    for _ in range(3):
        email_field = (page.ele('tag:input@type=email', timeout=3)
                       or page.ele('#email', timeout=1)
                       or page.ele('tag:input@name=email', timeout=1))
        if email_field:
            break
        time.sleep(1)

    if not email_field:
        raise Exception("Signup: email field not found")

    human_type(email_field, email)
    human_delay(0.3, 0.8)

    pw_field = (page.ele('tag:input@type=password', timeout=3)
                or page.ele('#password', timeout=2))
    if not pw_field:
        raise Exception("Signup: password field not found")

    human_type(pw_field, password)
    human_delay(0.5, 1.0)

    signup_btn = (page.ele('@type=submit', timeout=3)
                  or page.ele('@text():Sign Up', timeout=2)
                  or page.ele('@text():Create Account', timeout=2)
                  or page.ele('tag:button@@type=submit', timeout=2))
    if signup_btn:
        human_click(page, signup_btn)
    else:
        try:
            page.run_js("document.querySelector('form').submit();")
        except Exception:
            pass

    print(" [3] Signup form submitted...")
    human_delay(5.0, 8.0)
    return True

# ─── CF Login (email+password) ────────────────────────────────────────────────

def cf_login_email(page, email, password):
    print(f" [2] CF login: {email}")
    page.get("https://dash.cloudflare.com/login")
    human_delay(4.0, 7.0)

    email_field = None
    for _ in range(3):
        email_field = (page.ele('tag:input@type=email', timeout=3) or page.ele('#email', timeout=1))
        if email_field:
            break
        time.sleep(1)

    if not email_field:
        return False

    human_type(email_field, email)
    human_delay(0.3, 0.8)

    pw_field = page.ele('tag:input@type=password', timeout=3) or page.ele('#password', timeout=2)
    if not pw_field:
        return False

    human_type(pw_field, password)
    human_delay(0.5, 1.0)

    login_btn = (page.ele('@type=submit', timeout=3)
                 or page.ele('@text():Log In', timeout=2)
                 or page.ele('tag:button@@type=submit', timeout=2))
    if login_btn:
        human_click(page, login_btn)
    else:
        try:
            page.run_js("document.querySelector('form').submit();")
        except Exception:
            pass

    human_delay(6.0, 9.0)
    return "/login" not in (page.url or "")

# ─── Google OAuth ──────────────────────────────────────────────────────────────

def cf_login_google(page, email, password):
    print(" [2] Google OAuth...")
    google_btn = page.ele('@text():Google', timeout=10)
    if not google_btn:
        return False

    tabs_before = set(page.tab_ids)
    human_click(page, google_btn)
    human_delay(3.0, 5.0)

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
        return False

    tab = page.get_tab(new_tab_id)
    human_delay(2.0, 4.0)

    if "accountchooser" in (tab.url or ""):
        btn = tab.ele("@text():Use another account", timeout=3)
        if btn:
            human_click(tab, btn)
            human_delay(2.0, 4.0)

    print(f" [3] Google login: {email}")
    tab.wait.ele_displayed("#identifierId", timeout=15)
    human_type(tab.ele('#identifierId'), email)
    human_delay(0.5, 1.0)
    human_click(tab, tab.ele('#identifierNext'))
    human_delay(4.0, 6.0)

    pw_ele = tab.ele('@type=password', timeout=10)
    if not pw_ele:
        return False
    human_type(pw_ele, password)
    human_delay(0.5, 1.0)
    human_click(tab, tab.ele('#passwordNext'))
    human_delay(6.0, 9.0)

    cur = tab.url or ""
    if "workspacetermsofservice" in cur or "speedbump" in cur:
        btn = tab.ele('tag:button@@text():I understand', timeout=5)
        if btn:
            human_click(tab, btn)
            human_delay(4.0, 7.0)

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

    human_delay(5.0, 8.0)
    return True

# ─── Harvest Token ─────────────────────────────────────────────────────────────

def harvest_token(page, account_id):
    print(f" [5] Account ID: {account_id}")

    harvested = get_harvested()
    if account_id in harvested:
        print(f" [SKIP] Already harvested")
        return "SKIP"

    print(" [6] Fetch Workers AI permissions...")
    perm_ids = get_wa_permission_ids(page)
    if not perm_ids:
        raise Exception("Failed to get permission groups")
    print(f"    {len(perm_ids)} permissions: {[p['name'] for p in perm_ids]}")

    print(" [7] Create API token...")
    token = create_token_via_api(page, account_id, perm_ids)
    return token

def save_token(account_id, token):
    base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
    with open(RESULT_FILE, "a") as f:
        f.write(f"cloudflare_{account_id[:6]}|{base_url}|{token}|{MODELS}\n")
    print(f" [SUCCESS] Token: {token[:25]}...")
    print(f" [SAVED] → {RESULT_FILE}")

# ─── Main Harvest ──────────────────────────────────────────────────────────────

def harvest_one(account, index, total, global_proxy=None):
    """Harvest one CF account: try signup → login → Google OAuth."""
    email = account["email"]
    password = account["password"]
    proxy = account.get("proxy") or global_proxy

    print(f"\n{'='*55}")
    print(f" [{index}/{total}] {email}")
    if proxy:
        print(f" Proxy: {proxy[:40]}...")
    print(f"{'='*55}")

    fp = random_fingerprint()
    page, profile_dir = setup_browser(index, fp, proxy)
    main_page = page

    try:
        # Clear session
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

        # Step 1: Try CF signup first (new accounts)
        print(" [1] CF signup...")
        signup_ok = cf_signup(page, email, password)
        human_delay(3.0, 5.0)

        cur_url = page.url or ""

        # Check if we're on dashboard (signup succeeded, no verification needed)
        if "dash.cloudflare.com/" in cur_url and "/sign-up" not in cur_url and "/login" not in cur_url:
            print("    Signup success — no verification needed!")
        # Check if CF requires email verification
        elif "verify" in cur_url.lower() or "/sign-up" in cur_url:
            print(" [4] CF requires email verification.")
            print("    Please verify your email, then re-run this tool.")
            print("    (Bot will auto-detect if already verified)")
            # Wait up to 180s for user to verify email
            print("    Waiting up to 180s for verification...")
            for wait_round in range(36):
                time.sleep(5)
                # Try navigating to dashboard
                page.get("https://dash.cloudflare.com/")
                human_delay(2.0, 3.0)
                cur_url = page.url or ""
                if "dash.cloudflare.com/" in cur_url and "/login" not in cur_url and "/sign-up" not in cur_url:
                    # Check if we actually have an account ID
                    parts = cur_url.split("dash.cloudflare.com/")
                    if len(parts) > 1:
                        aid = parts[1].split("/")[0].split("?")[0]
                        if len(aid) == 32 and all(c in '0123456789abcdef' for c in aid):
                            print("    Email verified! Proceeding...")
                            break
                # Still waiting
                if wait_round % 6 == 5:
                    print(f"    Still waiting... ({(wait_round+1)*5}s)")

            # Check final state
            cur_url = page.url or ""
            if "/login" in cur_url or "/sign-up" in cur_url:
                # Signup didn't work — try login (maybe account already exists)
                print(" [4b] Trying CF login (account may already exist)...")
                logged_in = cf_login_email(page, email, password)
                if not logged_in:
                    # Try Google OAuth
                    print(" [4c] Trying Google OAuth...")
                    page.get("https://dash.cloudflare.com/login")
                    human_delay(3.0, 5.0)
                    logged_in = cf_login_google(page, email, password)
                if not logged_in:
                    raise Exception("Signup and login both failed. Verify email and re-run.")
                human_delay(5.0, 8.0)
        else:
            # Unknown state — try login
            print(" [4b] Trying CF login...")
            logged_in = cf_login_email(page, email, password)
            if not logged_in:
                print(" [4c] Trying Google OAuth...")
                page.get("https://dash.cloudflare.com/login")
                human_delay(3.0, 5.0)
                logged_in = cf_login_google(page, email, password)
            if not logged_in:
                raise Exception("All login methods failed")
            human_delay(5.0, 8.0)

        # Extract account ID
        account_id, page = extract_account_id(page)
        if not account_id:
            raise Exception(f"Failed to get Account ID. URL: {page.url}")

        # Harvest token
        token = harvest_token(page, account_id)
        if token and token != "SKIP":
            save_token(account_id, token)
            return True
        elif token == "SKIP":
            return True
        else:
            print(" [FAILED] Token creation failed")
            return False

    except Exception as e:
        print(f" [ERROR] {email}: {e}")
        return False
    finally:
        try:
            main_page.quit()
        except Exception:
            pass
        wipe_profile_traces(profile_dir)
        human_delay(1.0, 2.0)

# ─── Main ──────────────────────────────────────────────────────────────────────

BANNER = r"""
  ╔═══════════════════════════════════════════════╗
  ║  CF Workers AI Farmer — Signup + Harvest      ║
  ║  ☕ https://saweria.co/febfrmn                  ║
  ╚═══════════════════════════════════════════════╝
"""

def main():
    parser = argparse.ArgumentParser(description="CF Workers AI Token Harvester")
    parser.add_argument("--proxy", help="Global proxy (http://user:pass@ip:port)")
    parser.add_argument("--only", help="Harvest single account (email)")
    parser.add_argument("--delay", type=int, default=15, help="Delay between accounts (seconds)")
    parser.add_argument("--clean", action="store_true", help="Delete cf_keys.txt and exit")
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
        print("Usage:")
        print("  python3 cf_farmer.py                     # signup + harvest from akun.txt")
        print("  python3 cf_farmer.py --only user@x.com   # single account")
        print("  python3 cf_farmer.py --proxy http://...   # with proxy")
        print("  python3 cf_farmer.py --clean              # reset cf_keys.txt")
        print("\nakun.txt format:")
        print("  email|password")
        print("  email|password|http://proxy:port  (optional proxy)")
        sys.exit(1)

    existing = get_harvested()
    if existing:
        print(f"⚠️  cf_keys.txt has {len(existing)} keys. Duplicates auto-skipped.\n")

    print(f"Mode: Signup + Harvest ({len(accounts)} accounts)")
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
