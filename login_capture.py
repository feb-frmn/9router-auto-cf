#!/usr/bin/env python3
"""
login_capture.py v2 — Google OAuth login + session capture (1x per account).
This is the ONLY step that touches BotGuard. Once per account.
Output: sessions/<email>.json (cookie jar for harvest_hybrid.py)

Usage:
  python3 login_capture.py                # all accounts
  python3 login_capture.py --fast         # fast mode (minimal delays)
  python3 login_capture.py --delay 5      # custom delay between accounts
  python3 login_capture.py --only user@jujusa.my.id  # single account
"""
import os, sys, json, time, random, argparse
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions

SCRIPT_DIR = Path(__file__).resolve().parent
AKUN_FILE = SCRIPT_DIR / "akun.txt"
SESSION_DIR = SCRIPT_DIR / "sessions"
PROFILE_ROOT = SCRIPT_DIR / ".chrome_profiles"

WANTED_COOKIES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",
    "__Secure-1PAPISID", "__Secure-3PAPISID",
    "NID", "LSID", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
}

BANNER = f"""
\033[36m╔══════════════════════════════════════════════════════╗
║  \033[1;37m🔐 CF Login Capture v2\033[0;36m                               ║
║  \033[2mGoogle OAuth · Session Persist · One-time per account\033[0;36m ║
╚══════════════════════════════════════════════════════╝\033[0m
  \033[2m☕ https://saweria.co/febfrmn\033[0m
"""

FAST_DELAY = (1.5, 3.0)
NORM_DELAY = (3.0, 5.0)
FAST_ACCOUNT_DELAY = (3, 6)
NORM_ACCOUNT_DELAY = (10, 18)


def read_accounts():
    if not AKUN_FILE.exists():
        return []
    out = []
    for l in AKUN_FILE.read_text().splitlines():
        if "|" in l and l.strip():
            e, p = l.split("|")[0].strip(), l.split("|")[1].strip()
            out.append({"email": e, "password": p})
    return out


def human_type(ele, text, fast=False):
    ele.clear()
    delay_range = (0.02, 0.08) if fast else (0.05, 0.18)
    for c in text:
        ele.input(c)
        time.sleep(random.uniform(*delay_range))


def human_click(page, ele, fast=False):
    try:
        page.actions.move_to(ele)
        time.sleep(random.uniform(0.1, 0.3) if fast else (0.3, 0.7))
    except Exception:
        pass
    ele.click()


def capture_google_cookies(page):
    all_cookies = page.cookies(all_domains=True)
    jar = []
    for c in all_cookies:
        dom = c.get("domain", "")
        if "google.com" in dom:
            jar.append({
                "name": c.get("name"),
                "value": c.get("value"),
                "domain": dom,
                "path": c.get("path", "/"),
            })
    return jar


def has_session(jar):
    names = {c["name"] for c in jar}
    return "SID" in names or "__Secure-1PSID" in names


def login_account(account, index, total, proxy=None, fast=False):
    email = account["email"]
    password = account["password"]
    delays = FAST_DELAY if fast else NORM_DELAY
    print(f"\n{'='*55}\n [{index}/{total}] {email}\n{'='*55}")

    safe = email.replace("@", "_at_").replace(".", "_")
    profile_dir = PROFILE_ROOT / safe
    profile_dir.mkdir(parents=True, exist_ok=True)

    co = ChromiumOptions()
    co.set_argument("--disable-blink-features=AutomationControlled")
    co.set_argument("--window-size=1280,720")
    co.set_argument("--no-sandbox")
    co.set_argument("--disable-gpu")
    co.set_argument("--disable-dev-shm-usage")
    co.set_argument(f"--user-data-dir={profile_dir}")
    port = 9300 + index
    co.set_local_port(port)
    if proxy:
        co.set_argument(f"--proxy-server={proxy}")
    page = ChromiumPage(co)

    try:
        # Check if profile already logged in
        page.get("https://myaccount.google.com/")
        time.sleep(random.uniform(*delays))
        jar = capture_google_cookies(page)
        if has_session(jar) and "signin" not in page.url and "ServiceLogin" not in page.url:
            print("    ✅ Profile already logged in (persisted session)")
        else:
            print(" [1] Google login (BotGuard step)...")
            page.get("https://accounts.google.com/ServiceLogin")
            time.sleep(random.uniform(*delays))

            # Account chooser
            if "accountchooser" in page.url:
                btn = page.ele("@text():Use another account", timeout=3)
                if btn:
                    human_click(page, btn, fast)
                    time.sleep(random.uniform(1.0, 2.0) if fast else (2.0, 3.0))

            # Email
            page.wait.ele_displayed("#identifierId", timeout=15)
            human_type(page.ele("#identifierId"), email, fast)
            time.sleep(random.uniform(0.3, 0.5) if fast else (0.5, 1.0))
            human_click(page, page.ele("#identifierNext"), fast)
            time.sleep(random.uniform(2.0, 4.0) if fast else (4.0, 6.0))

            # Password
            pw = page.ele("@type=password", timeout=12)
            if not pw:
                raise Exception("Password field not shown (possible OOB/challenge)")
            human_type(pw, password, fast)
            time.sleep(random.uniform(0.3, 0.5) if fast else (0.5, 1.0))
            human_click(page, page.ele("#passwordNext"), fast)
            time.sleep(random.uniform(3.0, 5.0) if fast else (6.0, 9.0))

            # Workspace TOS speedbump
            if "workspacetermsofservice" in page.url or "speedbump" in page.url:
                btn = page.ele('tag:button@@text():I understand', timeout=5)
                if btn:
                    human_click(page, btn, fast)
                    time.sleep(random.uniform(1.5, 3.0) if fast else (3.0, 5.0))

            # Challenge check (OOB/phone) → fail
            if "challenge" in page.url or "signin/v2/challenge" in page.url:
                raise Exception("Challenge detected (OOB/phone verify) — skip")

            # Re-capture after login
            page.get("https://myaccount.google.com/")
            time.sleep(random.uniform(*delays))
            jar = capture_google_cookies(page)

        if not has_session(jar):
            raise Exception("Failed to capture session cookie (SID/__Secure-1PSID)")

        # Save cookie jar
        SESSION_DIR.mkdir(exist_ok=True)
        out_file = SESSION_DIR / f"{safe}.json"
        out_file.write_text(json.dumps({
            "email": email,
            "captured_at": int(time.time()),
            "cookies": jar,
        }, indent=2))
        names = sorted({c["name"] for c in jar} & WANTED_COOKIES)
        print(f" [OK] {len(jar)} cookies saved → sessions/{safe}.json")
        print(f"      Core cookies: {names}")
        return True

    except Exception as e:
        print(f" [ERROR] {email}: {e}")
        return False
    finally:
        try:
            page.quit()
        except Exception:
            pass


def main():
    print(BANNER)
    ap = argparse.ArgumentParser(description="CF Login Capture v2")
    ap.add_argument("--proxy", help="Proxy per session (e.g. http://user:pass@ip:port)")
    ap.add_argument("--only", help="Login single email only (for testing)")
    ap.add_argument("--fast", action="store_true", help="Fast mode (minimal delays)")
    ap.add_argument("--delay", type=int, default=None, help="Custom delay between accounts (seconds)")
    args = ap.parse_args()

    accounts = read_accounts()
    if args.only:
        accounts = [a for a in accounts if a["email"] == args.only]
    if not accounts:
        print("❌ Add accounts to akun.txt (email|password) or use --only email")
        sys.exit(1)

    print(f"Total accounts: {len(accounts)}")
    if args.fast:
        print("⚡ Fast mode enabled\n")

    ok = 0
    for i, acc in enumerate(accounts, 1):
        if login_account(acc, i, len(accounts), proxy=args.proxy, fast=args.fast):
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
    print(f"\n{'='*55}\n LOGIN DONE: {ok}/{len(accounts)} sessions captured\n{'='*55}")
    print(f"  ☕ https://saweria.co/febfrmn\n")


if __name__ == "__main__":
    main()
