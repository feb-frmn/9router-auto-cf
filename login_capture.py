#!/usr/bin/env python3
"""
FASE 1 — Login Google (browser, 1x per akun) + capture cookie jar.
Ini satu-satunya step yang nyentuh BotGuard. Sekali per akun.
Output: sessions/<email>.json  (cookie jar penuh, buat direplay via curl_cffi).

Kenapa browser: Google BotGuard blokir login pure-HTTP (butuh eval JS).
Setelah login, cookie SID/HSID/SSID/APISID/SAPISID + __Secure-*PSID* disimpan.
Fase 2 (harvest_api.py) replay cookie ini TANPA browser.
"""
import os, sys, json, time, random, argparse
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions

SCRIPT_DIR = Path(__file__).resolve().parent
AKUN_FILE = SCRIPT_DIR / "akun.txt"
SESSION_DIR = SCRIPT_DIR / "sessions"
PROFILE_ROOT = SCRIPT_DIR / ".chrome_profiles"

# Cookie yang wajib di-capture buat replay OAuth (dari riset)
WANTED_COOKIES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS",
    "__Secure-1PAPISID", "__Secure-3PAPISID",
    "NID", "LSID", "__Secure-1PSIDCC", "__Secure-3PSIDCC",
}


def read_accounts():
    if not AKUN_FILE.exists():
        return []
    out = []
    for l in AKUN_FILE.read_text().splitlines():
        if "|" in l and l.strip():
            e, p = l.split("|")[0].strip(), l.split("|")[1].strip()
            out.append({"email": e, "password": p})
    return out


def human_type(ele, text):
    ele.clear()
    for c in text:
        ele.input(c)
        time.sleep(random.uniform(0.05, 0.18))


def human_click(page, ele):
    try:
        page.actions.move_to(ele)
        time.sleep(random.uniform(0.3, 0.7))
    except Exception:
        pass
    ele.click()


def capture_google_cookies(page):
    """Ambil semua cookie .google.com dari browser."""
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
    """Cek cookie login inti ada (SID / __Secure-1PSID)."""
    names = {c["name"] for c in jar}
    return "SID" in names or "__Secure-1PSID" in names


def login_account(account, index, total, proxy=None):
    email = account["email"]
    password = account["password"]
    print(f"\n{'='*55}\n [{index}/{total}] {email}\n{'='*55}")

    # profile persist by EMAIL (bukan index) → aman kalau urutan berubah
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
    co.set_local_port(9300 + index)
    if proxy:
        co.set_argument(f"--proxy-server={proxy}")
    page = ChromiumPage(co)

    try:
        # Cek: profile udah punya session? (login sebelumnya)
        page.get("https://myaccount.google.com/")
        time.sleep(random.uniform(3.0, 5.0))
        jar = capture_google_cookies(page)
        if has_session(jar) and "signin" not in page.url and "ServiceLogin" not in page.url:
            print("    ✅ Profile udah login (session tersimpan) — skip BotGuard")
        else:
            print(" [1] Login Google (BotGuard step, browser)...")
            page.get("https://accounts.google.com/ServiceLogin")
            time.sleep(random.uniform(3.0, 5.0))

            # Account chooser?
            if "accountchooser" in page.url:
                btn = page.ele("@text():Use another account", timeout=3)
                if btn:
                    human_click(page, btn)
                    time.sleep(random.uniform(2.0, 3.0))

            # Email
            page.wait.ele_displayed("#identifierId", timeout=15)
            human_type(page.ele("#identifierId"), email)
            time.sleep(random.uniform(0.5, 1.0))
            human_click(page, page.ele("#identifierNext"))
            time.sleep(random.uniform(4.0, 6.0))

            # Password
            pw = page.ele("@type=password", timeout=12)
            if not pw:
                raise Exception("Field password gak muncul (mungkin OOB/challenge)")
            human_type(pw, password)
            time.sleep(random.uniform(0.5, 1.0))
            human_click(page, page.ele("#passwordNext"))
            time.sleep(random.uniform(6.0, 9.0))

            # Workspace TOS speedbump
            if "workspacetermsofservice" in page.url or "speedbump" in page.url:
                btn = page.ele('tag:button@@text():I understand', timeout=5)
                if btn:
                    human_click(page, btn)
                    time.sleep(random.uniform(3.0, 5.0))

            # Cek challenge (OOB/phone) → gagal, skip akun ini
            if "challenge" in page.url or "signin/v2/challenge" in page.url:
                raise Exception("Kena challenge (OOB/phone verif) — skip")

            # Re-capture setelah login
            page.get("https://myaccount.google.com/")
            time.sleep(random.uniform(3.0, 5.0))
            jar = capture_google_cookies(page)

        if not has_session(jar):
            raise Exception("Gagal capture session cookie (SID/__Secure-1PSID)")

        # Simpan cookie jar
        SESSION_DIR.mkdir(exist_ok=True)
        out_file = SESSION_DIR / f"{safe}.json"
        out_file.write_text(json.dumps({
            "email": email,
            "captured_at": int(time.time()),
            "cookies": jar,
        }, indent=2))
        names = sorted({c["name"] for c in jar} & WANTED_COOKIES)
        print(f" [OK] {len(jar)} cookie disimpan → sessions/{safe}.json")
        print(f"      cookie inti: {names}")
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--proxy", help="proxy per sesi, mis. http://user:pass@ip:port")
    ap.add_argument("--only", help="login 1 email aja (buat test)")
    ap.add_argument("--delay", type=int, default=10, help="delay antar akun (detik)")
    args = ap.parse_args()

    accounts = read_accounts()
    if args.only:
        accounts = [a for a in accounts if a["email"] == args.only]
    if not accounts:
        print("Isi akun.txt (email|password) atau --only email salah")
        sys.exit(1)

    print(f"Total akun: {len(accounts)}")
    ok = 0
    for i, acc in enumerate(accounts, 1):
        if login_account(acc, i, len(accounts), proxy=args.proxy):
            ok += 1
        if i < len(accounts):
            d = random.randint(args.delay, args.delay + 8)
            print(f"\nTunggu {d}s...")
            time.sleep(d)
    print(f"\n{'='*55}\n LOGIN DONE: {ok}/{len(accounts)} session captured\n{'='*55}")


if __name__ == "__main__":
    main()
