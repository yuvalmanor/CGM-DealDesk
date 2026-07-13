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

# Each pattern captures the value group. Kept intentionally simple and label-led;
# real per-Source parsing is deferred to Template phases.
_PRICE_RE = re.compile(
    r"(?:asking(?:\s+price)?|list(?:ing)?\s+price|purchase\s+price|price)\b[^\d$]{0,12}\$?\s*([\d,]{3,})",
    re.IGNORECASE,
)
_RENT_RE = re.compile(
    r"(?:market\s+rent|estimated\s+rent|est\.?\s+rent|monthly\s+rent|rent)\b[^\d$]{0,12}\$?\s*([\d,]{3,})",
    re.IGNORECASE,
)
_ARV_RE = re.compile(r"\barv\b[^\d$]{0,12}\$?\s*([\d,]{3,})", re.IGNORECASE)
_YEAR_RE = re.compile(r"(?:year\s+built|yr\s+built|built)\b[^\d]{0,8}(\d{4})", re.IGNORECASE)
_BEDS_RE = re.compile(r"(\d+)\s*(?:beds?|br|bd)\b", re.IGNORECASE)
_BATHS_RE = re.compile(r"(\d+(?:\.\d)?)\s*(?:baths?|ba)\b", re.IGNORECASE)
_ADDRESS_LABEL_RE = re.compile(r"(?:address|property)\s*[:\-]\s*(.+)", re.IGNORECASE)
_CITY_LABEL_RE = re.compile(r"\bcity\s*[:\-]\s*([A-Za-z .'\-]+)", re.IGNORECASE)
# Fallback city derivation: "..., Fort Worth TX 76101" or "..., Dallas, TX".
_CITY_FROM_ADDR_RE = re.compile(r",\s*([A-Za-z .'\-]+?)\s*,?\s*(?:TX|Texas)\b", re.IGNORECASE)


def generic_extract(text: str) -> dict:
    fields: dict[str, object] = {}
    _put_number(fields, "purchase_price", _PRICE_RE, text)
    _put_number(fields, "monthly_rent", _RENT_RE, text)
    _put_number(fields, "arv", _ARV_RE, text)

    year = _YEAR_RE.search(text)
    if year:
        fields["year_built"] = int(year.group(1))

    beds = _BEDS_RE.search(text)
    if beds:
        fields["beds"] = int(beds.group(1))
    baths = _BATHS_RE.search(text)
    if baths:
        fields["baths"] = float(baths.group(1))

    address = _ADDRESS_LABEL_RE.search(text)
    if address:
        fields["address"] = address.group(1).strip()

    city = _CITY_LABEL_RE.search(text)
    if city:
        fields["city"] = city.group(1).strip()
    elif "address" in fields:
        m = _CITY_FROM_ADDR_RE.search(str(fields["address"]))
        if m:
            fields["city"] = m.group(1).strip()

    return fields


def _put_number(fields: dict, key: str, pattern: re.Pattern, text: str) -> None:
    match = pattern.search(text)
    if match:
        fields[key] = int(match.group(1).replace(",", ""))
