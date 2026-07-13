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

# Read-only. Later phases that label/send will widen this.
GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"


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


def build_gmail_service(config: Config):
    """Build an authorized read-only Gmail API client."""
    creds = load_credentials(config, [GMAIL_READONLY_SCOPE])
    return build("gmail", "v1", credentials=creds, cache_discovery=False)
