#!/usr/bin/env python3
"""
Cloudflare Workers AI -> 9Router Injector
Reads cf_keys.txt → injects each token as a connection on the 'cfai' provider node.
Auto-registers all CF models in 9Router so they appear in /v1/models.

Flow:
  1. Create/update provider node (prefix=cfai)
  2. Add connections (one per account, each with own baseUrl + apiKey)
  3. Register models via /api/models/custom (so they show up in model list)
  4. Restart 9Router

Model format: cfai/@cf/zai-org/glm-5.2
"""

import os, sys, subprocess, json, sqlite3, urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(SCRIPT_DIR, "cf_keys.txt")
ADD_SCRIPT = os.path.expanduser("~/9router-add-provider/add-provider-db.sh")
DB_PATH = "/var/lib/9router/db/data.sqlite"
NODE_NAME = "cf-workers-ai"
PREFIX = "cfai"
NINEROUTER_API = "http://localhost:20128"

# All CF Workers AI models to register
CF_MODELS = [
    {"id": "@cf/zai-org/glm-5.2", "name": "GLM 5.2"},
    {"id": "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b", "name": "DeepSeek R1 32B"},
    {"id": "@cf/meta/llama-3.3-70b-instruct-fp8-fast", "name": "Llama 3.3 70B"},
    {"id": "@cf/meta/llama-3.1-70b-instruct", "name": "Llama 3.1 70B"},
    {"id": "@cf/qwen/qwen2.5-coder-32b-instruct", "name": "Qwen 2.5 Coder 32B"},
]

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
        print(f"  ⚠️  Gagal baca DB: {e}")
        return set()

def register_models():
    """Register all CF models in 9Router via /api/models/custom."""
    print(f"\n📋 Register {len(CF_MODELS)} model di 9Router...")
    success = 0
    for model in CF_MODELS:
        payload = json.dumps({
            "providerAlias": PREFIX,
            "id": model["id"],
            "type": "llm",
            "name": model["name"]
        }).encode()
        try:
            req = urllib.request.Request(
                f"{NINEROUTER_API}/api/models/custom",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read())
                if result.get("success"):
                    success += 1
                    print(f"  ✅ {PREFIX}/{model['id']} → {model['name']}")
                else:
                    print(f"  ⚠️  {model['id']}: {result}")
        except Exception as e:
            print(f"  ❌ {model['id']}: {e}")
    print(f"  → {success}/{len(CF_MODELS)} model terdaftar")

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
            print(f"  [{i}] ⏭️  Skip (format salah): {line[:60]}")
            failed += 1
            continue

        name, base_url, api_key, models_json = parts

        if api_key in existing_keys:
            print(f"  [{i}] ⏭️  Skip (sudah ada): {name} → {api_key[:20]}...")
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
            existing_keys.add(api_key)
            print(f"       ✅ Berhasil")
        elif "already exists" in result.stdout:
            skipped += 1
            print(f"       ⏭️  Sudah ada (via script)")
        else:
            failed += 1
            print(f"       ❌ Gagal: {result.stdout[:150]}")
            if result.stderr:
                print(f"       stderr: {result.stderr[:150]}")

    # Auto-register models
    register_models()

    # Restart 9Router if any changes were made
    if success > 0:
        print(f"\n🔄 Restart 9Router...")
        subprocess.run(["systemctl", "restart", "9router"], capture_output=True)
        print(f"   ✅ 9Router restarted")

    print(f"\n{'='*55}")
    print(f" ✅ Inject: {success} | ⏭️  Skip: {skipped} | ❌ Fail: {failed}")
    print(f" Total connections: {success + skipped + len(existing_keys)}")
    print(f" Models: {', '.join(m['id'] for m in CF_MODELS)}")
    print(f" Format: {PREFIX}/@cf/zai-org/glm-5.2")
    print(f"{'='*55}")

if __name__ == "__main__":
    inject_to_9router()
