import os
import re
import secrets
import urllib.parse
import webbrowser
import http.server

import requests
from dotenv import load_dotenv

load_dotenv()
CID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
CS = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
if not CID or not CS:
    raise SystemExit("GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET .env mein nahi mile")

PORT = 8765
REDIRECT = f"http://localhost:{PORT}/"
STATE = secrets.token_urlsafe(16)
result = {}


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q or "error" in q:
            result["code"] = q.get("code", [None])[0]
            result["error"] = q.get("error", [None])[0]
            result["state"] = q.get("state", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"<h3>Done. Ye tab band karke terminal par wapas jao.</h3>")

    def log_message(self, *args):
        pass


auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": CID,
    "redirect_uri": REDIRECT,
    "response_type": "code",
    "scope": "https://www.googleapis.com/auth/blogger",
    "access_type": "offline",
    "prompt": "consent",
    "state": STATE,
})

print("Browser khul raha hai. Na khule to ye link copy karke kholo:\n")
print(auth_url, "\n")
webbrowser.open(auth_url)

server = http.server.HTTPServer(("localhost", PORT), Handler)
server.timeout = 300
for _ in range(20):
    server.handle_request()
    if result:
        break

if result.get("error") or not result.get("code"):
    raise SystemExit(f"Login fail: {result.get('error') or 'code nahi mila'}")
if result.get("state") != STATE:
    raise SystemExit("State mismatch, dobara try karo")

resp = requests.post("https://oauth2.googleapis.com/token", data={
    "code": result["code"],
    "client_id": CID,
    "client_secret": CS,
    "redirect_uri": REDIRECT,
    "grant_type": "authorization_code",
}, timeout=30)
data = resp.json()
token = data.get("refresh_token")
if not token:
    raise SystemExit(f"Refresh token nahi mila: {data.get('error')} {data.get('error_description', '')}")

with open(".env", "r", encoding="utf-8") as f:
    env = f.read()
if re.search(r"^GOOGLE_REFRESH_TOKEN=.*$", env, flags=re.M):
    env = re.sub(r"^GOOGLE_REFRESH_TOKEN=.*$", lambda m: "GOOGLE_REFRESH_TOKEN=" + token, env, flags=re.M)
else:
    env += "\nGOOGLE_REFRESH_TOKEN=" + token + "\n"
with open(".env", "w", encoding="utf-8") as f:
    f.write(env)

print("Refresh token .env mein save ho gaya. Screen par print nahi kiya (safe).")