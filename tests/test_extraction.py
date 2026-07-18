"""Extraction Ladder: rung selection, PDF text, and AI fallback."""

from dealdesk.buybox import BuyBox, FieldSpec
from dealdesk.extraction import ExtractionLadder
from dealdesk.models import Attachment, Email

BUYBOX = BuyBox(
    fields=(
        FieldSpec("purchase_price", "gate", "feed-required", "<=", 300000),
        FieldSpec("year_built", "gate", "none", ">=", 2000),
        FieldSpec("city", "gate", "none", "in", ["dallas"]),
        FieldSpec("monthly_rent", "none", "feed-required"),
    )
)

_FULL = (
    "Address: 9 Oak Dr, Dallas, TX 75201\n"
    "Asking Price: $250,000\n"
    "Estimated Rent: $2,000\n"
    "Year Built: 2005\n"
)


class _FakeAI:
    def __init__(self, result=None):
        self.result = result or []
        self.calls = []

    def extract_properties(self, text):
        self.calls.append(text)
        return self.result


def _email(body="", attachments=()):
    return Email(id="e1", from_addr="x@acme.com", subject="", date="", body_text=body, attachments=attachments)


def test_pdf_text_feeds_the_ladder_without_ai():
    ai = _FakeAI()
    # Inject a fake PDF-to-text so the attachment's bytes yield the full block.
    ladder = ExtractionLadder(BUYBOX, ai, pdf_to_text=lambda data: _FULL)
    email = _email(attachments=(Attachment("report.pdf", "application/pdf", b"%PDF"),))

    result = ladder.extract(email)

    assert result.used_ai is False  # heuristics were sufficient — no AI call
    assert len(result.properties) == 1
    assert result.properties[0]["purchase_price"] == 250000
    assert ai.calls == []


def test_body_and_pdf_combine_to_reach_must_haves():
    ai = _FakeAI()
    # Body has price + rent; PDF supplies year + city. Neither alone is enough.
    ladder = ExtractionLadder(
        BUYBOX, ai, pdf_to_text=lambda d: "Year Built: 2005\nCity: Dallas"
    )
    email = _email(
        body="Asking Price: $250,000\nEstimated Rent: $2,000",
        attachments=(Attachment("r.pdf", "application/pdf", b"%PDF"),),
    )

    result = ladder.extract(email)

    assert ai.calls == []
    assert result.used_ai is False
    assert result.properties[0]["year_built"] == 2005
    assert result.properties[0]["city"] == "Dallas"


def test_unknown_source_falls_through_to_ai():
    ai = _FakeAI(result=[{"purchase_price": 199000, "city": "Dallas"}])
    ladder = ExtractionLadder(BUYBOX, ai)
    email = _email(body="hey, got a great one off-market, call me")

    result = ladder.extract(email)

    assert len(ai.calls) == 1  # deterministic rung fell short
    assert result.used_ai is True
    assert result.properties == [{"purchase_price": 199000, "city": "Dallas"}]


def test_no_ai_returns_partial_heuristics_without_calling_ai():
    ai = _FakeAI(result=[{"should": "not be used"}])
    ladder = ExtractionLadder(BUYBOX, ai, ai_enabled=False)
    # Body has price + rent but no year/city — must-haves are incomplete.
    email = _email(body="Asking Price: $250,000\nEstimated Rent: $2,000")

    result = ladder.extract(email)

    assert ai.calls == []          # AI never called
    assert result.used_ai is False
    assert result.properties[0]["purchase_price"] == 250000  # best-effort partial


def test_no_ai_with_no_facts_yields_no_properties():
    ai = _FakeAI(result=[{"x": 1}])
    ladder = ExtractionLadder(BUYBOX, ai, ai_enabled=False)

    result = ladder.extract(_email(body="just some prose, call me maybe"))

    assert ai.calls == []
    assert result.properties == []


def test_confident_reject_skips_ai_when_single_property():
    # Price is above the Buy Box ceiling and the year is missing, so must-haves
    # are incomplete — normally that pays for AI. But the recovered price alone
    # already fails a gate, so the Verdict is a certain Reject: skip the AI call.
    ai = _FakeAI(result=[{"should": "not be used"}])
    ladder = ExtractionLadder(BUYBOX, ai)
    email = _email(body="Asking Price: $400,000")

    result = ladder.extract(email)

    assert ai.calls == []           # confident Reject → no paid call
    assert result.used_ai is False
    assert result.properties[0]["purchase_price"] == 400000


def test_multi_listing_over_range_first_still_goes_to_ai():
    # Two listings; the first is out-of-range but the second is in-range. The
    # heuristics only read the first, so a Reject here would silently drop the
    # in-range listing. The multi-property guard defers the whole Email to AI.
    ai = _FakeAI(result=[{"purchase_price": 250000, "city": "Dallas"}])
    ladder = ExtractionLadder(BUYBOX, ai)
    email = _email(
        body=(
            "Address: 1 Elm St, Dallas, TX 75201\nAsking Price: $400,000\n"
            "Address: 2 Oak St, Dallas, TX 75202\nAsking Price: $250,000\n"
        )
    )

    result = ladder.extract(email)

    assert len(ai.calls) == 1       # guard held — not Rejected on the first deal
    assert result.used_ai is True


def test_empty_text_yields_no_properties_and_no_ai():
    ai = _FakeAI(result=[{"should": "not appear"}])
    ladder = ExtractionLadder(BUYBOX, ai)
    result = ladder.extract(_email(body="   "))
    assert result.properties == []
    assert result.used_ai is False
    assert ai.calls == []
