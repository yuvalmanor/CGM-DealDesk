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


# --- address plausibility ---------------------------------------------------
# A wrong address is worse than none: it lands in the Triage Log and seeds the
# re-send lookup. These are the real shapes that defeat the bare label match.

def test_boilerplate_heading_is_not_taken_as_an_address():
    # Verbatim from a live New Western Email's disclosures.
    text = (
        "2. NO UNACCOMPANIED ENTRY OF PROPERTY: Broker and its affiliates do not give "
        "you authority, express or implied, to enter or access this property "
        "unaccompanied. Access may only be obtained through scheduling an inspection."
    )
    assert "address" not in generic_extract(text)


def test_real_address_is_found_even_when_boilerplate_precedes_it():
    text = (
        "NO UNACCOMPANIED ENTRY OF PROPERTY: Broker does not give you authority to enter.\n"
        "Address: 373 Bellvue Dr, Fort Worth, TX 76134\n"
    )
    fields = generic_extract(text)
    assert fields["address"] == "373 Bellvue Dr, Fort Worth, TX 76134"
    assert fields["city"] == "Fort Worth"


def test_prose_without_a_number_is_rejected():
    assert "address" not in generic_extract("Property: contact your rep for details")


# --- beds/baths orientation -------------------------------------------------
# Prose is value-first ("3 bed / 2 bath"); the table layouts HTML Sources use are
# label-first ("Beds\n5"). Both must read correctly, and neither may borrow a
# number from the field beside it.

def test_table_layout_reads_label_first():
    # Verbatim shape from a live Diamond Acquisitions Email. Read value-first,
    # "$279,990\n\nBeds" yields 990 beds and "5\n\nBaths" yields 5 baths.
    text = "Sales Price\n$279,990\n\nBeds\n5\n\nBaths\n4\n\nSqFt\n3,238\n\nYear Built\n1958"
    fields = generic_extract(text)
    assert fields["purchase_price"] == 279990
    assert fields["beds"] == 5
    assert fields["baths"] == 4.0
    assert fields["year_built"] == 1958


def test_spaced_table_layout_reads_label_first():
    # Verbatim shape from a live New Western Email.
    text = "Bedrooms\n\n2\n\nBathrooms\n\n2.0\n\nYear Built\n\n1985\n\nCash Price\n\n$97,500"
    fields = generic_extract(text)
    assert fields["beds"] == 2
    assert fields["baths"] == 2.0
    assert fields["year_built"] == 1985
    assert fields["purchase_price"] == 97500


def test_value_first_prose_still_wins_over_the_label_first_reading():
    # Label-first against this reads the baths number (2) as the bed count.
    fields = generic_extract("3 bed / 2 bath")
    assert fields["beds"] == 3
    assert fields["baths"] == 2.0


def test_a_count_is_never_the_tail_of_a_larger_number():
    assert "beds" not in generic_extract("Sales Price $279,990 and no bedroom count given")
