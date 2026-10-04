"""Set up the cold-email sender domains (Joey bought 8 .xyz on Vercel 9/29 to 10x the care-home campaign).
For each domain, idempotent:
  1. ImprovMX: add the domain, alias joey@ -> survengeo@gmail.com (replies land in the same inbox), SMTP login joey@
  2. Vercel DNS: MX, SPF, DKIM (ImprovMX CNAMEs), DMARC, same as surven.ai
  3. check: asks ImprovMX whether the records are live
SMTP passwords are generated here and saved to PIA\\improvmx-senders.json. Never printed.
Usage: python -m surven_outreach.sender_domains [setup|check]"""
import base64, json, os, re, secrets, sys, urllib.request

DOMAINS = ["getsurven.xyz", "trysurven.xyz", "survenoccupancy.xyz", "survenhq.xyz",
           "joinsurven.xyz", "meetsurven.xyz", "gosurven.xyz", "usesurven.xyz",
           "survenoccupancy.com"]  # 10/4: .com test sender (the .xyz ones land in Gmail spam)
FORWARD_TO = "survengeo@gmail.com"
SECRETS = r"C:\Users\evann\Desktop\PIA\improvmx-senders.json"
TEAM = "team_Llp8VPs47TdN4ch2CrL23zag"
RECORDS = [  # mirrors surven.ai's mail records
    {"name": "", "type": "MX", "value": "mx1.improvmx.com", "mxPriority": 10},
    {"name": "", "type": "MX", "value": "mx2.improvmx.com", "mxPriority": 20},
    {"name": "", "type": "TXT", "value": "v=spf1 include:spf.improvmx.com ~all"},
    {"name": "dkimprovmx1._domainkey", "type": "CNAME", "value": "dkimprovmx1.improvmx.com"},
    {"name": "dkimprovmx2._domainkey", "type": "CNAME", "value": "dkimprovmx2.improvmx.com"},
    {"name": "_dmarc", "type": "TXT", "value": "v=DMARC1; p=none"},
]


def _imx_key():
    from . import secrets_store  # PIA files on Joey's PC, GitHub secret in the cloud
    return secrets_store.get("IMPROVMX_API_KEY")


def _vercel_token():
    return json.load(open(os.path.join(os.environ["APPDATA"], "com.vercel.cli", "Data", "auth.json")))["token"]


def call(method, url, body=None, headers=None):
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "surven-outreach", **(headers or {})})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read() or b"{}")
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "_body": e.read()[:300].decode(errors="ignore")}


def imx(method, path, body=None):
    auth = "Basic " + base64.b64encode(f"api:{_imx_key()}".encode()).decode()
    return call(method, "https://api.improvmx.com/v3" + path, body, {"Authorization": auth})


def vercel(method, path, body=None):
    sep = "&" if "?" in path else "?"
    return call(method, f"https://api.vercel.com{path}{sep}teamId={TEAM}", body, {"Authorization": "Bearer " + _vercel_token()})


def setup():
    creds = json.load(open(SECRETS)) if os.path.exists(SECRETS) else {}
    have = {d["domain"] for d in imx("GET", "/domains?limit=100").get("domains", [])}
    for d in DOMAINS:
        out = []
        if d not in have:
            r = imx("POST", "/domains", {"domain": d})
            out.append("imx domain " + ("added" if r.get("success") else f"ERR {r}"))
        aliases = {a["alias"]: a["forward"] for a in imx("GET", f"/domains/{d}/aliases").get("aliases", [])}
        if "joey" not in aliases:
            r = imx("POST", f"/domains/{d}/aliases", {"alias": "joey", "forward": FORWARD_TO})
            out.append("alias " + ("added" if r.get("success") else f"ERR {r}"))
        users = [c.get("username") for c in imx("GET", f"/domains/{d}/credentials").get("credentials", [])]
        if "joey" not in users or f"joey@{d}" not in creds:
            pw = secrets.token_urlsafe(24)
            if "joey" in users:  # login exists but we lost the password: reset it
                r = imx("PUT", f"/domains/{d}/credentials/joey", {"password": pw})
            else:
                r = imx("POST", f"/domains/{d}/credentials", {"username": "joey", "password": pw})
            if r.get("success"):
                creds[f"joey@{d}"] = pw
                json.dump(creds, open(SECRETS, "w"), indent=1)
                out.append("smtp login ready")
            else:
                out.append(f"smtp ERR {r}")
        existing = vercel("GET", f"/v4/domains/{d}/records?limit=100").get("records", [])
        keys = {(x["name"], x["type"], x["value"]) for x in existing}
        added = 0
        for rec in RECORDS:
            if (rec["name"], rec["type"], rec["value"]) in keys:
                continue
            r = vercel("POST", f"/v2/domains/{d}/records", {**rec, "ttl": 3600})
            if r.get("_error"):
                out.append(f"dns ERR {rec['type']} {rec['name'] or '@'}: {r}")
            else:
                added += 1
        out.append(f"dns +{added}")
        print(d, "|", "; ".join(out))


SENDERS_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "carehome", "senders.json")


def check(quiet=False):
    """Ask ImprovMX if each domain's MX/SPF/DKIM/DMARC are live. The first day all pass, the domain is marked
    ready in senders.json (carehome.py only sends from ready domains and ramps from that date)."""
    from datetime import date
    meta = json.load(open(SENDERS_FILE)) if os.path.exists(SENDERS_FILE) else {}
    for d in DOMAINS:
        addr = f"joey@{d}"
        if meta.get(addr, {}).get("ready"):
            continue
        rec = imx("GET", f"/domains/{d}/check").get("records", {})
        ok = all((rec.get(k) or {}).get("valid") is True for k in ("mx", "spf", "dkim1", "dkim2"))
        meta.setdefault(addr, {})["ready"] = ok
        if ok:
            meta[addr]["ready_on"] = date.today().isoformat()
        if not quiet:
            print(d, "| ready" if ok else f"| not yet: {({k: (rec.get(k) or {}).get('valid') for k in ('mx', 'spf', 'dkim1', 'dkim2', 'dmarc')})}")
    json.dump(meta, open(SENDERS_FILE, "w"), indent=1)
    return sum(1 for m in meta.values() if m.get("ready"))


if __name__ == "__main__":
    {"setup": setup, "check": check}[sys.argv[1] if len(sys.argv) > 1 else "setup"]()
