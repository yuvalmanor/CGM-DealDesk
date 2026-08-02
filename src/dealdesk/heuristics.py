"""Generic extraction heuristics (pure).

The cheap, deterministic first rung of the Extraction Ladder — regexes over the
combined body + PDF text that recover the common labeled fields many Sources
share. This is *not* a per-Source Template (those come in Phase 8+); it's the
template-free baseline. When it can't recover the must-have fields, the ladder
falls through to the AI fallback.

Returns a single ``property_fields`` dict of the fields it found (only keys it
actually recovered). Multi-Property detection is left to the AI fallback.
"""

from __future__ import annotations

import re

# Money fields share one shape: a label, then (only) a colon/whitespace gap, an
# optional ``$``, then the amount. The gap is deliberately *tight* — colon and
# whitespace only, not the old ``[^\d$]{0,12}`` "anything up to 12 chars". That
# loose gap was the root cause of three live mis-parses: it jumped ``"/SqFt: "``
# in ``Price/SqFt: $187.63`` (→ 187) and ``") * Up to "`` in ad copy (→ 100) to
# grab an unrelated number. Whitespace in the gap still covers table layouts
# ("Cash Price\n\n$97,500"), because ``\s`` matches the newlines.
#
# The amount fragment accepts k-shorthand ("$150k" → 150000); ``_put_amount``
# scales it. Without this, "$150k" parsed as 150 and any sub-$100k k-price (e.g.
# "$95k") was dropped entirely by the old ``{3,}`` minimum.
_AMOUNT = r"([\d,]+(?:\.\d+)?[kK]?)"
# The gap allows a colon, whitespace, or a pipe between label and value. The pipe
# covers table/list layouts several Sources use ("Purchase Price | $170,000 OBO"
# — LUSH), which a colon/space-only gap stopped dead at. It stays tight otherwise:
# only these separators, never letters, so it can't jump across a labeled column
# into an unrelated number.
#
# The gap also admits an *approximate* marker — "Est. ARV: ~$284k",
# "Estimated ARV: +- $65,000". These are punctuation, not letters, so they cannot
# jump a label into an unrelated number the way the old loose gap did; without
# them Southern Hills' every listing and Bumble Bee's ARV were silently dropped.
_GAP = r"[:\s|]*(?:~|≈|\+/-|\+-)?\s*\$?\s*"
_PRICE_RE = re.compile(
    r"(?:asking(?:\s+price)?|list(?:ing)?\s+price|purchase\s+price|price)\b" + _GAP + _AMOUNT,
    re.IGNORECASE,
)
_RENT_RE = re.compile(
    r"(?:market\s+rent|estimated\s+rent|est\.?\s+rent|monthly\s+rent|rent)\b" + _GAP + _AMOUNT,
    re.IGNORECASE,
)
# ``estimated at`` is the one *worded* connector allowed between the label and the
# figure ("With ARV estimated at $345K+" — ProphetHomes writes ARV in prose, not
# as a field). It is spelled out rather than allowed as a general word gap: an
# open gap is what caused the three historical price mis-parses noted above.
# "175-200k" — the trailing k carries the magnitude for both ends of the range.
# A "$" on the upper bound means the two are independently denominated
# ("$975,000-$1,000,000"), so that form is deliberately not matched.
_SHARED_K_RANGE_RE = re.compile(r"^\s*-\s*[\d,.]+\s*[kK]\b")
_ARV_RE = re.compile(
    r"\barv\b(?:\s+estimated\s+at)?" + _GAP + _AMOUNT, re.IGNORECASE
)
# ``buil[dt]`` accepts the "Year Build" misspelling some Sources ship (ProphetHomes
# writes "Year Build: 1968") alongside the correct "Year Built".
_YEAR_RE = re.compile(r"(?:year\s+buil[dt]|yr\s+buil[dt]|buil[dt])\b[^\d]{0,8}(\d{4})", re.IGNORECASE)
# Some Sources put the year *before* the word ("Off-Market vacant 2003 build in
# 76116"). Reading only forwards, the label pattern skipped straight past it and
# took the first four digits of the ZIP — 7611, which then sailed through the
# ``year_built >= 2000`` gate. A fabricated fact deciding a gate is the worst
# failure this pipeline has, so the year is now bounded as well as anchored: a
# ZIP or a street number can no longer be a year, and a match outside the window
# is skipped rather than accepted.
_YEAR_VALUE_FIRST_RE = re.compile(r"\b(\d{4})\s+buil[dt]\b", re.IGNORECASE)
_YEAR_MIN, _YEAR_MAX = 1800, 2030
# Beds/baths come in two orientations. Prose is value-first ("3 bed / 2 bath");
# the table layouts HTML Sources use are label-first ("Beds\n5"). Value-first is
# tried first because it is the less ambiguous of the two — run label-first
# against "3 bed / 2 bath" and it happily reads the *baths* number as beds.
#
# Only horizontal space may sit between a value and its label: a newline means
# the number belongs to the previous label in a table ("$279,990\n\nBeds\n5"),
# where reading it value-first yields 990 beds. The lookbehind stops a count
# being lopped off the tail of a larger number for the same reason.
_BEDS_RE = re.compile(r"(?<![\d,])(\d+)[ \t]*(?:beds?|br|bd)\b", re.IGNORECASE)
_BATHS_RE = re.compile(r"(?<![\d,])(\d+(?:\.\d)?)[ \t]*(?:baths?|ba)\b", re.IGNORECASE)
_BEDS_LABEL_RE = re.compile(r"\b(?:bedrooms?|beds?)\b\D{0,10}?(\d+)", re.IGNORECASE)
_BATHS_LABEL_RE = re.compile(r"\b(?:bathrooms?|baths?)\b\D{0,10}?(\d+(?:\.\d)?)", re.IGNORECASE)
# Square footage — the *building* area, and never the lot. Both are quoted, often
# in the same line ("Building Area: 1,868 SQFT ... Lot Size: 7,405 SQFT"), and no
# numeric range separates them: a 7,405 sq ft lot is a perfectly plausible house.
# Only *context* tells them apart, so this is two ordered patterns rather than one.
#
# The label spellings seen across the live Sources:
#   "Square Footage: 2,954"   "SQ FT: 1,610"      "Building Area: 1,868 SQFT"
#   "SQFT: 2,057"             "Living area: 2,100 sqft"
#   "Square Feet: 1,773"      "House: 1,672 SqFt"
_SQFT_LABELS = (
    r"(?:square\s+footage|square\s+f(?:ee)?t|building\s+area|living\s+area"
    r"|sq\.?\s*ft\.?|sqft)"
)
# Label-first, tried FIRST because it is the unambiguous form — the label names
# the building, so the lot figure later in the same line can't win. The lookbehind
# refuses the lot's *own* label, which Momentum writes as "LOT SQFT: 6,665".
# Requiring a real separator keeps it tight (the same reason the money gap is
# tight): "as-is property square footage measurements" — New Western's
# disclaimer — carries no colon and so matches nothing.
_SQFT_LABEL_RE = re.compile(
    r"(?<!lot\s)" + _SQFT_LABELS + r"\s*[:|]\s*([\d,]{3,})", re.IGNORECASE
)
# Value-first ("3 beds 2 baths 2,118 sqft" — InvestorLift; "2039 Sqft" — Rise).
# Only reached when nothing labeled was found, because this is where the traps
# live. The lookbehind rejects a money figure: A-Team writes "EMD: $5,000 SQ FT:
# 1,610", where the earnest deposit sits directly against the SQ FT label and
# would otherwise be read as the area.
# The ``-`` also guards a prose range: ProphetHomes writes "an additional 400-500
# sqft that was added", where the tail of the range is not the house's area.
_SQFT_VALUE_RE = re.compile(
    r"(?<![$\d,.\-])([\d,]{3,})\s*" + _SQFT_LABELS + r"\b", re.IGNORECASE
)
# A comparable's area is another house's area — the single biggest source of
# wrong answers in the live mail. Six Sources quote sold comps with their own
# square footage, and they are NOT separable by position: Q Acquisitions writes
# "Click Here for Photos and comps" *before* the subject property's own stats, so
# truncating at the first mention of "comps" would throw the real answer away.
#
# The test is therefore local to each candidate — does the text immediately
# around this figure describe a *sale that already happened*? That is what every
# comp block has in common ("SOLD for $385,000 3 bed / 2 bath 1,776 SqFt",
# "SqFt: 1,983 Sold For: $309,000", "Price/SqFt: $155.82") and what no subject
# listing has.
# Deliberately keyed on the *sale verb*, never on the word "comp". Two live
# spellings put "comps" right beside a subject property's own figures — Q
# Acquisitions' "Click Here for Photos and comps" precedes its stats block, and
# Momentum's rent line reads "Rental Comps: $2,800-$3,000/mo" — so matching the
# noun rejected 13 correct answers. A completed sale is the thing only a
# comparable has.
# A comps *section header* is also enough, but only in its strict form — the word
# followed by a colon, and not the rent line. That distinction is the whole
# reason this is not simply ``\bcomps?\b``:
#   "COMPS: 5522 Challenger Court | $459,000 | 2,315 SQFT"  -> header, reject
#   "Rental Comps: $2,800-$3,000/mo"                        -> a rent, keep
#   "Click Here for Photos and comps"                       -> a link, keep
_COMPARABLE_RE = re.compile(
    r"\bsold\b|\bclosed\s+for\b|recent\s+sales?\b|price\s*/\s*sq|\blisting\s+id\b"
    r"|(?<!rental\s)(?<!rent\s)\bcomp(?:s|arables?)?\s*:",
    re.IGNORECASE,
)
_COMP_LOOKBACK = 90
_COMP_LOOKAHEAD = 40
# "Lot Size: 7,405 SQFT" — value-first shaped, so the lookbehind can't see it.
# Checked against the short run of text before the number instead.
_LOT_NEAR_RE = re.compile(r"\blot\b[^\d]{0,12}$", re.IGNORECASE)
_LOT_LOOKBACK = 16
# An area belonging to an outbuilding, not the house: Lush quotes "a 460 Sq. Ft.
# Carport (Added in 1978)". No size floor separates these — 460 is small, but a
# 900 sq ft detached garage is not — so this is a structural test on what the
# figure is *called*, like the lot guard above.
# The negative lookahead on ``:`` is what separates "a 460 Sq. Ft. Carport" (the
# figure *belongs* to the outbuilding) from "Size: 1,114 SqFt Garage: 1 Car" (the
# figure is the house's and an unrelated field simply comes next). Without it
# Southern Hills' listings returned the garage's 325 sq ft as the house.
_OUTBUILDING_RE = re.compile(
    r"^\W{0,3}(?:carport|garage|shed|barn|patio|porch|workshop|storage|addition|casita)"
    r"\b(?!\s*:)",
    re.IGNORECASE,
)
_OUTBUILDING_LOOKAHEAD = 14
# Below this it is not a dwelling at all. There is deliberately no *upper* bound:
# every lot size in the live mail (5,450 / 6,665 / 7,405 / 8,669) is also a
# plausible house, so a ceiling would buy nothing and would eventually drop a
# real one. Discrimination is contextual, above — never numeric.
_SQFT_MIN = 300

_ADDRESS_LABEL_RE = re.compile(r"(?:address|property)\s*[:\-]\s*(.+)", re.IGNORECASE)
# The label alone is too weak a signal: boilerplate headings ("NO UNACCOMPANIED
# ENTRY OF PROPERTY: Broker and its affiliates...") match it and would capture a
# paragraph of legalese. A mailing address always carries a number (street number
# and/or ZIP) and is short. A *wrong* address is worse than none — it reaches the
# Triage Log and seeds the re-send lookup — so a candidate must look like one.
_ADDRESS_MAX_LEN = 100
_DIGIT_RE = re.compile(r"\d")
_CITY_LABEL_RE = re.compile(r"\bcity\s*[:\-]\s*([A-Za-z .'\-]+)", re.IGNORECASE)
# Fallback city derivation: "..., Fort Worth TX 76101" or "..., Dallas, TX".
_CITY_FROM_ADDR_RE = re.compile(r",\s*([A-Za-z .'\-]+?)\s*,?\s*(?:TX|Texas)\b", re.IGNORECASE)


def generic_extract(text: str) -> dict:
    fields: dict[str, object] = {}
    # Plausibility floors reject the small-int junk the label match can still grab
    # (187 from "Price/SqFt", 100 from ad copy) without dropping real values. A
    # house price/ARV is never a bare number below $10k; a monthly rent is never
    # below $100. k-shorthand bypasses the floor — it is an explicit magnitude.
    # The two gate fields (purchase_price, year_built) lead so the fields the
    # Buy Box decides on are extracted first; rent/arv are calc-only. Order does
    # not affect the returned dict, only reading.
    _put_amount(fields, "purchase_price", _PRICE_RE, text, min_plausible=10000)

    year = _extract_year(text)
    if year is not None:
        fields["year_built"] = year

    _put_amount(fields, "monthly_rent", _RENT_RE, text, min_plausible=100)
    _put_amount(fields, "arv", _ARV_RE, text, min_plausible=10000)

    sqft = _extract_sqft(text)
    if sqft is not None:
        fields["sqft"] = sqft

    beds = _BEDS_RE.search(text) or _BEDS_LABEL_RE.search(text)
    if beds:
        fields["beds"] = int(beds.group(1))
    baths = _BATHS_RE.search(text) or _BATHS_LABEL_RE.search(text)
    if baths:
        fields["baths"] = float(baths.group(1))

    # Scan every labeled candidate, not just the first: boilerplate can precede
    # the real address in the text.
    for match in _ADDRESS_LABEL_RE.finditer(text):
        candidate = match.group(1).strip()
        if _plausible_address(candidate):
            fields["address"] = candidate
            break

    city = _CITY_LABEL_RE.search(text)
    if city:
        fields["city"] = city.group(1).strip()
    elif "address" in fields:
        m = _CITY_FROM_ADDR_RE.search(str(fields["address"]))
        if m:
            fields["city"] = m.group(1).strip()

    return fields


def looks_single_property(text: str) -> bool:
    """A cheap multi-listing guard for the ladder's confident-Reject short-circuit.

    ``generic_extract`` only ever reads the *first* listing in the text, so the
    ladder must not let a heuristic Reject stand for a whole Email that actually
    carries several listings — the first deal being out-of-range says nothing
    about the ones behind it. Count the strongest per-listing markers (priced
    amounts and plausible labeled addresses); more than one of either means the
    Email may hold multiple Properties, so defer to the AI rung. The bias is
    deliberately conservative: when unsure, return False (spend a token) rather
    than risk a silent false Reject that drops in-range listings."""
    prices = len(_PRICE_RE.findall(text))
    addresses = sum(
        1 for m in _ADDRESS_LABEL_RE.finditer(text) if _plausible_address(m.group(1).strip())
    )
    return prices <= 1 and addresses <= 1


def _extract_year(text: str) -> int | None:
    """The year the house was built, or None. The labeled form is tried first —
    it is the explicit one — but any candidate outside a plausible year window is
    skipped rather than returned, which is what lets the search fall through to
    the value-first spelling instead of stopping on a ZIP code."""
    for pattern in (_YEAR_RE, _YEAR_VALUE_FIRST_RE):
        for match in pattern.finditer(text):
            year = int(match.group(1))
            if _YEAR_MIN <= year <= _YEAR_MAX:
                return year
    return None


def _extract_sqft(text: str) -> int | None:
    """The property's building area, or None. Labeled spelling wins; the
    unlabeled one is a guarded fallback (see the patterns above for why the
    order matters)."""
    for match in _SQFT_LABEL_RE.finditer(text):
        if _describes_a_comparable(text, match):
            continue
        value = _sqft_value(match.group(1))
        if value is not None:
            return value

    for match in _SQFT_VALUE_RE.finditer(text):
        if _describes_a_comparable(text, match):
            continue
        before = text[max(0, match.start() - _LOT_LOOKBACK) : match.start()]
        if _LOT_NEAR_RE.search(before):
            continue  # the lot's area, not the house's
        after = text[match.end() : match.end() + _OUTBUILDING_LOOKAHEAD]
        if _OUTBUILDING_RE.match(after):
            continue  # a carport/garage's area, not the house's
        value = _sqft_value(match.group(1))
        if value is not None:
            return value
    return None


def _describes_a_comparable(text: str, match: re.Match) -> bool:
    """Is this figure part of a sold-comparable rather than the deal on offer?
    Judged from the text immediately around it — see ``_COMPARABLE_RE``."""
    window = text[
        max(0, match.start() - _COMP_LOOKBACK) : match.end() + _COMP_LOOKAHEAD
    ]
    return _COMPARABLE_RE.search(window) is not None


def _sqft_value(raw: str) -> int | None:
    try:
        value = int(raw.replace(",", ""))
    except ValueError:
        return None
    return value if value >= _SQFT_MIN else None


def _plausible_address(value: str) -> bool:
    return bool(value) and len(value) <= _ADDRESS_MAX_LEN and _DIGIT_RE.search(value) is not None


def _put_amount(
    fields: dict, key: str, pattern: re.Pattern, text: str, *, min_plausible: float
) -> None:
    match = pattern.search(text)
    if not match:
        return
    raw = match.group(1)
    # Two distinct questions, and conflating them is a bug: whether the captured
    # text *ends* in a "k" (so the character must be stripped before parsing), and
    # whether the value is in thousands (so it must be scaled). They differ for a
    # range whose k-suffix sits only on the upper bound — "ARV: ~$175-200k", where
    # the magnitude is shared but the captured "175" carries no k of its own.
    # Stripping a character there turned $175,000 into $17,000.
    has_k_suffix = raw[-1] in "kK"
    in_thousands = has_k_suffix or bool(
        _SHARED_K_RANGE_RE.match(text[match.end() : match.end() + 14])
    )
    norm = raw.replace(",", "")[:-1] if has_k_suffix else raw.replace(",", "")
    try:
        val = float(norm) * 1000 if in_thousands else float(norm)
    except ValueError:
        return
    # A bare number below the floor is junk (a price-per-sqft or ad figure caught
    # by the label); k-shorthand is explicit, so it is trusted as-is.
    if not in_thousands and val < min_plausible:
        return
    fields[key] = int(val) if val.is_integer() else val
