import csv
import os
import re

import pandas as pd

FREE_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "aol.com", "outlook.com", "icloud.com",
}


def _email_valid(v) -> bool:
    if not isinstance(v, str):
        return False
    v = v.strip()
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", v)) and v != "0"


def _looks_corrupt(company) -> bool:
    if not isinstance(company, str):
        return False
    s = company.strip()
    # numeric (lat/long bled in) or JSON-ish garbage
    return bool(re.fullmatch(r"-?\d+(\.\d+)?", s)) or s.startswith("{")


def _city(address) -> str:
    # Outscraper address ~ "12 Main St, Newark, NJ" -> second-to-last comma field
    if not isinstance(address, str):
        return ""
    parts = [p.strip() for p in address.split(",") if p.strip()]
    return parts[-2] if len(parts) >= 2 else ""


_LEGAL = re.compile(
    r"[,\s]+(?:llc|l\.l\.c\.?|inc\.?|incorporated|corp\.?|corporation|ltd\.?|llp)\.?$",
    re.I,
)


def _clean_business(name) -> str:
    # drop trailing legal suffixes (LLC, Inc, Corp, Ltd, LLP) so it reads human in the email
    n = (name or "").strip()
    prev = None
    while prev != n:
        prev = n
        n = _LEGAL.sub("", n).strip().rstrip(",").strip()
    return n or (name or "").strip()


def _clean_city(city) -> str:
    # "Weehawken Township" -> "Weehawken"
    return re.sub(r"\s+(?:township|twp\.?)$", "", city or "", flags=re.I).strip()


def _domain(url) -> str:
    if not isinstance(url, str):
        return ""
    return re.sub(r"^https?://(www\.)?", "", url.strip()).split("/")[0].lower()


def _score(row: dict) -> int:
    s = 0
    if row["first_name"]:
        s += 2
    if row["about"]:
        s += 2
    edom = row["email"].split("@")[-1].lower()
    if edom and edom not in FREE_EMAIL_DOMAINS and edom in _domain(row["website"]):
        s += 3  # domain-matched email = strongest signal
    if row["website"]:
        s += 1
    return s


def clean_prospects(csv_path: str) -> list[dict]:
    df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        email = (r.get("email") or "").strip()
        if not _email_valid(email):
            continue
        if _looks_corrupt(r.get("company_name", "")):
            continue
        business = _clean_business(r.get("name") or r.get("company_name") or "")
        if not business:
            continue
        row = {
            "email": email.lower(),
            "business": business,
            "first_name": (r.get("first_name") or "").strip(),
            "category": (r.get("category") or r.get("subtypes") or "").strip(),
            "city": _clean_city(_city(r.get("address", ""))),
            "website": (r.get("website") or "").strip(),
            "about": (r.get("about") or r.get("description") or "").strip(),
        }
        row["personalization_score"] = _score(row)
        # dedupe by email, keep the richer (higher score) row
        prev = out.get(row["email"])
        if prev is None or row["personalization_score"] > prev["personalization_score"]:
            out[row["email"]] = row
    # second pass: ONE contact per business (group by website domain, else business name),
    # keeping the highest-scoring contact (score already favors a real owner name + about)
    by_biz: dict[str, dict] = {}
    for row in out.values():
        key = _domain(row["website"]) or row["business"].strip().lower()
        prev = by_biz.get(key)
        if prev is None or row["personalization_score"] > prev["personalization_score"]:
            by_biz[key] = row
    return sorted(by_biz.values(), key=lambda x: x["personalization_score"], reverse=True)


def apply_suppression(rows: list[dict], suppressed: set[str]) -> list[dict]:
    s = {e.strip().lower() for e in suppressed}
    return [r for r in rows if r["email"].lower() not in s]


def load_suppression(path: str) -> set[str]:
    if not os.path.exists(path):
        return set()
    with open(path, newline="", encoding="utf-8") as f:
        return {row[0].strip().lower() for row in csv.reader(f) if row and "@" in row[0]}
