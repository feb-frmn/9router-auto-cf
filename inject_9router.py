#!/usr/bin/env python3
"""
Cloudflare Workers AI -> 9Router Injector
Reads cf_keys.txt → injects each token as a connection on the 'cfai' provider node.

9Router routes by prefix: user sends cfai/@cf/zai-org/glm-5.2
  → 9Router strips 'cfai/' → forwards @cf/zai-org/glm-5.2 to CF endpoint

Each CF account has a unique account_id → unique base URL.
All connections go on the same node (prefix=cfai), each with its own baseUrl + apiKey.
9Router load-balances across connections (proven by antigravity having 55 connections).

Prefix: cfai (user model format: cfai/@cf/zai-org/glm-5.2)
"""

import os, sys, subprocess, json, sqlite3

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(SCRIPT_DIR, "cf_keys.txt")
ADD_SCRIPT = os.path.expanduser("~/9router-add-provider/add-provider-db.sh")
DB_PATH = "/var/lib/9router/db/data.sqlite"
NODE_NAME = "cf-workers-ai"
PREFIX = "cfai"

def get_existing_keys():
    """Get all API keys already in 9Router DB for the cfai provider."""
    if not os.path.exists(DB_PATH):
        return set()
    try:
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT json_extract(c.data, '$.apiKey') FROM providerConnections c "
            "JOIN providerNodes n ON c.provider = n.id "
            "WHERE json_extract(n.data, '$.prefix') = ?", (PREFIX,)
        ).fetchall()
        conn.close()
        return {r[0] for r in rows if r[0]}
    except Exception as e:
        print(f"  ⚠️ Gagal baca DB: {e}")
        return set()

def inject_to_9router():
    if not os.path.exists(KEY_FILE):
        print(f"❌ {KEY_FILE} belum ada. Jalankan bot_cf.py dulu.")
        return

    if not os.path.exists(ADD_SCRIPT):
        print(f"❌ {ADD_SCRIPT} belum ada.")
        print("   Clone: git clone https://github.com/feb-frmn/9router-add-provider ~/9router-add-provider")
        return

    with open(KEY_FILE, "r") as f:
        lines = [l.strip() for l in f if l.strip()]

    if not lines:
        print("❌ cf_keys.txt kosong.")
        return

    # Get existing keys to avoid duplicates
    existing_keys = get_existing_keys()
    print(f"📋 Ditemukan {len(lines)} key di cf_keys.txt")
    print(f"📋 Sudah ada {len(existing_keys)} key di 9Router DB")
    print(f"📋 Prefix: {PREFIX} | Node: {NODE_NAME}")
    print()

    success = 0
    skipped = 0
    failed = 0

    for i, line in enumerate(lines, 1):
        parts = line.split("|")
        if len(parts) != 4:
            print(f"  [{i}] ⏭️ Skip (format salah): {line[:60]}")
            failed += 1
            continue

        name, base_url, api_key, models_json = parts

        # Dedup: skip if key already in DB
        if api_key in existing_keys:
            print(f"  [{i}] ⏭️ Skip (sudah ada): {name} → {api_key[:20]}...")
            skipped += 1
            continue

        print(f"  [{i}] Injecting {name}...")
        print(f"       URL: {base_url}")
        print(f"       Key: {api_key[:25]}...")

        result = subprocess.run([
            "bash", ADD_SCRIPT,
            "--name", NODE_NAME,
            "--prefix", PREFIX,
            "--url", base_url,
            "--key", api_key
        ], capture_output=True, text=True)

        if "Done!" in result.stdout:
            success += 1
            existing_keys.add(api_key)  # prevent dupes within same run
            print(f"       ✅ Berhasil")
        elif "already exists" in result.stdout:
            # This means the key was already added (shouldn't happen with dedup check above)
            skipped += 1
            print(f"       ⏭️ Sudah ada (via script)")
        else:
            failed += 1
            print(f"       ❌ Gagal: {result.stdout[:150]}")
            if result.stderr:
                print(f"       stderr: {result.stderr[:150]}")

    print(f"\n{'='*55}")
    print(f" ✅ Inject: {success} | ⏭️ Skip: {skipped} | ❌ Fail: {failed}")
    print(f" Total connections: {success + skipped} (existing) + {success} (new)")
    print(f" Model format: {PREFIX}/@cf/zai-org/glm-5.2")
    print(f" Restart 9router: systemctl restart 9router")
    print(f"{'='*55}")

if __name__ == "__main__":
    inject_to_9router()
