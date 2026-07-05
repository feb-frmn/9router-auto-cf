#!/usr/bin/env python3
"""
9Router Auto CF — Inject harvested Cloudflare tokens into 9Router.

Uses 9Router's BUILT-IN cloudflare-ai provider.
No custom provider needed — just POST connections via the API.

Flow:
  1. bot_cf.py harvests tokens → cf_keys.txt
  2. This script reads cf_keys.txt → POST /api/providers for each
  3. 9Router auto-discovers all models (20+ LLM, image, etc.)
  4. Use: cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast

Requires: 9Router running on localhost:20128
"""

import os, sys, json, re, urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(SCRIPT_DIR, "cf_keys.txt")
NINEROUTER_API = "http://localhost:20128"

PROVIDER = "cloudflare-ai"

def extract_account_id(base_url):
    """Extract account_id from CF base URL."""
    m = re.search(r'/accounts/([a-f0-9]+)/', base_url)
    return m.group(1) if m else None

def get_existing_connections():
    """Get existing cloudflare-ai connections from 9Router."""
    try:
        req = urllib.request.Request(f"{NINEROUTER_API}/api/providers")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        existing = {}
        for c in data.get("connections", []):
            if c.get("provider") == PROVIDER:
                acct = c.get("providerSpecificData", {}).get("accountId", "")
                if acct:
                    existing[acct] = c.get("id", "")
        return existing
    except Exception as e:
        print(f"  ⚠️  Gagal cek existing connections: {e}")
        return {}

def add_connection(api_key, account_id, name):
    """Add a cloudflare-ai connection via 9Router API."""
    payload = json.dumps({
        "provider": PROVIDER,
        "apiKey": api_key,
        "name": name,
        "providerSpecificData": {
            "accountId": account_id
        }
    }).encode()
    try:
        req = urllib.request.Request(
            f"{NINEROUTER_API}/api/providers",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
        if result.get("connection", {}).get("id"):
            return True, result["connection"]["id"]
        return False, result.get("error", "unknown error")
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        try:
            msg = json.loads(body).get("error", body[:100])
        except:
            msg = body[:100]
        return False, msg
    except Exception as e:
        return False, str(e)

def inject():
    if not os.path.exists(KEY_FILE):
        print(f"❌ {KEY_FILE} belum ada. Jalankan bot_cf.py dulu.")
        return

    with open(KEY_FILE, "r") as f:
        lines = [l.strip() for l in f if l.strip()]

    if not lines:
        print("❌ cf_keys.txt kosong.")
        return

    existing = get_existing_connections()
    print(f"📋 {len(lines)} key di cf_keys.txt")
    print(f"📋 {len(existing)} connection sudah ada di 9Router")
    print(f"📋 Provider: {PROVIDER} (built-in)")
    print()

    success = 0
    skipped = 0
    failed = 0

    for i, line in enumerate(lines, 1):
        parts = line.split("|")
        if len(parts) != 4:
            print(f"  [{i}] ⏭️  Skip (format salah): {line[:60]}")
            failed += 1
            continue

        name, base_url, api_key, models_json = parts
        account_id = extract_account_id(base_url)

        if not account_id:
            print(f"  [{i}] ❌ Gagal extract account_id dari: {base_url[:60]}")
            failed += 1
            continue

        # Dedup
        if account_id in existing:
            print(f"  [{i}] ⏭️  Skip (sudah ada): {account_id[:12]}...")
            skipped += 1
            continue

        print(f"  [{i}] Adding {name}...")
        print(f"       Account: {account_id[:16]}...")
        print(f"       Key: {api_key[:25]}...")

        ok, result = add_connection(api_key, account_id, name)
        if ok:
            success += 1
            existing[account_id] = result
            print(f"       ✅ Berhasil (id: {result[:12]})")
        else:
            failed += 1
            print(f"       ❌ Gagal: {result}")

    print(f"\n{'='*55}")
    print(f" ✅ Added: {success} | ⏭️  Skip: {skipped} | ❌ Fail: {failed}")
    print(f" Total connections: {len(existing)}")
    print(f" Prefix: cf/  (example: cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast)")
    print(f"{'='*55}")

if __name__ == "__main__":
    inject()
