"""Service-account auth → Gmail read client.

Reuses the Calculator's Google service-account key (one credential for Gmail +
Sheets, per ADR-0001/0002). Gmail access uses domain-wide delegation: the
service account impersonates the deals mailbox. Phase 1 requests read-only scope.
"""

from __future__ import annotations

import json
import os

from google.oauth2 import service_account
from googleapiclient.discovery import build

from .config import Config

# Discovery (Phase 1) needs read only. The pipeline (Phase 2) needs modify to
# apply Bucket labels, plus Sheets to write the Triage Log. The modify scope also
# authorizes ``messages.send`` (Phase 4 Deal Notifications / Daily Digest), so no
# extra scope is added.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"


def load_credentials(config: Config, scopes: list[str]):
    """Load the shared service-account key from the configured env var and
    return a delegated credential impersonating the inbox user."""
    raw = os.environ.get(config.credential_env)
    if not raw:
        raise RuntimeError(
            f"Missing service-account key: set ${config.credential_env} to the "
            "Calculator's Google service-account JSON."
        )
    info = json.loads(raw)
    creds = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    # Domain-wide delegation: act as the deals mailbox.
    return creds.with_subject(config.delegated_subject)


def build_gmail_service(config: Config, scopes: list[str] | None = None):
    """Build an authorized Gmail API client. Defaults to read-only (discovery);
    the pipeline passes ``[GMAIL_MODIFY_SCOPE]`` so it can apply Bucket labels."""
    creds = load_credentials(config, scopes or [GMAIL_READONLY_SCOPE])
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def build_sheets_service(config: Config):
    """Build an authorized Google Sheets API client for the Triage Log write."""
    creds = load_credentials(config, [SHEETS_SCOPE])
    return build("sheets", "v4", credentials=creds, cache_discovery=False)
