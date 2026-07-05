#!/usr/bin/env python3
"""
harvest_hybrid.py — FASE 2A: Hybrid harvest
Login via browser (silent, no BotGuard — profile udah authenticated dari login_capture.py)
→ OAuth CF silent redirect → grab account_id + create token via CF internal API
→ simpan ke cf_keys.txt + inject 9router

Kenapa hybrid bukan murni-HTTP:
- CF mungkin pakai PKCE (code_verifier dibuat di JS) → gak bisa replay HTTP
- CSRF header name rotate antar versi dash
- OAuth authorize step gak kena BotGuard kalau profile udah login Google (silent)
- Browser step ini CEPET (<10s/akun) — gak ngetik password, langsung redirect

Untuk murni-HTTP OAuth, gunakan harvest_http.py (eksperimental, mungkin kena PKCE).
"""
import os, sys, json, time, random
from pathlib import Path
from DrissionPage import ChromiumPage, ChromiumOptions

SCRIPT_DIR = Path(__file__).resolve().parent
AKUN_FILE  = SCRIPT_DIR / "akun.txt"
RESULT_FILE = SCRIPT_DIR / "cf_keys.txt"
PROFILE_ROOT = SCRIPT_DIR / ".chrome_profiles"
SESSION_DIR  = SCRIPT_DIR / "sessions"

MODELS = '["@cf/zai-org/glm-5.2","@cf/deepseek-ai/deepseek-r1-distill-qwen-32b","@cf/meta/llama-3.3-70b-instruct-fp8-fast","@cf/qwen/qwen2.5-coder-32b-instruct","@cf/qwen/qwq-32b"]'


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


def harvest_one(account, index, total, harvested):
    email    = account["email"]
    password = account["password"]
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
    co.set_local_port(9400 + index)
    page = ChromiumPage(co)

    try:
        # === LOGIN CHECK — kalau profile udah punya session Google, OAuth CF jadi silent ===
        print(" [1] Buka CF login...")
        page.get("https://dash.cloudflare.com/login")
        time.sleep(random.uniform(3.0, 5.0))

        # Udah login CF? (profile sebelumnya)
        if "dash.cloudflare.com/" in page.url and "/login" not in page.url:
            print("    ✅ CF session masih aktif (dari profile persist)")
        else:
            # Klik Continue with Google
            print(" [2] OAuth Google (harusnya silent jika Google session aktif)...")
            btn = page.ele("@text():Google", timeout=8)
            if not btn:
                raise Exception("Tombol Google gak ketemu")

            tabs_before = set(page.tab_ids)
            btn.click()
            time.sleep(random.uniform(3.0, 5.0))

            # Cari tab Google OAuth
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
                time.sleep(random.uniform(2.0, 4.0))
                cur_url = tab.url or ""

                if "accounts.google.com" in cur_url:
                    # Consent sudah granted → harusnya auto-redirect
                    # Kalau muncul consent screen, klik Allow
                    allow = tab.ele("@text():Allow", timeout=5)
                    if allow:
                        allow.click()
                        time.sleep(random.uniform(2.0, 4.0))

                    # Kalau masih di Google (account chooser), klik akun
                    if "accounts.google.com" in (tab.url or ""):
                        acct_btn = tab.ele(f"@data-email:{email}", timeout=3)
                        if acct_btn:
                            acct_btn.click()
                            time.sleep(random.uniform(2.0, 3.0))
                        else:
                            # Harus login (session expired), fall back ke DrissionPage login
                            print("    ⚠️  Google session expired — perlu re-login")
                            print("       Jalankan: python3 login_capture.py --only", email)
                            raise Exception("Google session expired — re-run login_capture.py")

            # Tunggu redirect ke CF dashboard
            print(" [3] Tunggu redirect ke CF dashboard...")
            for _ in range(15):
                time.sleep(2)
                if "dash.cloudflare.com/" in page.url and "/login" not in page.url:
                    break
            if "/login" in page.url:
                raise Exception("Redirect ke CF dashboard gagal")

        # === AMBIL ACCOUNT ID ===
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
            time.sleep(4)
            parts = page.url.split("dash.cloudflare.com/")
            if len(parts) > 1:
                aid = parts[1].split("/")[0].split("?")[0]
                if len(aid) > 10:
                    account_id = aid

        if not account_id:
            raise Exception(f"Gagal ambil account_id. URL: {page.url}")
        print(f" [4] Account ID: {account_id}")

        # SKIP kalau udah di-harvest
        if account_id in harvested:
            print(f" [SKIP] Udah ada di cf_keys.txt")
            return True

        # === BUAT TOKEN ===
        print(" [5] Ambil Workers AI permissions...")
        perm_ids = get_wa_perms(page)
        if not perm_ids:
            raise Exception("Gagal ambil permission groups")
        print(f"    {len(perm_ids)} perms: {[p['name'] for p in perm_ids]}")

        print(" [6] Buat API token...")
        token = create_token(page, account_id, perm_ids)
        if not token:
            raise Exception("API gagal bikin token")

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
    accounts = read_accounts()
    if not accounts:
        print("Isi akun.txt"); sys.exit(1)

    harvested = get_harvested()
    print(f"Total akun: {len(accounts)} | Udah di-harvest: {len(harvested)}")

    ok = 0
    for i, acc in enumerate(accounts, 1):
        if harvest_one(acc, i, len(accounts), harvested):
            ok += 1
        if i < len(accounts):
            d = random.randint(8, 15)
            print(f"\nTunggu {d}s..."); time.sleep(d)

    print(f"\n{'='*55}\n DONE: {ok}/{len(accounts)} berhasil\n{'='*55}")


if __name__ == "__main__":
    main()
