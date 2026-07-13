"""Generic-heuristics extraction over a clean labeled block."""

from dealdesk.heuristics import generic_extract

_SAMPLE = """
Address: 123 Main St, Dallas, TX 75201
Asking Price: $250,000
Estimated Rent: $2,000/mo
Year Built: 2005
ARV: $320,000
3 bed / 2 bath
"""


def test_extracts_labeled_fields():
    fields = generic_extract(_SAMPLE)
    assert fields["purchase_price"] == 250000
    assert fields["monthly_rent"] == 2000
    assert fields["arv"] == 320000
    assert fields["year_built"] == 2005
    assert fields["address"] == "123 Main St, Dallas, TX 75201"
    assert fields["city"] == "Dallas"
    assert fields["beds"] == 3
    assert fields["baths"] == 2.0


def test_city_from_explicit_label_wins():
    fields = generic_extract("City: Fort Worth\nAsking Price: $199,000")
    assert fields["city"] == "Fort Worth"


def test_returns_only_found_fields():
    fields = generic_extract("just some prose with no deal facts")
    assert "purchase_price" not in fields
    assert fields == {} or all(v is not None for v in fields.values())
