"""Upload the campaign keys to GitHub as secrets (CAREHOME_SECRETS = JSON, DOTENV = .env). Run from Joey's PC after
any key changes. Goes straight from Python to `gh`, so no shell re-encoding (PowerShell pipes add a BOM) and no printing."""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from surven_outreach import secrets_store  # noqa: E402

REPO = "joedubstep/surven-carehome"
vals = {"CAREHOME_SECRETS": json.dumps(secrets_store.all_for_cloud()),
        "DOTENV": open(os.path.join(ROOT, ".env"), encoding="utf-8-sig").read()}
for name, body in vals.items():
    r = subprocess.run(["gh", "secret", "set", name, "-R", REPO], input=body.encode("utf-8"), capture_output=True)
    print(name, "set" if r.returncode == 0 else f"FAILED {r.stderr.decode(errors='ignore')[:200]}")
