"""Gmail Gateway — read half (thin I/O adapter).

Phase 1 exposes exactly one capability: fetch the header-only work queue. It
lists message ids matching the work-queue query, then pulls just the From /
Subject / Date headers for each. Strictly read-only — no label, no modify call
is made here.
"""

from __future__ import annotations

from datetime import date

from .models import MessageMeta
from .query import build_work_queue_query

# Impersonated-mailbox alias understood by the Gmail API.
_USER_ID = "me"
_METADATA_HEADERS = ["From", "Subject", "Date"]


class GmailGateway:
    """Wraps an authorized Gmail service. Inject a fake service in tests."""

    def __init__(self, service):
        self._service = service

    def fetch_work_queue(
        self, cutoff: date, bucket_labels: tuple[str, ...] | list[str]
    ) -> list[MessageMeta]:
        """Return header-only metadata for every unprocessed Inbox Email on/after
        the cutoff. Paginates the list; fetches metadata format only (no body,
        no attachment download)."""
        query = build_work_queue_query(cutoff, bucket_labels)
        messages = self._service.users().messages()

        ids: list[str] = []
        page_token: str | None = None
        while True:
            resp = messages.list(
                userId=_USER_ID, q=query, pageToken=page_token
            ).execute()
            ids.extend(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        result: list[MessageMeta] = []
        for msg_id in ids:
            detail = messages.get(
                userId=_USER_ID,
                id=msg_id,
                format="metadata",
                metadataHeaders=_METADATA_HEADERS,
            ).execute()
            result.append(_to_meta(msg_id, detail))
        return result


def _to_meta(msg_id: str, detail: dict) -> MessageMeta:
    headers = {
        h.get("name", "").lower(): h.get("value", "")
        for h in detail.get("payload", {}).get("headers", [])
    }
    return MessageMeta(
        id=msg_id,
        from_addr=headers.get("from", ""),
        subject=headers.get("subject", ""),
        date=headers.get("date", ""),
    )
