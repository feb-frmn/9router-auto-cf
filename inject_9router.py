#!/usr/bin/env python3
"""
inject_9router.py v2 — Inject harvested Cloudflare tokens into 9Router.

Uses 9Router's BUILT-IN cloudflare-ai provider.
No custom provider needed — just POST connections via the API.

Flow:
  1. harvest_hybrid.py harvests tokens → cf_keys.txt
  2. This script reads cf_keys.txt → POST /api/providers for each
  3. 9Router auto-discovers all models (20+ LLM, image, etc.)
  4. Use: cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast

Requires: 9Router running on localhost:20128

Usage:
  python3 inject_9router.py           # inject all keys from cf_keys.txt
  python3 inject_9router.py --dry-run # preview without injecting
"""
import os, sys, json, re, argparse, urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_FILE = os.path.join(SCRIPT_DIR, "cf_keys.txt")
NINEROUTER_API = "http://localhost:20128"
PROVIDER = "cloudflare-ai"

EXTRA_MODELS = [
    {"id": "@cf/zai-org/glm-5.2", "name": "GLM 5.2"},
]

BANNER = f"""
\033[36m╔══════════════════════════════════════════════════════╗
║  \033[1;37m🔗 9Router CF Injector v2\033[0;36m                             ║
║  \033[2mInject CF tokens → 9Router load-balanced connections\033[0;36m ║
╚══════════════════════════════════════════════════════╝\033[0m
  \033[2m☕ https://saweria.co/febfrmn\033[0m
"""


def extract_account_id(base_url):
    m = re.search(r'/accounts/([a-f0-9]+)/', base_url)
    return m.group(1) if m else None


def get_existing_connections():
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
        print(f"  ⚠️  Failed to check existing connections: {e}")
        return {}


def add_connection(api_key, account_id, name, dry_run=False):
    if dry_run:
        return True, "dry-run"
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


def register_extra_models(dry_run=False):
    if not EXTRA_MODELS:
        return
    print(f"\n📋 Register {len(EXTRA_MODELS)} extra model(s)...")
    for model in EXTRA_MODELS:
        if dry_run:
            print(f"  [DRY] {PROVIDER}/{model['id']} → {model['name']}")
            continue
        payload = json.dumps({
            "providerAlias": PROVIDER,
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
                print(f"  ✅ {PROVIDER}/{model['id']} → {model['name']}")
            else:
                print(f"  ⚠️  {model['id']}: {result}")
        except Exception as e:
            print(f"  ❌ {model['id']}: {e}")


def inject(dry_run=False):
    print(BANNER)

    if not os.path.exists(KEY_FILE):
        print(f"❌ {KEY_FILE} not found. Run harvest_hybrid.py first.")
        return

    with open(KEY_FILE, "r") as f:
        lines = [l.strip() for l in f if l.strip()]

    if not lines:
        print("❌ cf_keys.txt is empty.")
        return

    existing = get_existing_connections()
    print(f"📋 {len(lines)} keys in cf_keys.txt")
    print(f"📋 {len(existing)} connections already in 9Router")
    print(f"📋 Provider: {PROVIDER} (built-in)")
    if dry_run:
        print("📋 DRY RUN — no changes will be made")
    print()

    success = 0
    skipped = 0
    failed = 0

    for i, line in enumerate(lines, 1):
        parts = line.split("|")
        if len(parts) != 4:
            print(f"  [{i}] ⏭️  Skip (bad format): {line[:60]}")
            failed += 1
            continue

        name, base_url, api_key, models_json = parts
        account_id = extract_account_id(base_url)

        if not account_id:
            print(f"  [{i}] ❌ Failed to extract account_id: {base_url[:60]}")
            failed += 1
            continue

        # Dedup
        if account_id in existing:
            print(f"  [{i}] ⏭️  Skip (already exists): {account_id[:12]}...")
            skipped += 1
            continue

        print(f"  [{i}] Adding {name}...")
        print(f"       Account: {account_id[:16]}...")
        print(f"       Key: {api_key[:25]}...")

        ok, result = add_connection(api_key, account_id, name, dry_run=dry_run)
        if ok:
            success += 1
            existing[account_id] = result
            print(f"       ✅ Added (id: {result[:12]})")
        else:
            failed += 1
            print(f"       ❌ Failed: {result}")

    # Register extra models
    register_extra_models(dry_run=dry_run)

    print(f"\n{'='*55}")
    print(f" ✅ Added: {success} | ⏭️  Skipped: {skipped} | ❌ Failed: {failed}")
    print(f" Total connections: {len(existing)}")
    print(f" Prefix: cf/  (example: cf/@cf/meta/llama-3.3-70b-instruct-fp8-fast)")
    print(f"{'='*55}")
    print(f"  ☕ https://saweria.co/febfrmn\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="9Router CF Token Injector v2")
    ap.add_argument("--dry-run", action="store_true", help="Preview without injecting")
    args = ap.parse_args()
    inject(dry_run=args.dry_run)
