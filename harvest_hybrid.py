#!/usr/bin/env python3
"""
harvest_hybrid.py v2 — Silent OAuth harvest
Login via browser (profile persisted from login_capture.py)
→ OAuth CF silent redirect → grab account_id + create token via CF internal API
→ save to cf_keys.txt + inject 9router

Usage:
  python3 harvest_hybrid.py              # normal speed
  python3 harvest_hybrid.py --fast       # fast mode (minimal delays)
  python3 harvest_hybrid.py --delay 3    # custom delay between accounts
  python3 harvest_hybrid.py --only user@jujusa.my.id  # single account
"""
import os, sys, json, time, random, argparse
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions

SCRIPT_DIR = Path(__file__).resolve().parent
AKUN_FILE  = SCRIPT_DIR / "akun.txt"
RESULT_FILE = SCRIPT_DIR / "cf_keys.txt"
PROFILE_ROOT = SCRIPT_DIR / ".chrome_profiles"
SESSION_DIR  = SCRIPT_DIR / "sessions"

MODELS = '["@cf/zai-org/glm-5.2","@cf/deepseek-ai/deepseek-r1-distill-qwen-32b","@cf/meta/llama-3.3-70b-instruct-fp8-fast","@cf/qwen/qwen2.5-coder-32b-instruct","@cf/qwen/qwq-32b"]'

BANNER = f"""
\033[36m╔══════════════════════════════════════════════════════╗
║  \033[1;37m☁️  CF Workers AI Harvester v2\033[0;36m                       ║
║  \033[2mSilent OAuth · Auto Token · 9Router Inject\033[0;36m           ║
╚══════════════════════════════════════════════════════╝\033[0m
  \033[2m☕ https://saweria.co/febfrmn\033[0m
"""

FAST_DELAY = (2, 4)     # delay between steps in fast mode
NORM_DELAY = (3, 5)     # delay between steps in normal mode
FAST_ACCOUNT_DELAY = (3, 6)   # delay between accounts in fast mode
NORM_ACCOUNT_DELAY = (8, 15)  # delay between accounts in normal mode


def read_accounts():
    if not AKUN_FILE.exists(): return []
    return [{"email": l.split("|")[0].strip(), "password": l.split("|")[1].strip()}
            for l in AKUN_FILE.read_text().splitlines() if "|" in l and l.strip()]


def get_harvested():
    if not RESULT_FILE.exists(): return set()
    ids = set()
    for line in RESULT_FILE.read_text().splitlines():
        parts = line.split("|")
        if len(parts) >= 2 and "/accounts/" in parts[1]:
            ids.add(parts[1].split("/accounts/")[1].split("/")[0])
    return ids


def get_wa_perms(page):
    result = page.run_js("""return (async()=>{
        const r = await fetch('/api/v4/user/tokens/permission_groups');
        return await r.json();
    })();""")
    if not result.get("success"): return []
    return [{"id": g["id"], "name": g["name"]}
            for g in result.get("result", [])
            if "workers ai" in g.get("name", "").lower()]


def create_token(page, account_id, perm_ids):
    payload = json.dumps({
        "name": f"cf-ai-{int(time.time())}",
        "policies": [{"effect": "allow",
                      "permission_groups": perm_ids,
                      "resources": {f"com.cloudflare.api.account.{account_id}": "*"}}]
    })
    result = page.run_js("""return (async()=>{
        const payload = JSON.parse(%s);
        const r = await fetch('/api/v4/user/tokens', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify(payload)
        });
        return await r.json();
    })();""" % json.dumps(payload))
    if result.get("success") and result.get("result", {}).get("value"):
        return result["result"]["value"]
    return None


def harvest_one(account, index, total, harvested, fast=False):
    email    = account["email"]
    password = account["password"]
    delays = FAST_DELAY if fast else NORM_DELAY
    print(f"\n{'='*55}\n [{index}/{total}] {email}\n{'='*55}")

    safe = email.replace("@","_at_").replace(".","_")
    profile_dir = PROFILE_ROOT / safe
    profile_dir.mkdir(parents=True, exist_ok=True)

    co = ChromiumOptions()
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1280,720")
    co.set_argument("--no-sandbox"); co.set_argument("--disable-gpu")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument(f"--user-data-dir={profile_dir}")
    # Unique port per account (avoid conflicts)
    port = 9400 + (index % 100)
    co.set_local_port(port)
    page = ChromiumPage(co)

    try:
        # Step 1: Check CF session
        print(" [1] Opening CF login...")
        page.get("https://dash.cloudflare.com/login")
        time.sleep(random.uniform(*delays))

        if "dash.cloudflare.com/" in page.url and "/login" not in page.url:
            print("    ✅ CF session active (persisted profile)")
        else:
            # Step 2: OAuth Google (silent if Google session active)
            print(" [2] Google OAuth (silent if session active)...")
            btn = page.ele("@text():Google", timeout=8)
            if not btn:
                raise Exception("Google button not found")

            tabs_before = set(page.tab_ids)
            btn.click()
            time.sleep(random.uniform(*delays))

            # Find Google OAuth tab
            new_tab_id = None
            for _ in range(6):
                new_tabs = set(page.tab_ids) - tabs_before
                if new_tabs:
                    new_tab_id = new_tabs.pop(); break
                time.sleep(1)
            if not new_tab_id:
                for tid in page.tab_ids:
                    try:
                        t = page.get_tab(tid)
                        if "accounts.google.com" in (t.url or ""):
                            new_tab_id = tid; break
                    except: continue

            if new_tab_id:
                tab = page.get_tab(new_tab_id)
                time.sleep(random.uniform(2.0, 3.0))
                cur_url = tab.url or ""

                if "accounts.google.com" in cur_url:
                    allow = tab.ele("@text():Allow", timeout=5)
                    if allow:
                        allow.click()
                        time.sleep(random.uniform(2.0, 3.0))

                    if "accounts.google.com" in (tab.url or ""):
                        acct_btn = tab.ele(f"@data-email:{email}", timeout=3)
                        if acct_btn:
                            acct_btn.click()
                            time.sleep(random.uniform(1.5, 2.5))
                        else:
                            print("    ⚠️  Google session expired — re-run login_capture.py")
                            raise Exception("Google session expired")

            # Wait for CF dashboard redirect
            print(" [3] Waiting for CF dashboard redirect...")
            for _ in range(15):
                time.sleep(1 if fast else 2)
                if "dash.cloudflare.com/" in page.url and "/login" not in page.url:
                    break
            if "/login" in page.url:
                raise Exception("Redirect to CF dashboard failed")

        # Step 4: Get account ID
        account_id = None
        for tid in page.tab_ids:
            try:
                t = page.get_tab(tid)
                url = t.url or ""
                if "dash.cloudflare.com/" in url:
                    parts = url.split("dash.cloudflare.com/")
                    if len(parts) > 1:
                        aid = parts[1].split("/")[0].split("?")[0]
                        if len(aid) > 10:
                            account_id = aid; page = t; break
            except: continue

        if not account_id:
            page.get("https://dash.cloudflare.com/")
            time.sleep(3 if fast else 4)
            parts = page.url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                aid = parts[1].split("/")[0].split("?")[0]
                if len(aid) > 10:
                    account_id = aid

        if not account_id:
            raise Exception(f"Failed to get account_id. URL: {page.url}")
        print(f" [4] Account ID: {account_id}")

        # Skip if already harvested
        if account_id in harvested:
            print(f" [SKIP] Already in cf_keys.txt")
            return True

        # Step 5: Get Workers AI permissions
        print(" [5] Getting Workers AI permissions...")
        perm_ids = get_wa_perms(page)
        if not perm_ids:
            raise Exception("Failed to get permission groups")
        print(f"    {len(perm_ids)} perms: {[p['name'] for p in perm_ids]}")

        # Step 6: Create API token
        print(" [6] Creating API token...")
        token = create_token(page, account_id, perm_ids)
        if not token:
            raise Exception("API failed to create token")

        base_url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1"
        with open(RESULT_FILE, "a") as f:
            f.write(f"cloudflare_{account_id[:6]}|{base_url}|{token}|{MODELS}\n")
        print(f" [SUCCESS] Token: {token[:20]}... → cf_keys.txt")
        return True

    except Exception as e:
        print(f" [ERROR] {email}: {e}")
        return False
    finally:
        try: page.quit()
        except: pass


def main():
    print(BANNER)
    ap = argparse.ArgumentParser(description="CF Workers AI Token Harvester v2")
    ap.add_argument("--fast", action="store_true", help="Fast mode (minimal delays)")
    ap.add_argument("--delay", type=int, default=None, help="Custom delay between accounts (seconds)")
    ap.add_argument("--only", help="Harvest single account only")
    args = ap.parse_args()

    accounts = read_accounts()
    if args.only:
        accounts = [a for a in accounts if a["email"] == args.only]
    if not accounts:
        print("❌ Add accounts to akun.txt (email|password per line)")
        sys.exit(1)

    harvested = get_harvested()
    print(f"Total accounts: {len(accounts)} | Already harvested: {len(harvested)}")
    if args.fast:
        print("⚡ Fast mode enabled\n")

    ok = 0
    for i, acc in enumerate(accounts, 1):
        if harvest_one(acc, i, len(accounts), harvested, fast=args.fast):
            ok += 1
        if i < len(accounts):
            if args.delay is not None:
                d = args.delay
            elif args.fast:
                d = random.randint(*FAST_ACCOUNT_DELAY)
            else:
                d = random.randint(*NORM_ACCOUNT_DELAY)
            print(f"\n  Waiting {d}s...")
            time.sleep(d)

    print(f"\n{'='*55}\n DONE: {ok}/{len(accounts)} successful\n{'='*55}")
    print(f"  ☕ https://saweria.co/febfrmn\n")


if __name__ == "__main__":
    main()
