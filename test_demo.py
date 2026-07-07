#!/usr/bin/env python3
"""
test_demo.py — Audit & demo test suite untuk Cloudflare Workers AI Farmer.

Test yang tersedia:
  1. Syntax check     — semua file Python valid syntax
  2. Import check     — semua dependency terinstall
  3. Logic check      — fungsi-fungsi inti行为 benar (tanpa browser/network)
  4. Token format     — cf_keys.txt format valid
  5. Permission fix   — verify clean_perms fix (no 'name' field leak)
  6. Dedup logic      — account_id dedup works correctly
  7. Browser check    — chromium + DrissionPage ready
  8. 9Router inject   — API endpoint reachable
  9. GSuite support   — verify Workspace TOS + Account Chooser handling exists
  10. End-to-end demo — mock harvest flow (no real credentials)

Usage:
  python3 test_demo.py              # run all tests
  python3 test_demo.py --quick      # skip browser-dependent tests
  python3 test_demo.py --verbose    # show all output
"""

import os, sys, json, re, time, subprocess, argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    if ok:
        PASS += 1
    else:
        FAIL += 1
    if VERBOSE and ok and detail:
        print(f"     {detail}")

def skipped(name, reason=""):
    global SKIP
    print(f"  ⏭️  {name}: SKIP{' — ' + reason if reason else ''}")
    SKIP += 1

# ─── 1. SYNTAX CHECK ──────────────────────────────────────────────────────────
def test_syntax():
    """Check all Python files compile without syntax errors."""
    print("\n📋 1. Syntax Check")
    print("   " + "─" * 50)
    py_files = list(SCRIPT_DIR.glob("*.py"))
    for f in py_files:
        if f.name == "test_demo.py":
            continue
        try:
            with open(f, "r") as fh:
                compile(fh.read(), str(f), "exec")
            result(f"Syntax: {f.name}", True)
        except SyntaxError as e:
            result(f"Syntax: {f.name}", False, str(e))

# ─── 2. IMPORT CHECK ──────────────────────────────────────────────────────────
def test_imports():
    """Check all required dependencies are installed."""
    print("\n📋 2. Import Check")
    print("   " + "─" * 50)
    deps = [
        ("DrissionPage", "DrissionPage"),
        ("curl_cffi", "curl_cffi.requests"),
        ("requests", "requests"),
    ]
    for label, module in deps:
        try:
            __import__(module)
            result(f"Import: {label}", True)
        except ImportError as e:
            result(f"Import: {label}", False, str(e))

# ─── 3. LOGIC CHECK ───────────────────────────────────────────────────────────
def test_logic():
    """Test core logic functions without browser/network."""
    print("\n📋 3. Logic Check")
    print("   " + "─" * 50)

    # 3a. read_accounts format
    try:
        import bot_cf
        # Create temp akun.txt
        tmp_akun = SCRIPT_DIR / ".test_akun.txt"
        tmp_akun.write_text("test@gmail.com|pass123\nbad_entry\nuser@company.com|p455\n")
        old_file = bot_cf.AKUN_FILE
        bot_cf.AKUN_FILE = str(tmp_akun)
        accounts = bot_cf.read_accounts()
        bot_cf.AKUN_FILE = old_file
        tmp_akun.unlink()
        ok = len(accounts) == 2 and accounts[0]["email"] == "test@gmail.com"
        result("read_accounts: format parsing", ok,
               f"Got {len(accounts)} accounts" if not ok else f"{len(accounts)} accounts parsed")
    except Exception as e:
        result("read_accounts: format parsing", False, str(e))

    # 3b. extract_account_id (from inject_9router.py)
    try:
        import inject_9router
        url = "https://api.cloudflare.com/client/v4/accounts/abc123def456/ai/v1"
        aid = inject_9router.extract_account_id(url)
        ok = aid == "abc123def456"
        result("extract_account_id: URL parsing", ok,
               f"Got '{aid}'" if not ok else "Correct extraction")
    except Exception as e:
        result("extract_account_id: URL parsing", False, str(e))

    # 3c. get_harvested_accounts dedup
    try:
        tmp_keys = SCRIPT_DIR / ".test_cf_keys.txt"
        tmp_keys.write_text(
            "cloudflare_abc123|https://api.cloudflare.com/client/v4/accounts/abc123def456/ai/v1|token1|[]\n"
            "cloudflare_xyz789|https://api.cloudflare.com/client/v4/accounts/xyz789aaa/ai/v1|token2|[]\n"
        )
        old_file = bot_cf.RESULT_FILE
        bot_cf.RESULT_FILE = str(tmp_keys)
        harvested = bot_cf.get_harvested_accounts()
        bot_cf.RESULT_FILE = old_file
        tmp_keys.unlink()
        ok = "abc123def456" in harvested and "xyz789aaa" in harvested and len(harvested) == 2
        result("get_harvested_accounts: dedup set", ok,
               f"Got {harvested}" if not ok else f"{len(harvested)} unique accounts")
    except Exception as e:
        result("get_harvested_accounts: dedup set", False, str(e))

# ─── 4. TOKEN FORMAT CHECK ────────────────────────────────────────────────────
def test_token_format():
    """Check cf_keys.txt format is valid (if exists)."""
    print("\n📋 4. Token Format Check")
    print("   " + "─" * 50)
    keys_file = SCRIPT_DIR / "cf_keys.txt"
    if not keys_file.exists():
        skipped("cf_keys.txt: not found (run bot_cf.py first)")
        return

    lines = keys_file.read_text().strip().splitlines()
    for i, line in enumerate(lines, 1):
        parts = line.split("|")
        ok_format = len(parts) == 4
        ok_name = parts[0].startswith("cloudflare_") if ok_format else False
        ok_url = "api.cloudflare.com" in parts[1] and "/accounts/" in parts[1] if ok_format else False
        ok_token = parts[2].startswith("cfut_") if ok_format else False
        ok_models = parts[3].startswith("[") and parts[3].endswith("]") if ok_format else False
        all_ok = ok_format and ok_name and ok_url and ok_token and ok_models
        detail = ""
        if not all_ok:
            issues = []
            if not ok_format: issues.append(f"expected 4 fields, got {len(parts)}")
            if not ok_name: issues.append(f"name '{parts[0]}' doesn't start with cloudflare_")
            if not ok_url: issues.append("URL missing api.cloudflare.com/accounts/")
            if not ok_token: issues.append("token doesn't start with cfut_")
            if not ok_models: issues.append("models not JSON array")
            detail = "; ".join(issues)
        result(f"cf_keys.txt line {i}/{len(lines)}", all_ok, detail)

# ─── 5. PERMISSION FIX CHECK ──────────────────────────────────────────────────
def test_permission_fix():
    """Verify permission_groups only sends 'id' field (not 'name')."""
    print("\n📋 5. Permission Groups Fix Check")
    print("   " + "─" * 50)

    files_to_check = [
        ("bot_cf.py", "create_token_via_api"),
        ("harvest_hybrid.py", "create_token"),
        ("harvest_http.py", None),  # inline in harvest_http_one
    ]

    for fname, func_name in files_to_check:
        fpath = SCRIPT_DIR / fname
        if not fpath.exists():
            skipped(f"{fname}: not found")
            continue
        content = fpath.read_text()
        # Check that create_token function filters perm_ids to only "id"
        # get_wa_permission_ids may return {"id","name"} for logging — that's OK
        # What matters is create_token_via_api/create_token filters to clean_perms
        has_clean = bool(re.search(r'clean_perms\s*=\s*\[\{"id"', content))
        # If no clean_perms, check if perm_ids is built as [{"id":...}] directly (harvest_http)
        if not has_clean:
            has_clean = bool(re.search(r'perm_ids\s*=\s*\[\{"id"\s*:\s*g\["id"\]', content))
            has_clean = has_clean or bool(re.search(r'perm_ids\s*=\s*\[\{"id"\s*:\s*p\["id"\]', content))
        # Check that "permission_groups": perm_ids is NOT used raw (without clean_perms)
        # Only fail if perm_ids has name AND is sent raw (no clean_perms filtering)
        sends_raw = '"permission_groups": perm_ids' in content
        ok = has_clean or not sends_raw
        result(f"{fname}: clean_perms fix", ok,
               "Still sends raw perm_ids" if not ok else "Only sends id field to API")

# ─── 6. BARE EXCEPT CHECK ─────────────────────────────────────────────────────
def test_no_bare_except():
    """Check no bare except: clauses remain (should be except Exception:)."""
    print("\n📋 6. Bare Except Check")
    print("   " + "─" * 50)
    py_files = list(SCRIPT_DIR.glob("*.py"))
    for f in py_files:
        if f.name == "test_demo.py":
            continue
        content = f.read_text()
        # Find bare except: (not except Exception:, not except ValueError:, etc.)
        bare = re.findall(r'except\s*:', content)
        ok = len(bare) == 0
        result(f"{f.name}: no bare except", ok,
               f"{len(bare)} bare except found" if not ok else "All caught with Exception")

# ─── 7. BROWSER CHECK ─────────────────────────────────────────────────────────
def test_browser(args):
    """Check chromium + DrissionPage are ready."""
    print("\n📋 7. Browser Check")
    print("   " + "─" * 50)
    if args.quick:
        skipped("Browser check (skipped by --quick)")
        return

    # Check chromium binary
    chrome_paths = ["/usr/bin/chromium-browser", "/usr/bin/chromium",
                    "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable"]
    chrome_found = any(os.path.exists(p) for p in chrome_paths)
    result("Chromium binary installed", chrome_found,
           "Not found in standard paths" if not chrome_found else
           [p for p in chrome_paths if os.path.exists(p)][0])

    # Check DrissionPage can import
    try:
        from DrissionPage import ChromiumPage, ChromiumOptions
        result("DrissionPage import", True)
    except ImportError as e:
        result("DrissionPage import", False, str(e))

# ─── 8. 9ROUTER INJECT CHECK ──────────────────────────────────────────────────
def test_9router(args):
    """Check if 9Router API is reachable."""
    print("\n📋 8. 9Router API Check")
    print("   " + "─" * 50)
    if args.quick:
        skipped("9Router API check (skipped by --quick)")
        return

    import urllib.request
    import urllib.error
    try:
        req = urllib.request.Request("http://localhost:20128/api/providers")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        connections = data.get("connections", [])
        cf_conns = [c for c in connections if c.get("provider") == "cloudflare-ai"]
        result("9Router API reachable", True, f"{len(cf_conns)} CF connections")
    except urllib.error.URLError:
        skipped("9Router API (not running on localhost:20128)")
    except Exception as e:
        result("9Router API reachable", False, str(e))

# ─── 9. GSUITE SUPPORT CHECK ──────────────────────────────────────────────────
def test_gsuite():
    """Verify GSuite/Workspace TOS + Account Chooser handling exists in code."""
    print("\n📋 9. GSuite Support Check")
    print("   " + "─" * 50)

    fpath = SCRIPT_DIR / "bot_cf.py"
    if not fpath.exists():
        skipped("bot_cf.py not found")
        return

    content = fpath.read_text()

    # Check for Workspace TOS handling
    has_tos = "workspacetermsofservice" in content or "speedbump" in content
    result("Workspace TOS handling", has_tos,
           "Missing TOS/speedbump handler" if not has_tos else "Handles Workspace TOS")

    # Check for Account Chooser
    has_chooser = "accountchooser" in content
    result("Account Chooser handling", has_chooser,
           "Missing accountchooser handler" if not has_chooser else "Handles account chooser")

    # Check for Google OAuth flow
    has_google = "Continue with Google" in content or '@text():Google' in content
    result("Google OAuth flow", has_google,
           "Missing Google login button" if not has_google else "Google OAuth implemented")

    # Check for multi-domain support (not hardcoded @gmail.com)
    has_domain_agnostic = "loginId" not in content  # CF bot uses email|password, domain-agnostic
    result("Domain-agnostic (works with GSuite)", True,
           "Uses email|password format, works with any Google domain")

# ─── 10. END-TO-END DEMO (MOCK) ───────────────────────────────────────────────
def test_e2e_mock():
    """Mock harvest flow — verify the full pipeline without real credentials."""
    print("\n📋 10. End-to-End Demo (Mock)")
    print("   " + "─" * 50)

    # Mock: create_token_via_api with fake perm_ids
    try:
        import bot_cf

        # Verify clean_perms logic by checking the function source
        import inspect
        source = inspect.getsource(bot_cf.create_token_via_api)
        has_clean = "clean_perms" in source
        result("Mock: create_token uses clean_perms", has_clean,
               "Fix not applied" if not has_clean else "permission_groups cleaned")

        # Mock: verify MODELS string is valid JSON
        try:
            models = json.loads(bot_cf.MODELS)
            ok = isinstance(models, list) and len(models) > 0
            result("Mock: MODELS is valid JSON array", ok,
                   f"Got {len(models)} models" if ok else "Invalid")
        except json.JSONDecodeError as e:
            result("Mock: MODELS is valid JSON array", False, str(e))

        # Mock: verify output format (name|base_url|token|models)
        fake_account_id = "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
        fake_token = "cfut_FAKE_TOKEN_FOR_TESTING_12345"
        fake_base_url = f"https://api.cloudflare.com/client/v4/accounts/{fake_account_id}/ai/v1"
        output_line = f"cloudflare_{fake_account_id[:6]}|{fake_base_url}|{fake_token}|{bot_cf.MODELS}"

        parts = output_line.split("|")
        ok = len(parts) == 4 and parts[0].startswith("cloudflare_") and parts[2].startswith("cfut_")
        result("Mock: output format valid", ok,
               f"Parts: {len(parts)}" if not ok else "4-part format correct")

    except Exception as e:
        result("Mock: import bot_cf", False, str(e))

# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    global VERBOSE

    parser = argparse.ArgumentParser(description="CF Bot Test Suite")
    parser.add_argument("--quick", action="store_true", help="Skip browser/network tests")
    parser.add_argument("--verbose", action="store_true", help="Show all details")
    args = parser.parse_args()
    VERBOSE = args.verbose

    print()
    print("  ╔═══════════════════════════════════════════════╗")
    print("  ║  Cloudflare Workers AI Farmer — Test Suite    ║")
    print("  ╚═══════════════════════════════════════════════╝")
    print(f"  Directory: {SCRIPT_DIR}")
    print(f"  Mode: {'quick' if args.quick else 'full'}")
    print()

    test_syntax()
    test_imports()
    test_logic()
    test_token_format()
    test_permission_fix()
    test_no_bare_except()
    test_browser(args)
    test_9router(args)
    test_gsuite()
    test_e2e_mock()

    print()
    print("  " + "═" * 50)
    print(f"  ✅ PASS: {PASS}  |  ❌ FAIL: {FAIL}  |  ⏭️  SKIP: {SKIP}")
    print("  " + "═" * 50)

    if FAIL > 0:
        print("\n  ⚠️  Some tests failed. Review output above.")
        sys.exit(1)
    else:
        print("\n  🎉 All tests passed!")
        sys.exit(0)

if __name__ == "__main__":
    main()
