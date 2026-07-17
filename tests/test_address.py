"""Address Normalizer goldens — equivalence and non-equivalence.

The normalizer exists to answer one question: are these two strings the same
address, spelled differently? So the tests are stated as pairs through the public
interface, not as assertions about a particular key format (the key is an opaque
implementation detail — only its equality matters).

The non-equivalence cases carry the real weight. A false collapse would make
DealDesk claim two different houses are one, which is the failure the re-send
flag is explicitly forbidden to make (US 28).
"""

import pytest

from dealdesk.address import normalize_address


def _same(a, b):
    return normalize_address(a) == normalize_address(b)


# --- equivalence: the same house, spelled differently ----------------------

@pytest.mark.parametrize(
    "a, b, why",
    [
        ("123 Main St", "123 Main Street", "street-type abbreviation"),
        ("123 Main St", "123 MAIN ST", "casing"),
        ("123 Main St", "  123   Main   St  ", "whitespace"),
        ("123 Main St, Dallas, TX", "123 Main St Dallas TX", "punctuation"),
        ("123 Main St., Dallas, TX.", "123 Main St Dallas TX", "trailing periods"),
        ("123 Main St, Dallas, Texas", "123 Main St, Dallas, TX", "state abbreviation"),
        ("456 Oak Ave", "456 Oak Avenue", "avenue"),
        ("9 Elm Rd", "9 Elm Road", "road"),
        ("77 Pine Dr", "77 Pine Drive", "drive"),
        ("5 Cedar Ln", "5 Cedar Lane", "lane"),
        ("12 Park Blvd", "12 Park Boulevard", "boulevard"),
        ("8 Rose Ct", "8 Rose Court", "court"),
        ("3 Hill Cir", "3 Hill Circle", "circle"),
        ("21 Bay Pkwy", "21 Bay Parkway", "parkway"),
        ("123 N Main St", "123 North Main Street", "directional"),
        ("400 SW Grand Ave", "400 Southwest Grand Avenue", "compound directional"),
    ],
)
def test_equivalent_spellings_share_a_key(a, b, why):
    assert _same(a, b), why


@pytest.mark.parametrize(
    "a, b, why",
    [
        ("123 Main St Apt 4", "123 Main St Unit 4", "apt == unit"),
        ("123 Main St Apt 4", "123 Main St #4", "hash designator"),
        ("123 Main St Apt 4", "123 Main Street, Apt. 4", "designator + street type"),
        ("123 Main St Ste 200", "123 Main St Suite 200", "suite"),
        ("123 Main St Unit #4", "123 Main St Unit 4", "doubled designator"),
    ],
)
def test_unit_suffix_spellings_share_a_key(a, b, why):
    assert _same(a, b), why


def test_full_single_line_address_round_trips():
    # The shape the AI fallback actually emits (one line, city/state/zip).
    assert _same(
        "1420 Maple Grove Dr, Fort Worth, TX 76107",
        "1420 MAPLE GROVE DRIVE, FORT WORTH, TX 76107",
    )


# --- non-equivalence: distinct houses stay distinct ------------------------

@pytest.mark.parametrize(
    "a, b, why",
    [
        ("123 Main St", "124 Main St", "different street number"),
        ("123 Main St", "123 Oak St", "different street name"),
        ("123 Main St", "123 Main Ave", "street vs avenue"),
        ("123 N Main St", "123 S Main St", "opposite directionals"),
        ("123 Main St Apt 4", "123 Main St Apt 5", "different unit"),
        ("123 Main St, Dallas, TX", "123 Main St, Fort Worth, TX", "different city"),
        ("123 Main St, Dallas, TX 75201", "123 Main St, Dallas, TX 75202", "different zip"),
        ("123 Main St", "1234 Main St", "number is not a prefix match"),
    ],
)
def test_distinct_houses_never_share_a_key(a, b, why):
    assert not _same(a, b), why


def test_unit_number_is_kept_not_dropped():
    # A unit is part of the identity: the building is not the home.
    assert not _same("123 Main St", "123 Main St Apt 4")


# --- the empty key: unmatchable by design ---------------------------------

@pytest.mark.parametrize("blank", [None, "", "   ", ",", " , . "])
def test_addressless_input_yields_the_empty_key(blank):
    assert normalize_address(blank) == ""


@pytest.mark.parametrize("junk", ["#", "Unit", "Apt #", "N St", ", TX,"])
def test_input_with_nothing_identifying_yields_the_empty_key(junk):
    # Structure but no house: refusing a key is what stops two junk addresses
    # from keying alike and being called the same house.
    assert normalize_address(junk) == ""


def test_normalization_is_stable():
    # Normalizing an already-normalized key must not move it again, or the same
    # house could key differently depending on which spelling arrived first.
    once = normalize_address("123 N Main St., Apt. 4, Dallas, TX")
    assert normalize_address(once) == once
