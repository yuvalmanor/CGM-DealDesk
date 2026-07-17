"""Address Normalizer (pure, deep module).

``normalize_address(raw) -> key`` — a cheap canonical form for one purpose: the
re-send lookup. Two spellings of the same street address collapse to one key
("123 Main St" == "123 Main Street"); two genuinely different houses never do.

Deliberately conservative. It canonicalizes *spelling* — case, punctuation,
whitespace, street-type and directional abbreviations, unit designators — and
nothing else. It does not geocode, correct typos, drop the city/state/zip, or
guess. Over-normalizing would make distinct houses collide and fabricate a "same
house" claim, which the operator's rule forbids (US 28): rows are never merged
on this key, so a *missed* re-send is a cheap miss while a *false* one is a lie.
That asymmetry decides every judgement call here.

An address that yields no tokens returns ``""``. The empty key never matches, so
an address-less Property is never called a re-send of another address-less one.
"""

from __future__ import annotations

import re

# Street types, abbreviation -> canonical. Both spellings map to the same key, so
# the direction of expansion is arbitrary; what matters is that it is total.
_STREET_TYPES = {
    "st": "street", "str": "street", "street": "street",
    "ave": "avenue", "av": "avenue", "aven": "avenue", "avenue": "avenue",
    "rd": "road", "road": "road",
    "dr": "drive", "drv": "drive", "drive": "drive",
    "ln": "lane", "lane": "lane",
    "blvd": "boulevard", "boul": "boulevard", "boulevard": "boulevard",
    "ct": "court", "court": "court",
    "cir": "circle", "circ": "circle", "circle": "circle",
    "pl": "place", "place": "place",
    "ter": "terrace", "terr": "terrace", "terrace": "terrace",
    "trl": "trail", "trail": "trail",
    "pkwy": "parkway", "pkway": "parkway", "parkway": "parkway",
    "hwy": "highway", "highway": "highway",
    "cv": "cove", "cove": "cove",
    "way": "way", "loop": "loop", "run": "run", "path": "path", "row": "row",
}

# Directionals, abbreviation -> canonical. "123 N Main St" == "123 North Main St".
_DIRECTIONALS = {
    "n": "north", "north": "north",
    "s": "south", "south": "south",
    "e": "east", "east": "east",
    "w": "west", "west": "west",
    "ne": "northeast", "northeast": "northeast",
    "nw": "northwest", "northwest": "northwest",
    "se": "southeast", "southeast": "southeast",
    "sw": "southwest", "southwest": "southwest",
}

# Unit designators all collapse to one token, so "Apt 4" == "Unit 4" == "#4".
# The unit *number* is kept — Apt 4 and Apt 5 are different homes.
# "no"/"num" are deliberately excluded: too likely to appear as ordinary words.
_UNIT_DESIGNATORS = {"apt", "apartment", "unit", "ste", "suite"}
_UNIT = "unit"

_STATES = {"tx": "texas", "texas": "texas"}

# Tokens that describe an address's *shape* rather than identify it. A key built
# from these alone ("unit", "north street") names no particular house, so it must
# not match anything — see ``normalize_address``.
_STRUCTURAL = (
    {_UNIT}
    | set(_DIRECTIONALS.values())
    | set(_STREET_TYPES.values())
    | set(_STATES.values())
)

# "#" is a unit designator glued to its number ("#4"); give it whitespace so it
# tokenizes, then let the designator rule fold it in.
_HASH_RE = re.compile(r"#")
# Anything that isn't a letter, digit, or space is a separator. This drops the
# commas/periods that vary freely between Sources ("Dallas, TX." == "Dallas TX").
_SEPARATOR_RE = re.compile(r"[^a-z0-9 ]+")


def normalize_address(raw: object) -> str:
    """Return the equivalence key for ``raw``, or ``""`` if there's nothing to key
    on. Equal keys mean "the same address, spelled differently" — never more."""
    if raw is None:
        return ""
    text = str(raw).strip().lower()
    if not text:
        return ""

    text = _HASH_RE.sub(f" {_UNIT} ", text)
    text = _SEPARATOR_RE.sub(" ", text)

    tokens: list[str] = []
    for token in text.split():
        canonical = _canonical(token)
        # "Unit #4" produces two unit tokens in a row; keep one.
        if canonical == _UNIT and tokens and tokens[-1] == _UNIT:
            continue
        tokens.append(canonical)

    # Nothing identifying survived (a stray "#", "Unit", "N St") — refuse to key
    # it. Otherwise two junk addresses would key alike and get called the same
    # house, exactly the claim this module must never fabricate.
    if not any(t not in _STRUCTURAL for t in tokens):
        return ""
    return " ".join(tokens)


def _canonical(token: str) -> str:
    if token in _UNIT_DESIGNATORS:
        return _UNIT
    if token in _DIRECTIONALS:
        return _DIRECTIONALS[token]
    if token in _STREET_TYPES:
        return _STREET_TYPES[token]
    if token in _STATES:
        return _STATES[token]
    return token
