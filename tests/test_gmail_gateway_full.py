"""Gmail Gateway full-fetch (body + attachments) and label-apply."""

import base64

from dealdesk.gmail_gateway import GmailGateway


def _b64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


class _Exec:
    def __init__(self, result):
        self._result = result

    def execute(self):
        return self._result


class _FakeMessages:
    def __init__(self, message, attachment_data):
        self._message = message
        self._attachment_data = attachment_data
        self.modify_calls = []
        self.send_calls = []

    def get(self, userId, id, format):
        return _Exec(self._message)

    def attachments(self):
        outer = self

        class _Att:
            def get(self, userId, messageId, id):
                return _Exec({"data": outer._attachment_data})

        return _Att()

    def modify(self, userId, id, body):
        self.modify_calls.append((id, body))
        return _Exec({})

    def send(self, userId, body):
        self.send_calls.append((userId, body))
        return _Exec({"id": "sent-1"})


class _FakeLabels:
    def __init__(self, existing):
        self._existing = existing
        self.create_calls = []

    def list(self, userId):
        return _Exec({"labels": self._existing})

    def create(self, userId, body):
        self.create_calls.append(body)
        new = {"id": f"Label_{len(self.create_calls)}", "name": body["name"]}
        self._existing.append(new)
        return _Exec(new)


class _FakeUsers:
    def __init__(self, messages, labels):
        self._messages = messages
        self._labels = labels

    def messages(self):
        return self._messages

    def labels(self):
        return self._labels


class _FakeService:
    def __init__(self, messages, labels):
        self._users = _FakeUsers(messages, labels)

    def users(self):
        return self._users


def _message_with_attachment():
    return {
        "id": "m1",
        "payload": {
            "headers": [
                {"name": "From", "value": "deals@acme.com"},
                {"name": "Subject", "value": "New deal"},
                {"name": "Date", "value": "Mon, 01 Jul 2026"},
            ],
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64url("Asking Price: $250,000")}},
                {
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "body": {"attachmentId": "att-1"},
                },
            ],
        },
    }


def test_fetch_email_parses_body_and_downloads_attachment():
    messages = _FakeMessages(_message_with_attachment(), _b64url("PDFBYTES"))
    service = _FakeService(messages, _FakeLabels([]))
    gateway = GmailGateway(service)

    email = gateway.fetch_email("m1")

    assert email.from_addr == "deals@acme.com"
    assert email.subject == "New deal"
    assert "Asking Price: $250,000" in email.body_text
    assert len(email.attachments) == 1
    assert email.attachments[0].filename == "report.pdf"
    assert email.attachments[0].data == b"PDFBYTES"


def test_apply_label_creates_missing_label_then_modifies():
    messages = _FakeMessages(_message_with_attachment(), "")
    labels = _FakeLabels([{"id": "L_INBOX", "name": "INBOX"}])
    gateway = GmailGateway(_FakeService(messages, labels))

    gateway.apply_label("m1", "Passed-BuyBox")

    assert labels.create_calls == [{"name": "Passed-BuyBox"}]
    assert messages.modify_calls == [("m1", {"addLabelIds": ["Label_1"]})]


def test_apply_label_caches_resolved_id():
    messages = _FakeMessages(_message_with_attachment(), "")
    labels = _FakeLabels([{"id": "L1", "name": "Rejected"}])
    gateway = GmailGateway(_FakeService(messages, labels))

    gateway.apply_label("m1", "Rejected")
    gateway.apply_label("m2", "Rejected")

    # Existing label — never created; both modifies used the resolved id.
    assert labels.create_calls == []
    assert messages.modify_calls == [("m1", {"addLabelIds": ["L1"]}), ("m2", {"addLabelIds": ["L1"]})]


def test_apply_label_strips_stale_error_label():
    # A retry that reaches a terminal Bucket removes the stale Error label in the
    # same modify call, so the Email ends carrying exactly one Bucket.
    messages = _FakeMessages(_message_with_attachment(), "")
    labels = _FakeLabels(
        [{"id": "L_ERR", "name": "Error"}, {"id": "L_PB", "name": "Passed-BuyBox"}]
    )
    gateway = GmailGateway(_FakeService(messages, labels))

    gateway.apply_label("m1", "Passed-BuyBox", remove_labels=("Error",))

    assert labels.create_calls == []  # nothing created
    assert messages.modify_calls == [
        ("m1", {"addLabelIds": ["L_PB"], "removeLabelIds": ["L_ERR"]})
    ]


def test_apply_label_does_not_create_an_absent_remove_label():
    # If the Error label doesn't exist, it must not be conjured just to remove it.
    messages = _FakeMessages(_message_with_attachment(), "")
    labels = _FakeLabels([{"id": "L_PB", "name": "Passed-BuyBox"}])
    gateway = GmailGateway(_FakeService(messages, labels))

    gateway.apply_label("m1", "Passed-BuyBox", remove_labels=("Error",))

    assert labels.create_calls == []  # Error was never created
    assert messages.modify_calls == [("m1", {"addLabelIds": ["L_PB"]})]  # no removeLabelIds


def test_send_message_encodes_from_subject_and_body():
    messages = _FakeMessages(_message_with_attachment(), "")
    gateway = GmailGateway(_FakeService(messages, _FakeLabels([])))

    gateway.send_message(
        "operator@example.com", "Deal: 123 Main St", "Price: $250,000",
        sender="deals@cgm-ventures.com",
    )

    assert len(messages.send_calls) == 1
    user_id, body = messages.send_calls[0]
    assert user_id == "me"
    raw = base64.urlsafe_b64decode(body["raw"].encode()).decode()
    assert "From: deals@cgm-ventures.com" in raw   # sent from the deals mailbox
    assert "To: operator@example.com" in raw
    assert "Subject: Deal: 123 Main St" in raw
    assert "Price: $250,000" in raw


def test_send_message_applies_label_to_sent_message():
    messages = _FakeMessages(_message_with_attachment(), "")
    labels = _FakeLabels([{"id": "L_DN", "name": "Deal Notifications"}])
    gateway = GmailGateway(_FakeService(messages, labels))

    gateway.send_message(
        "deals@cgm-ventures.com", "Deal: 1 Oak", "body",
        sender="deals@cgm-ventures.com", label="Deal Notifications",
    )

    # Existing label resolved (never re-created) and applied to the sent id.
    assert labels.create_calls == []
    assert messages.modify_calls == [("sent-1", {"addLabelIds": ["L_DN"]})]
