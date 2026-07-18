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
_GAP = r"[:\s|]*\$?\s*"
_PRICE_RE = re.compile(
    r"(?:asking(?:\s+price)?|list(?:ing)?\s+price|purchase\s+price|price)\b" + _GAP + _AMOUNT,
    re.IGNORECASE,
)
_RENT_RE = re.compile(
    r"(?:market\s+rent|estimated\s+rent|est\.?\s+rent|monthly\s+rent|rent)\b" + _GAP + _AMOUNT,
    re.IGNORECASE,
)
_ARV_RE = re.compile(r"\barv\b" + _GAP + _AMOUNT, re.IGNORECASE)
# ``buil[dt]`` accepts the "Year Build" misspelling some Sources ship (ProphetHomes
# writes "Year Build: 1968") alongside the correct "Year Built".
_YEAR_RE = re.compile(r"(?:year\s+buil[dt]|yr\s+buil[dt]|buil[dt])\b[^\d]{0,8}(\d{4})", re.IGNORECASE)
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

    year = _YEAR_RE.search(text)
    if year:
        fields["year_built"] = int(year.group(1))

    _put_amount(fields, "monthly_rent", _RENT_RE, text, min_plausible=100)
    _put_amount(fields, "arv", _ARV_RE, text, min_plausible=10000)

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


def _plausible_address(value: str) -> bool:
    return bool(value) and len(value) <= _ADDRESS_MAX_LEN and _DIGIT_RE.search(value) is not None


def _put_amount(
    fields: dict, key: str, pattern: re.Pattern, text: str, *, min_plausible: float
) -> None:
    match = pattern.search(text)
    if not match:
        return
    raw = match.group(1)
    is_k = raw[-1] in "kK"
    norm = raw.replace(",", "")[:-1] if is_k else raw.replace(",", "")
    try:
        val = float(norm) * 1000 if is_k else float(norm)
    except ValueError:
        return
    # A bare number below the floor is junk (a price-per-sqft or ad figure caught
    # by the label); k-shorthand is explicit, so it is trusted as-is.
    if not is_k and val < min_plausible:
        return
    fields[key] = int(val) if val.is_integer() else val
