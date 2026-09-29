"""One-time Google sign-in for the care-home Google Sheet (9/29, Joey OK'd).
Uses the existing Surven Google OAuth client (PIAs.txt 'google ads client' / 'google ads secret') with the
drive.file scope only: the app can see and edit only files it creates. Saves the refresh token to
C:\\Users\\evann\\Desktop\\PIA\\google-sheets-oauth.json. Never prints secrets.
Also exports access_token() for other scripts."""
import http.server, json, re, secrets, sys, threading, time, urllib.parse, urllib.request, webbrowser

PIA = r"C:\Users\evann\Desktop\PIA\PIAs.txt"
TOKEN_FILE = r"C:\Users\evann\Desktop\PIA\google-sheets-oauth.json"
# spreadsheets (not drive.file): Joey made the sheet himself (9/29, "Care home email list", owner joedubstep312),
# and drive.file only reaches files this app created.
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
# The OAuth client is a Web client with exactly one registered redirect URI (see memory project-google-ads-api-access).
PORT, PATH = 53682, "/oauth2callback"


def client():
    from . import secrets_store  # PIA files on Joey's PC, GitHub secret in the cloud
    return secrets_store.get("GOOGLE_CLIENT_ID"), secrets_store.get("GOOGLE_CLIENT_SECRET")


def access_token():
    from . import secrets_store
    cid, sec = client()
    rt = secrets_store.get("GOOGLE_SHEETS_REFRESH_TOKEN")
    body = urllib.parse.urlencode({"client_id": cid, "client_secret": sec, "refresh_token": rt, "grant_type": "refresh_token"}).encode()
    return json.loads(urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=30).read())["access_token"]


def sign_in():
    cid, sec = client()
    state = secrets.token_urlsafe(16)
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got.update({k: v[0] for k, v in q.items()})
            self.send_response(200); self.send_header("Content-Type", "text/html"); self.end_headers()
            msg = "Done. You can close this tab." if "code" in got else "Something went wrong: " + got.get("error", "no code")
            self.wfile.write(f"<h2 style='font-family:sans-serif'>{msg}</h2>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", PORT), H)
    redirect = f"http://127.0.0.1:{PORT}{PATH}"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": SCOPE,
        "access_type": "offline", "prompt": "consent", "state": state, "login_hint": "joedubstep312@gmail.com"})
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    webbrowser.open(url)
    print("Opened Google sign-in in the browser. Waiting up to 10 minutes...", flush=True)
    t0 = time.time()
    while "code" not in got and "error" not in got and time.time() - t0 < 600:
        time.sleep(1)
    srv.shutdown()
    if "code" not in got:
        sys.exit("No sign-in: " + got.get("error", "timed out"))
    body = urllib.parse.urlencode({"code": got["code"], "client_id": cid, "client_secret": sec,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    tok = json.loads(urllib.request.urlopen("https://oauth2.googleapis.com/token", body, timeout=30).read())
    if "refresh_token" not in tok:
        sys.exit("Google returned no refresh token")
    json.dump({"client_id": cid, "refresh_token": tok["refresh_token"], "scope": tok.get("scope", SCOPE)}, open(TOKEN_FILE, "w"))
    print("Saved Google token (scope: " + tok.get("scope", SCOPE) + ")")


if __name__ == "__main__":
    sign_in()
