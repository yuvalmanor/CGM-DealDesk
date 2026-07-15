"""Gmail Gateway — thin I/O adapter.

Read half (Phase 1): fetch the header-only work queue. Full half (Phase 2):
fetch an Email's body + attachments, and apply a Bucket label (the label-last
write that marks an Email "done"). Label resolution creates the Bucket label if
the operator hasn't made it yet.
"""

from __future__ import annotations

import base64
from datetime import date
from email.message import EmailMessage

from .models import Attachment, Email, MessageMeta
from .query import build_work_queue_query

# Impersonated-mailbox alias understood by the Gmail API.
_USER_ID = "me"
_METADATA_HEADERS = ["From", "Subject", "Date"]
_TEXT_MIME = "text/plain"


class GmailGateway:
    """Wraps an authorized Gmail service. Inject a fake service in tests."""

    def __init__(self, service):
        self._service = service
        self._label_ids: dict[str, str] = {}  # name -> id, resolved lazily

    def fetch_work_queue(
        self,
        cutoff: date,
        bucket_labels: tuple[str, ...] | list[str],
        limit: int | None = None,
    ) -> list[MessageMeta]:
        """Return header-only metadata for every unprocessed Inbox Email on/after
        the cutoff. Paginates the list; fetches metadata format only (no body,
        no attachment download). ``limit`` caps the number of Emails returned —
        pagination and metadata fetches stop early, so it genuinely bounds reads
        (and, downstream, AI cost)."""
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
            if not page_token or (limit is not None and len(ids) >= limit):
                break

        if limit is not None:
            ids = ids[:limit]

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

    def fetch_email(self, msg_id: str) -> Email:
        """Fetch an Email in full: plain-text body plus every attachment's bytes
        (attachment payloads are downloaded separately by Gmail's API)."""
        messages = self._service.users().messages()
        detail = messages.get(userId=_USER_ID, id=msg_id, format="full").execute()
        headers = _header_map(detail.get("payload", {}))

        body_parts: list[str] = []
        attachments: list[Attachment] = []
        self._walk_payload(msg_id, detail.get("payload", {}), body_parts, attachments)

        return Email(
            id=msg_id,
            from_addr=headers.get("from", ""),
            subject=headers.get("subject", ""),
            date=headers.get("date", ""),
            body_text="\n".join(body_parts),
            attachments=tuple(attachments),
        )

    def apply_label(self, msg_id: str, label_name: str) -> None:
        """Apply a Bucket label to an Email — the label-last write that marks it
        processed. Creates the label if it doesn't exist yet."""
        label_id = self._resolve_label_id(label_name)
        self._service.users().messages().modify(
            userId=_USER_ID, id=msg_id, body={"addLabelIds": [label_id]}
        ).execute()

    def send_message(
        self, to: str, subject: str, body_text: str, sender: str, label: str | None = None
    ) -> None:
        """Send a plain-text email. ``sender`` is set explicitly as the From so a
        Deal Notification / Digest carries the ``deals@cgm-ventures.com`` address.
        The service account impersonates that mailbox (domain-wide delegation), so
        it is authorized to send as it. Covered by the ``gmail.modify`` scope.

        When ``label`` is given, apply it to the sent message directly (rather
        than relying on a Gmail filter to route it) — for send-to-self, the
        delivered copy carries the label. Creates the label if it doesn't exist."""
        message = EmailMessage()
        message["To"] = to
        message["From"] = sender
        message["Subject"] = subject
        message.set_content(body_text)
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
        sent = self._service.users().messages().send(
            userId=_USER_ID, body={"raw": raw}
        ).execute()
        if label:
            label_id = self._resolve_label_id(label)
            self._service.users().messages().modify(
                userId=_USER_ID, id=sent["id"], body={"addLabelIds": [label_id]}
            ).execute()

    def _walk_payload(self, msg_id, payload, body_parts, attachments) -> None:
        mime = payload.get("mimeType", "")
        body = payload.get("body", {})
        filename = payload.get("filename", "")

        if filename and body.get("attachmentId"):
            data = self._service.users().messages().attachments().get(
                userId=_USER_ID, messageId=msg_id, id=body["attachmentId"]
            ).execute()
            raw = _b64url(data.get("data", ""))
            attachments.append(Attachment(filename=filename, mime_type=mime, data=raw))
        elif mime == _TEXT_MIME and body.get("data"):
            body_parts.append(_b64url(body["data"]).decode("utf-8", errors="replace"))

        for part in payload.get("parts", []) or []:
            self._walk_payload(msg_id, part, body_parts, attachments)

    def _resolve_label_id(self, label_name: str) -> str:
        if label_name in self._label_ids:
            return self._label_ids[label_name]
        labels_api = self._service.users().labels()
        listing = labels_api.list(userId=_USER_ID).execute()
        for lbl in listing.get("labels", []):
            self._label_ids[lbl["name"]] = lbl["id"]
        if label_name not in self._label_ids:
            created = labels_api.create(
                userId=_USER_ID, body={"name": label_name}
            ).execute()
            self._label_ids[label_name] = created["id"]
        return self._label_ids[label_name]


def _header_map(payload: dict) -> dict:
    return {
        h.get("name", "").lower(): h.get("value", "")
        for h in payload.get("headers", [])
    }


def _b64url(data: str) -> bytes:
    # Gmail encodes body/attachment payloads as base64url (RFC 4648). Pad before
    # decoding so a missing '=' tail doesn't raise.
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded)


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
