"""Copy the desk's dashboard files to trade.riosventures.org (Cloudflare KV, read by site/worker.js).

Runs after every desk run (desk.yml). Only files that changed since the last copy are sent, to stay well
inside KV's free writes. Without CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID it does nothing, and any
failure is printed, never raised: the site must never stop the desk.

    python site_publish.py            # the desk's files in the desk folder
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
import urllib.request

from common import load_json, path, save_json

KV_NAMESPACE = "4bd0b89266be4f72ad1e73231e524459"  # spy-desk-site-data (site/wrangler.jsonc)
MANIFEST = "site_published.json"  # name -> sha256 of what the site holds
FILES = ["dashboard_state.json", "sentiment_state.json", "tests_state.json", "account.json", "crypto_state.json"]
CHARTS = "chart_*.json"


def changed_files() -> dict[str, str]:
    """name -> text for every site file whose content differs from the last copy."""
    sent = load_json(MANIFEST, {}) or {}
    names = FILES + sorted(os.path.basename(p) for p in glob.glob(str(path(CHARTS))))
    out = {}
    for name in names:
        p = path(name)
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        if sent.get(name) != hashlib.sha256(text.encode()).hexdigest():
            out[name] = text
    return out


def put_bulk(files: dict[str, str], token: str, account: str, opener=urllib.request.urlopen) -> None:
    body = json.dumps([{"key": k, "value": v} for k, v in files.items()]).encode()
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account}/storage/kv/namespaces/{KV_NAMESPACE}/bulk",
        data=body, method="PUT", headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with opener(req, timeout=30) as r:
        res = json.loads(r.read().decode() or "{}")
    if not res.get("success", False):
        raise RuntimeError(f"Cloudflare said no: {res.get('errors')}")


def publish(env=os.environ, opener=urllib.request.urlopen) -> str:
    token, account = env.get("CLOUDFLARE_API_TOKEN"), env.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        return "skipped: no Cloudflare secrets"
    files = changed_files()
    if not files:
        return "nothing changed"
    try:
        put_bulk(files, token, account, opener)
    except Exception as e:  # noqa: BLE001 - the site must never stop the desk
        return f"failed: {type(e).__name__}: {str(e)[:200]}"
    sent = load_json(MANIFEST, {}) or {}
    sent.update({k: hashlib.sha256(v.encode()).hexdigest() for k, v in files.items()})
    save_json(MANIFEST, sent)
    return f"sent {len(files)}: {', '.join(sorted(files))}"


if __name__ == "__main__":
    print("trade.riosventures.org:", publish())
    sys.exit(0)
