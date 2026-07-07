#!/usr/bin/env python3
"""
test_demo.py — Audit & demo test suite untuk Cloudflare Workers AI Farmer.

Tests:
  1. Syntax check     — semua file Python valid
  2. Import check     — dependency terinstall
  3. Logic check      — fungsi inti behavior benar
  4. Token format     — cf_keys.txt format valid
  5. Permission fix   — verify clean_perms (no 'name' field leak)
  6. Bare except      — no bare except: clauses
  7. Anti-ban check   — fingerprint + trace removal + human delay
  8. Proxy support    — per-account + global proxy
  9. GSuite support   — Workspace TOS + Account Chooser
  10. Neuron tracking — cf_proxy.py estimation logic
  11. End-to-end demo — mock harvest + proxy flow

Usage:
  python3 test_demo.py              # all tests
  python3 test_demo.py --quick      # skip browser tests
  python3 test_demo.py --verbose    # show all output
"""

import os, sys, json, re, time, argparse, inspect
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PASS = 0
FAIL = 0
SKIP = 0
VERBOSE = False

def result(name, ok, detail=""):
    global PASS, FAIL
    icon = "✅" if ok else "❌"
    status = "PASS" if ok else "FAIL"
    if not ok and detail:
        status += f" — {detail}"
    print(f"  {icon} {name}: {status}")
    if ok: PASS += 1
    else: FAIL += 1
    if VERBOSE and ok and detail:
        print(f"     {detail}")

def skipped(name, reason=""):
    global SKIP
    print(f"  ⏭️  {name}: SKIP{' — ' + reason if reason else ''}")
    SKIP += 1

# ─── 1. SYNTAX CHECK ──────────────────────────────────────────────────────────
def test_syntax():
    print("\n📋 1. Syntax Check")
    print("   " + "─" * 50)
    for f in sorted(SCRIPT_DIR.glob("*.py")):
        if f.name == "test_demo.py": continue
        try:
            compile(f.read_text(), str(f), "exec")
            result(f"Syntax: {f.name}", True)
        except SyntaxError as e:
            result(f"Syntax: {f.name}", False, str(e))

# ─── 2. IMPORT CHECK ──────────────────────────────────────────────────────────
def test_imports():
    print("\n📋 2. Import Check")
    print("   " + "─" * 50)
    for label, module in [("DrissionPage", "DrissionPage"), ("requests", "requests")]:
        try:
            __import__(module)
            result(f"Import: {label}", True)
        except ImportError as e:
            result(f"Import: {label}", False, str(e))

# ─── 3. LOGIC CHECK ───────────────────────────────────────────────────────────
def test_logic():
    print("\n📋 3. Logic Check")
    print("   " + "─" * 50)
    try:
        import cf_farmer
        from pathlib import Path as P
        tmp = SCRIPT_DIR / ".test_akun.txt"
        tmp.write_text("test@gmail.com|pass123\nbad_entry\nuser@company.com|p455|http://proxy:8080\n")
        old = cf_farmer.AKUN_FILE
        cf_farmer.AKUN_FILE = P(str(tmp))
        accounts = cf_farmer.read_accounts()
        cf_farmer.AKUN_FILE = old
        tmp.unlink()
        ok = len(accounts) == 2 and accounts[1].get("proxy") == "http://proxy:8080"
        result("read_accounts: format + proxy parsing", ok,
               f"Got {len(accounts)} accounts" if not ok else f"{len(accounts)} accounts, proxy detected")
    except Exception as e:
        result("read_accounts: format + proxy parsing", False, str(e))

    try:
        import cf_farmer
        from pathlib import Path as P
        tmp = SCRIPT_DIR / ".test_cf_keys.txt"
        tmp.write_text(
            "cloudflare_abc|https://api.cloudflare.com/client/v4/accounts/abc123def456/ai/v1|cfut_tok|[]\n"
            "cloudflare_xyz|https://api.cloudflare.com/client/v4/accounts/xyz789aaa/ai/v1|cfut_tok2|[]\n"
        )
        old = cf_farmer.RESULT_FILE
        cf_farmer.RESULT_FILE = P(str(tmp))
        harvested = cf_farmer.get_harvested()
        cf_farmer.RESULT_FILE = old
        tmp.unlink()
        ok = "abc123def456" in harvested and "xyz789aaa" in harvested and len(harvested) == 2
        result("get_harvested: dedup set", ok, f"Got {harvested}" if not ok else f"{len(harvested)} unique")
    except Exception as e:
        result("get_harvested: dedup set", False, str(e))

    try:
        import cf_proxy
        aid = cf_proxy.extract_account_id("https://api.cloudflare.com/client/v4/accounts/abc123def/ai/v1")
        result("extract_account_id: URL parsing", aid == "abc123def",
               f"Got '{aid}'" if aid != "abc123def" else "Correct")
    except Exception as e:
        result("extract_account_id: URL parsing", False, str(e))

# ─── 4. TOKEN FORMAT ──────────────────────────────────────────────────────────
def test_token_format():
    print("\n📋 4. Token Format Check")
    print("   " + "─" * 50)
    keys_file = SCRIPT_DIR / "cf_keys.txt"
    if not keys_file.exists():
        skipped("cf_keys.txt: not found (run cf_farmer.py first)")
        return
    for i, line in enumerate(keys_file.read_text().strip().splitlines(), 1):
        parts = line.split("|")
        ok = (len(parts) == 4 and parts[0].startswith("cloudflare_")
              and "api.cloudflare.com" in parts[1] and "/accounts/" in parts[1]
              and parts[2].startswith("cfut_") and parts[3].startswith("["))
        result(f"cf_keys.txt line {i}", ok, "" if ok else f"format wrong: {len(parts)} parts")

# ─── 5. PERMISSION FIX ────────────────────────────────────────────────────────
def test_permission_fix():
    print("\n📋 5. Permission Groups Fix Check")
    print("   " + "─" * 50)
    for fname in ["cf_farmer.py"]:
        fpath = SCRIPT_DIR / fname
        if not fpath.exists():
            skipped(f"{fname}: not found"); continue
        content = fpath.read_text()
        has_clean = bool(re.search(r'clean_perms\s*=\s*\[\{"id"', content))
        sends_raw = '"permission_groups": perm_ids' in content
        ok = has_clean and not sends_raw
        result(f"{fname}: clean_perms fix", ok, "Only sends id field" if ok else "Still raw")

# ─── 6. BARE EXCEPT ───────────────────────────────────────────────────────────
def test_no_bare_except():
    print("\n📋 6. Bare Except Check")
    print("   " + "─" * 50)
    for f in sorted(SCRIPT_DIR.glob("*.py")):
        if f.name == "test_demo.py": continue
        bare = re.findall(r'except\s*:', f.read_text())
        result(f"{f.name}: no bare except", len(bare) == 0,
               f"{len(bare)} found" if bare else "All Exception")

# ─── 7. ANTI-BAN CHECK ────────────────────────────────────────────────────────
def test_anti_ban():
    print("\n📋 7. Anti-Ban Check")
    print("   " + "─" * 50)
    fpath = SCRIPT_DIR / "cf_farmer.py"
    if not fpath.exists():
        skipped("cf_farmer.py not found"); return
    content = fpath.read_text()

    checks = [
        ("Fingerprint randomization", "random_fingerprint" in content and "USER_AGENTS" in content),
        ("Human typing delays", "human_type" in content and "random.uniform" in content),
        ("Human click delays", "human_click" in content),
        ("Trace removal (wipe_profile_traces)", "wipe_profile_traces" in content),
        ("Cookie clearing", "cookies.clear()" in content),
        ("Random window sizes", "WINDOW_SIZES" in content),
        ("Random user agents", "USER_AGENTS" in content),
        ("Profile per account (hash)", "profile_hash" in content or "md5" in content),
        ("Session logout before login", "cloudflare.com/logout" in content),
        ("Headless mode (--headless=new)", "--headless=new" in content),
    ]
    for name, ok in checks:
        result(f"Anti-ban: {name}", ok)

    # 7b. CF Signup Check
    print("\n📋 7b. CF Signup Check")
    print("   " + "─" * 50)
    checks = [
        ("cf_signup function", "def cf_signup" in content),
        ("CF signup URL (dash.cloudflare.com/sign-up)", "dash.cloudflare.com/sign-up" in content),
        ("Email field detection", "tag:input@type=email" in content),
        ("Password field detection", "tag:input@type=password" in content),
        ("Submit button detection", "@type=submit" in content),
        ("Email verification wait", "verify" in content.lower() and "180" in content),
        ("Auto-fallback to CF login", "cf_login_email" in content),
        ("Auto-fallback to Google OAuth", "cf_login_google" in content),
        ("Signup → login → Google OAuth chain", "cf_signup" in content and "cf_login_email" in content and "cf_login_google" in content),
        ("akun.txt input (user provides email)", "read_accounts" in content and "email|password" not in content or "parts[0]" in content),
    ]
    for name, ok in checks:
        result(f"Signup: {name}", ok)

# ─── 8. PROXY SUPPORT ─────────────────────────────────────────────────────────
def test_proxy():
    print("\n📋 8. Proxy Support Check")
    print("   " + "─" * 50)
    fpath = SCRIPT_DIR / "cf_farmer.py"
    if not fpath.exists():
        skipped("cf_farmer.py not found"); return
    content = fpath.read_text()
    checks = [
        ("Global --proxy flag", "--proxy" in content and "global_proxy" in content),
        ("Per-account proxy (akun.txt)", '"proxy"' in content and 'parts[2]' in content),
        ("Chromium --proxy-server arg", "--proxy-server" in content),
        ("Proxy displayed in output", 'Proxy:' in content),
    ]
    for name, ok in checks:
        result(f"Proxy: {name}", ok)

# ─── 9. GSUITE ────────────────────────────────────────────────────────────────
def test_gsuite():
    print("\n📋 9. GSuite Support Check")
    print("   " + "─" * 50)
    fpath = SCRIPT_DIR / "cf_farmer.py"
    if not fpath.exists():
        skipped("cf_farmer.py not found"); return
    content = fpath.read_text()
    checks = [
        ("Workspace TOS handling", "workspacetermsofservice" in content or "speedbump" in content),
        ("Account Chooser", "accountchooser" in content),
        ("Google OAuth flow", '@text():Google' in content),
        ("Domain-agnostic (email|password)", "loginId" not in content),
    ]
    for name, ok in checks:
        result(f"GSuite: {name}", ok)

# ─── 10. NEURON TRACKING ──────────────────────────────────────────────────────
def test_neuron_tracking():
    print("\n📋 10. Neuron Tracking Check")
    print("   " + "─" * 50)
    fpath = SCRIPT_DIR / "cf_proxy.py"
    if not fpath.exists():
        skipped("cf_proxy.py not found"); return
    try:
        import cf_proxy
        # Test estimation
        n = cf_proxy.estimate_neurons("@cf/meta/llama-3.2-1b-instruct", 1e6, 1e6)
        expected = 2457 + 18252
        result("Neuron estimation: known model", abs(n - expected) < 0.01,
               f"Expected {expected}, got {n:.2f}" if abs(n - expected) >= 0.01 else f"✓ {n:.2f}")

        n2 = cf_proxy.estimate_neurons("@cf/unknown/model", 1e6, 0)
        result("Neuron estimation: unknown fallback", abs(n2 - cf_proxy.DEFAULT_RATE["in"]) < 0.01,
               f"Expected {cf_proxy.DEFAULT_RATE['in']}, got {n2:.2f}")

        result("Neuron: NEURON_FREE_DAILY = 10000", cf_proxy.NEURON_FREE_DAILY == 10000)

        content = fpath.read_text()
        checks = [
            ("Rate table (RATES)", "RATES" in content and "llama-3.3-70b" in content),
            ("429 cooldown (mark_429)", "mark_429" in content),
            ("Daily limit 4006 handling", "4006" in content),
            ("Round-robin rotation", "_cursor" in content),
            ("In-flight reservation", "_reserved" in content),
            ("Thread-safe lock", "threading.Lock" in content or "_lock" in content),
            ("Account pool class", "AccountPool" in content),
            ("Streaming support", "stream" in content.lower()),
            ("OpenAI-compatible /v1/chat/completions", "/v1/chat/completions" in content),
            ("Dashboard HTML", "_send_dashboard" in content or "dashboard" in content.lower()),
            ("9Router DB import", "import_from_9router" in content),
            ("cf_keys.txt import", "import_from_file" in content),
        ]
        for name, ok in checks:
            result(f"Neuron: {name}", ok)
    except Exception as e:
        result("Neuron tracking import", False, str(e))

# ─── 11. E2E MOCK ─────────────────────────────────────────────────────────────
def test_e2e_mock():
    print("\n📋 11. End-to-End Demo (Mock)")
    print("   " + "─" * 50)
    try:
        import cf_farmer
        # Verify MODELS is valid JSON
        models = json.loads(cf_farmer.MODELS)
        result("Mock: MODELS valid JSON", isinstance(models, list) and len(models) > 0,
               f"{len(models)} models")

        # Verify output format
        fake_id = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        line = f"cloudflare_{fake_id[:6]}|https://api.cloudflare.com/client/v4/accounts/{fake_id}/ai/v1|cfut_FAKE|{cf_farmer.MODELS}"
        parts = line.split("|")
        result("Mock: output format", len(parts) == 4 and parts[2].startswith("cfut_"),
               f"{len(parts)} parts")

        # Verify fingerprint function returns dict
        fp = cf_farmer.random_fingerprint()
        result("Mock: fingerprint generation",
               "user_agent" in fp and "window_size" in fp,
               f"keys: {list(fp.keys())}")
    except Exception as e:
        result("Mock: import cf_farmer", False, str(e))

    try:
        import cf_proxy
        # Verify DB init
        tmp_db = SCRIPT_DIR / ".test_proxy.db"
        db = cf_proxy.init_db(tmp_db)
        result("Mock: DB init", db is not None)
        # Verify pool
        p = cf_proxy.AccountPool(db)
        result("Mock: AccountPool creation", p is not None)
        # Stats with empty pool
        s = p.stats()
        result("Mock: stats on empty pool", s["total"] == 0)
        tmp_db.unlink()
    except Exception as e:
        result("Mock: cf_proxy DB/pool", False, str(e))

# ─── MAIN ─────────────────────────────────────────────────────────────────────
def main():
    global VERBOSE
    parser = argparse.ArgumentParser(description="CF Bot Test Suite")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    VERBOSE = args.verbose

    print("\n  ╔═══════════════════════════════════════════════╗")
    print("  ║  CF Workers AI Farmer — Test Suite (v2)       ║")
    print("  ╚═══════════════════════════════════════════════╝")
    print(f"  Directory: {SCRIPT_DIR}")
    print()

    test_syntax()
    test_imports()
    test_logic()
    test_token_format()
    test_permission_fix()
    test_no_bare_except()
    test_anti_ban()
    test_proxy()
    test_gsuite()
    test_neuron_tracking()
    test_e2e_mock()

    print("\n  " + "═" * 50)
    print(f"  ✅ PASS: {PASS}  |  ❌ FAIL: {FAIL}  |  ⏭️  SKIP: {SKIP}")
    print("  " + "═" * 50)
    if FAIL > 0:
        print("\n  ⚠️  Some tests failed."); sys.exit(1)
    print("\n  🎉 All tests passed!"); sys.exit(0)

if __name__ == "__main__":
    main()
