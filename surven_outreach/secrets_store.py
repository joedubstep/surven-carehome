"""One place for the campaign's keys (9/29, move to the cloud).
Cloud (GitHub Actions): every value comes from the JSON in the CAREHOME_SECRETS env var (a GitHub secret).
Joey's PC: read from the PIA folder, exactly as the scripts did before. Never print a value."""
import json
import os
import re

PIA_DIR = r"C:\Users\evann\Desktop\PIA"
_CLOUD = json.loads(os.environ["CAREHOME_SECRETS"].lstrip("﻿")) if os.environ.get("CAREHOME_SECRETS") else None


def _pias() -> str:
    return open(os.path.join(PIA_DIR, "PIAs.txt"), encoding="utf-8", errors="ignore").read()


def _local(name):
    if name == "GHL_PIT":  # first pit- token on PIAs.txt line 15
        return re.search(r"pit-[\w-]+", _pias().split("\n")[14]).group(0)
    if name == "NOTION_TOKEN":
        return [l for l in _pias().splitlines() if "Notion general Token" in l][0].split(":", 1)[1].strip()
    if name == "IMPROVMX_API_KEY":
        line = [l for l in _pias().splitlines() if l.lower().startswith("improvmx api key")][0]
        return re.search(r"sk_[A-Za-z0-9]+", line).group(0)
    if name == "IMPROVMX_SENDERS":  # {"joey@x.xyz": "smtp password", ...}
        p = os.path.join(PIA_DIR, "improvmx-senders.json")
        return json.load(open(p)) if os.path.exists(p) else {}
    if name == "GOOGLE_CLIENT_ID":
        return re.search(r"[\w-]+\.apps\.googleusercontent\.com", _pias()).group(0)
    if name == "GOOGLE_CLIENT_SECRET":
        return re.search(r"GOCSPX-[\w-]+", _pias()).group(0)
    if name == "GOOGLE_SHEETS_REFRESH_TOKEN":
        return json.load(open(os.path.join(PIA_DIR, "google-sheets-oauth.json")))["refresh_token"]
    raise KeyError(name)


def get(name):
    return _CLOUD[name] if _CLOUD is not None else _local(name)


def all_for_cloud() -> dict:
    """Everything the cloud needs, for uploading as the CAREHOME_SECRETS GitHub secret."""
    return {k: _local(k) for k in ("GHL_PIT", "NOTION_TOKEN", "IMPROVMX_API_KEY", "IMPROVMX_SENDERS",
                                   "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_SHEETS_REFRESH_TOKEN")}
