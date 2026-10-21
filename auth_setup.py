"""ONE-TIME local OAuth setup for the Blogger API.

Run this once on your machine:
    python auth_setup.py

It opens a browser, you click "Allow", and it prints a refresh token.
Copy that token into your .env file AND into the GitHub Secret
GOOGLE_REFRESH_TOKEN. Nothing is written to any committed file.

Prerequisites:
1. Go to https://console.cloud.google.com -> create/select a project
2. Enable "Blogger API v3"
3. OAuth consent screen -> add your own email as a test user
4. Credentials -> OAuth client ID -> Application type: Desktop app
5. Put the client ID / secret in .env as GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

PROJECT_ROOT = Path(__file__).resolve().parent
SCOPES = ["https://www.googleapis.com/auth/blogger"]

BOX_WIDTH = 78


def _box(title: str, body: str) -> None:
    line = "-" * (BOX_WIDTH - 2)
    print("+" + line + "+")
    print(f"| {title.ljust(BOX_WIDTH - 4)} |")
    print("+" + line + "+")
    for row in body.splitlines() or [""]:
        print(f"| {row.ljust(BOX_WIDTH - 4)} |")
    print("+" + line + "+")


def main() -> int:
    load_dotenv(PROJECT_ROOT / ".env")
    client_id = input("Paste your OAuth Client ID (or leave blank to use .env): ").strip()
    if not client_id:
        client_id = os.getenv("GOOGLE_CLIENT_ID", "").strip()
    client_secret = input("Paste your OAuth Client Secret (or leave blank to use .env): ").strip()
    if not client_secret:
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        print("\nERROR: client_id and client_secret are both required.", file=sys.stderr)
        return 1

    print("\nOpening your browser for Google sign-in...")
    print("If it does not open, copy the URL printed below into a browser.\n")

    client_config = {
        "installed": {
            "client_id": client_id,
            "client_secret": client_secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
            "scopes": SCOPES,
        }
    }

    flow = InstalledAppFlow.from_client_config(
        client_config,
        scopes=SCOPES,
    )
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        success_message="Authorization complete. You can close this tab.",
    )

    if not creds.refresh_token:
        _box(
            "NO REFRESH TOKEN RETURNED",
            "\n".join(
                [
                    "Google did not give a refresh token.",
                    "This happens when an app is already authorised.",
                    "",
                    "Fix: go to https://myaccount.google.com/permissions",
                    "Remove your OAuth client from the list, then run this script again.",
                ]
            ),
        )
        return 2

    _box(
        "SUCCESS - COPY YOUR REFRESH TOKEN",
        "\n".join(
            [
                "",
                "  " + creds.refresh_token,
                "",
                "Next steps:",
                "1. Paste it into .env  ->  GOOGLE_REFRESH_TOKEN=" + creds.refresh_token,
                "2. GitHub repo -> Settings -> Secrets and variables -> Actions",
                "3. Add secret GOOGLE_REFRESH_TOKEN with the same value",
                "4. Run: python main.py --dry-run",
                "",
                "This token is sensitive. Do not commit it, do not share it.",
            ]
        ),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())