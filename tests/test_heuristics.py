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


# --- money-field precision --------------------------------------------------
# Verbatim-real shapes from the 2026-07-17 spike. The loose label→number gap and
# the missing k-shorthand handling made these mis-parse in production; the AI
# re-extract masked it, so a Reject short-circuit would have silently killed real
# deals on a fabricated price. These pin the fix.

def test_k_shorthand_price_is_scaled():
    # "$150k" must be 150000, not 150 (southernhills / lotus).
    assert generic_extract("The Numbers Price: $150k")["purchase_price"] == 150000


def test_sub_100k_k_shorthand_is_not_dropped():
    # The old {3,} minimum dropped "$95k" entirely (only 2 digits before the k).
    assert generic_extract("Asking Price: $95k")["purchase_price"] == 95000


def test_price_per_sqft_is_not_read_as_the_price():
    # "Price/SqFt: $187.63" → the loose gap grabbed 187 (Fazio, ~64 emails).
    assert "purchase_price" not in generic_extract("Price/SqFt: $187.63")


def test_ad_copy_number_is_not_read_as_the_price():
    # "purchase price) * Up to 100% construction holdback" → grabbed 100 (lender ad).
    assert "purchase_price" not in generic_extract(
        "purchase price) * Up to 100% construction holdback"
    )


def test_sold_comp_is_not_read_as_the_price():
    # "Sold For:" carries no price label, so no purchase_price is emitted.
    assert "purchase_price" not in generic_extract("Sold For: $339,243")


def test_real_comma_price_still_parses():
    assert generic_extract("Asking Price: $250,000")["purchase_price"] == 250000


def test_arv_k_shorthand_is_scaled():
    assert generic_extract("ARV: $320k")["arv"] == 320000


def test_low_monthly_rent_is_not_rejected_by_the_price_floor():
    # Rent's plausibility floor is far below the price/ARV floor; a real $1,200
    # rent (no comma-free k, under $10k) must still be recovered.
    assert generic_extract("Market Rent: $1,200/mo")["monthly_rent"] == 1200


# --- shared-reader flavor-1 fixes (Phase 8 Step 1) --------------------------
# Verbatim-real shapes. These are format gaps common enough to fix in the shared
# reader rather than per-Source: a pipe separator (LUSH) and the "Build"
# misspelling (ProphetHomes). Fixing them here helps every Source at once.

def test_pipe_separator_price_is_recovered():
    # LUSH: "Purchase Price | $170,000 OBO" — a colon/space-only gap stopped at |.
    assert generic_extract("Purchase Price | $170,000 OBO")["purchase_price"] == 170000


def test_year_build_misspelling_is_recovered():
    # ProphetHomes: "Year Build: 1968Cash Price: $89000" (cells concatenated).
    fields = generic_extract("Year Build: 1968Cash Price: $89000")
    assert fields["year_built"] == 1968
    assert fields["purchase_price"] == 89000


def test_pipe_gap_does_not_jump_across_a_labeled_column():
    # The pipe gap must not cross a text column into an unrelated number.
    assert "purchase_price" not in generic_extract("Price | Beds | 5")


# ---- square footage -------------------------------------------------------
#
# Fixtures are the real spellings from the live Sources (verbatim fragments of
# the Emails behind the Calculator-fed deals), one per format in the bank.


def test_sqft_square_footage_label():
    # Lush Property Solutions
    text = "Bed: 4 Bath: 3 Square Footage: 2,954 Zoning: Single Family Lot Size: 5,450 Sq. Ft."
    assert generic_extract(text)["sqft"] == 2954


def test_sqft_sq_ft_label():
    # A-Team Home Buyers
    text = "BEDS: 3 YEAR BUILT: 1963 BATHS: 2 EMD: $5,000 SQ FT: 1,610 CLOSE: 07/22/26"
    assert generic_extract(text)["sqft"] == 1610


def test_sqft_building_area_label():
    # Alpha Home Buyers
    text = "Beds/Baths: 3/2 Building Area: 1,868 SQFT Garage: 2 car garage Lot Size: 7,405 SQFT"
    assert generic_extract(text)["sqft"] == 1868


def test_sqft_bare_sqft_label():
    # Momentum Capital
    text = "Property Type: SFH Bed/Bath: 3/3 SQFT: 2,057 LOT SQFT: 6,665 Yr Build: 2002"
    assert generic_extract(text)["sqft"] == 2057


def test_sqft_living_area_label():
    # Aaragon Properties
    text = "3 Beds | 2 Bath Living area: 2,100 sqft Lot size: 2.42 Acres Year Built: 2004"
    assert generic_extract(text)["sqft"] == 2100


def test_sqft_unlabeled_value_first():
    # InvestorLift — no label anywhere, area trails the bed/bath run
    text = "Fort Worth, TX 76132 3 beds 2 baths 1,418 sqft"
    assert generic_extract(text)["sqft"] == 1418


def test_sqft_unlabeled_without_thousands_separator():
    # Rise Realty — "2039  Sqft", two spaces, no comma
    text = "3  Beds  2  Bath  2039  Sqft  2  Garage Sachse, TX 75048"
    assert generic_extract(text)["sqft"] == 2039


# ---- the four traps -------------------------------------------------------


def test_sqft_never_reads_the_lot_size():
    # The lot is quoted in the same line and is a plausible house size, so only
    # the label can tell them apart.
    text = "Building Area: 1,868 SQFT Lot Size: 7,405 SQFT"
    assert generic_extract(text)["sqft"] == 1868


def test_sqft_never_reads_a_lot_labeled_sqft():
    text = "LOT SQFT: 6,665 Yr Build: 2002"
    assert "sqft" not in generic_extract(text)


def test_sqft_never_reads_money_sitting_against_the_label():
    # A-Team's "EMD: $5,000 SQ FT: 1,610" — the deposit abuts the SQ FT label.
    text = "EMD: $5,000 SQ FT: 1,610"
    assert generic_extract(text)["sqft"] == 1610


def test_sqft_never_reads_a_comparables_area():
    # Momentum lists six comps, each with its own area; none is this house.
    text = "COMPS: 5522 Challenger Court | $459,000 | 2,315 SQFT 115 Mayflower Court | $460,000 | 2,332 SQFT"
    assert "sqft" not in generic_extract(text)


def test_sqft_ignores_a_carport_area():
    # Lush: "It also has a 460 Sq. Ft. Carport" — below any dwelling floor.
    text = "This property is a 4 Bedroom, 3 Full Bathroom. It also has a 460 Sq. Ft. Carport"
    assert "sqft" not in generic_extract(text)


def test_sqft_ignores_boilerplate_with_no_figure():
    # New Western's disclaimer names the term but quotes no number.
    text = "including, but not limited to, estimated rehab costs, as-is property square footage measurements"
    assert "sqft" not in generic_extract(text)


def test_sqft_absent_when_the_email_does_not_say():
    assert "sqft" not in generic_extract("Asking Price: $250,000 Year Built: 2010")


def test_labeled_sqft_wins_over_an_earlier_unlabeled_lot_figure():
    text = "Lot Size: 8,669 sq ft ACCESS: call SQ FT: 1,610"
    assert generic_extract(text)["sqft"] == 1610


# ---- formats and traps found by the 272-email audit ------------------------
#
# The first pass was built from 22 Emails (the Calculator-fed ones) and was
# wrong on the wider corpus in both directions. These are the cases it missed.


def test_sqft_square_feet_label():
    # Q Acquisitions — "Square Feet", a spelling the first pass didn't know, so
    # it fell through to value-first and returned a comparable's area instead.
    text = "Details Bed / Bath: 3 / 2 Square Feet: 1,773 Year Build: 1987 Lot Size: 0.126 Acres"
    assert generic_extract(text)["sqft"] == 1773


def test_sqft_is_the_house_not_the_garage_listed_as_the_next_field():
    # Southern Hills — "Size: 1,114 SqFt Garage: 1 Car, 325 SqFt (attached)".
    # The outbuilding guard fired on the *following field label* and returned
    # the garage's 325.
    text = "Bedrooms: 3 Bathrooms: 2 Size: 1,114 SqFt Garage: 1 Car, 325 SqFt (attached) Built: 1986"
    assert generic_extract(text)["sqft"] == 1114


def test_sqft_house_label_with_a_trailing_garage_field():
    text = "House: 2,026 SqFt Garage: 2 Car, 484 SqFt (attached) Built: 1973"
    assert generic_extract(text)["sqft"] == 2026


# ---- sold comparables (the biggest source of wrong answers) ---------------


def test_sqft_ignores_a_sold_comparable_in_a_comparables_block():
    text = "Comparables 5414 Cypress Dr Rowlett, Texas SOLD for $385,000 3 bed / 2 bath 1,776 SqFt"
    assert "sqft" not in generic_extract(text)


def test_sqft_ignores_a_labeled_comparable_with_a_sale_price():
    # Fazio — the comp carries the same "SqFt:" label as a subject property.
    text = "10757 Braemoor Drive Beds: 3 | Bath: 2 | SqFt: 1,983 Sold For: $309,000 | Price/SqFt: $155.82"
    assert "sqft" not in generic_extract(text)


def test_sqft_ignores_a_comparable_sold_on_a_date():
    text = "COMPARABLES: Same street - Sold on 02/10/26 for $167,500 Bed/Bath: 3/2 Sqft: 1,347"
    assert "sqft" not in generic_extract(text)


def test_sqft_ignores_a_comp_described_as_closed():
    # New Western writes prose comps that say "closed for", never "sold".
    text = "we really only have two legit comps - 2737 Jills Dr which was 1326 SqFt 3/2/0 and closed for $300,000"
    assert "sqft" not in generic_extract(text)


def test_the_word_comps_alone_does_not_reject_the_subject_property():
    """Keying the guard on the noun rather than the sale cost 13 correct answers:
    two Sources put "comps" right beside a subject property's own figures."""
    # Q Acquisitions — a link caption immediately before the stats block.
    qa = "FCFS. Click Here for Photos and comps Details Bed / Bath: 3 / 2 Square Feet: 1,399 Year Build: 1925"
    assert generic_extract(qa)["sqft"] == 1399
    # Momentum — its *rent* line is called "Rental Comps".
    momentum = "Rental Comps: $2,800-$3,000/mo Property Details: Bed/Bath: 3/3 SQFT: 2,057 LOT SQFT: 6,665"
    assert generic_extract(momentum)["sqft"] == 2057


# ---- other correct abstentions -------------------------------------------


def test_sqft_ignores_a_prose_range_describing_an_addition():
    text = "a roof in good condition, and an additional 400-500 sqft that was added along with a second bath"
    assert "sqft" not in generic_extract(text)


def test_sqft_absent_on_a_land_listing_that_states_only_a_lot():
    # Southern Hills teardown: no house area exists to extract.
    text = "Price: $135k New Construction Value: ~$400k Property Stats Lot: ~7,031 SqFt (0.1614 Acres)"
    assert "sqft" not in generic_extract(text)


def test_sqft_absent_when_only_a_lot_size_is_given():
    text = "First Come First Serve Lot Size: 16,457 Sq. Ft. Lot Acres: .38"
    assert "sqft" not in generic_extract(text)


# ---- ARV: approximate markers (found by the 272-email audit) ---------------
#
# The gap between label and figure was colon/space/pipe only, so every Source
# that hedges its ARV was silently dropped. Fixtures are the live spellings.


def test_arv_with_a_tilde():
    # Southern Hills hedges every ARV it quotes.
    assert generic_extract("Price: $180k Est. ARV: ~$284k Property Stats")["arv"] == 284000


def test_arv_with_a_tilde_and_a_plus():
    assert generic_extract("Price: $195k Est. ARV: ~$245k+ Property Stats")["arv"] == 245000


def test_arv_with_a_plus_minus_marker():
    # Bumble Bee — "+-" before the figure.
    assert generic_extract("** Estimated ARV: +- $65,000")["arv"] == 65000


def test_arv_stated_in_prose():
    # ProphetHomes writes ARV as a sentence, not a field.
    text = "expect a major rehab. With ARV estimated at $345K+, this is a prime opportunity"
    assert generic_extract(text)["arv"] == 345000


def test_arv_range_sharing_one_k_suffix_scales_the_low_end():
    """"~$175-200k" — the k carries the magnitude for both ends. Reading the low
    end is the conservative choice for an ARV; reading it as a bare 175 would
    have been dropped by the plausibility floor, and mis-stripping the digit
    produced $17,000."""
    assert generic_extract("Price: $110k Estimated ARV: ~$175-200k Property")["arv"] == 175000


def test_arv_range_with_independent_dollar_amounts_takes_the_low_end():
    # "$975,000-$1,000,000" — both ends denominated, no shared suffix to apply.
    assert generic_extract("ARV: $975,000-$1,000,000 PICS")["arv"] == 975000


def test_arv_range_without_a_k_is_unchanged():
    assert generic_extract("ARV: $440,000-485,000 Rents: $2,850+")["arv"] == 440000


def test_approximate_marker_does_not_change_a_plain_amount():
    assert generic_extract("ARV: $250,000 Rental Comps: $1,600/mo")["arv"] == 250000


def test_arv_comparable_is_not_read_as_the_deals_arv():
    # shared1 quotes an "ARV Comparable" that already sold — a different house.
    text = "AS IS Comparable | SOLD for $275k on 7/24/25 ARV Comparable | SOLD for $515,000 on 8/7/25"
    assert "arv" not in generic_extract(text)


# ---- year built: bounded, and readable in either direction ----------------


def test_year_built_read_when_it_precedes_the_word():
    # New Western: "Off-Market vacant 2003 build in 76116" — reading only
    # forwards took the ZIP's first four digits (7611) and passed the gate.
    text = "Off-Market vacant 2003 build in 76116 with a pool for only 210k!"
    assert generic_extract(text)["year_built"] == 2003


def test_a_zip_code_is_never_read_as_a_year():
    text = "vacant 2003 build in 76116 with a pool"
    assert generic_extract(text)["year_built"] != 7611


def test_labeled_year_still_wins_and_is_unchanged():
    assert generic_extract("Bedrooms 2 Bathrooms 2.0 Year Built 1969 Cash Price $69,999")["year_built"] == 1969
    assert generic_extract("Year Build: 1968")["year_built"] == 1968
    assert generic_extract("Yr Build: 2002 Occupancy: Occupied")["year_built"] == 2002


def test_implausible_year_is_skipped_not_returned():
    # A street number abutting the word must not become a year.
    assert "year_built" not in generic_extract("build 7341 Baker Boulevard, Richland Hills")


def test_year_window_accepts_a_new_build_and_an_old_house():
    assert generic_extract("Year Built: 2026")["year_built"] == 2026
    assert generic_extract("Year Built: 1922")["year_built"] == 1922
