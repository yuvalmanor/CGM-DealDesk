"""AI extraction fallback — last rung of the Extraction Ladder.

Per ADR-0001, the Anthropic API is a **last-resort** extraction fallback, used
only when the deterministic rungs can't recover the must-have fields. It sits
behind the ``AiFallback`` protocol so the ladder can be tested with a fake and so
an unknown Source still parses (a brand-new wholesaler's mail isn't dropped just
because it has no Template yet).

``anthropic`` is imported lazily inside the call so the test suite and the
template-only happy paths don't require the SDK installed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

# Field names the model is asked to extract — the union of what the Buy Box and
# Calculator care about, plus the address for the Triage Log / re-send lookup.
_EXTRACT_FIELDS = (
    "address",
    "city",
    "property_type",
    "purchase_price",
    "monthly_rent",
    "arv",
    "year_built",
    "beds",
    "baths",
)

# Controlled vocabulary for ``property_type`` so the Buy Box's membership gate can
# match it deterministically. The model must pick one of these or omit the field
# when it can't tell — never invent a value (an omitted type reaches a human as
# Needs-Human rather than being falsely Rejected).
_PROPERTY_TYPES = (
    "single_family",
    "multi_family",
    "condo",
    "townhouse",
    "mobile_home",
    "manufactured",
    "vacant_lot",
    "land",
    "commercial",
)


@runtime_checkable
class AiFallback(Protocol):
    """Extract every Property from free text. Returns one ``property_fields``
    dict per Property (empty list when the text holds no deal)."""

    def extract_properties(self, text: str) -> list[dict]: ...


class AnthropicFallback:
    """Real fallback backed by the Anthropic Messages API with structured
    (JSON-schema) output. Cheap-by-design: only invoked when deterministic
    extraction fails."""

    def __init__(self, model: str, api_key: str | None = None):
        self._model = model
        self._api_key = api_key

    def extract_properties(self, text: str) -> list[dict]:
        import anthropic  # lazy: only needed when the fallback actually fires

        client = anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()
        response = client.messages.create(
            model=self._model,
            max_tokens=4096,
            output_config={"format": {"type": "json_schema", "schema": _SCHEMA}},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Extract every distinct property offered in the text below "
                        "into the `properties` array. One object per property. Omit "
                        "any field you cannot find rather than guessing. For "
                        "`address`, return the complete single-line mailing address "
                        "including city, state, and ZIP when present, e.g. "
                        "`373 Bellvue Dr, Fort Worth, TX 76134` — not just the street "
                        "line. Also fill `city` with just the city name (for filtering). "
                        "For `property_type`, classify into one of the schema's allowed "
                        "values (a house is `single_family`; a vacant/build-ready/"
                        "commercial lot is `vacant_lot`, `land`, or `commercial`); "
                        "omit it if genuinely unclear.\n"
                        "For money fields, report the current asking figure for THIS "
                        "property: when a reduced price is shown (e.g. '$283k $263k'), "
                        "use the lower, most-recent one; never use a sold-comparable, an "
                        "estimated after-repair value, or a price-per-square-foot figure "
                        "as `purchase_price`. If a rent is given as a range, use the "
                        "higher figure. If two genuinely conflicting prices appear with no "
                        "way to tell which is current, omit `purchase_price`.\n\n"
                        f"{text}"
                    ),
                }
            ],
        )
        import json

        payload = next((b.text for b in response.content if b.type == "text"), "")
        data = json.loads(payload) if payload else {}
        return [_clean(p) for p in data.get("properties", [])]


_PROPERTY_SCHEMA = {
    "type": "object",
    "properties": {
        "address": {
            "type": "string",
            "description": (
                "Complete single-line mailing address including city, state, and ZIP "
                "when present, e.g. '373 Bellvue Dr, Fort Worth, TX 76134'."
            ),
        },
        "city": {"type": "string", "description": "City name only, for filtering."},
        "property_type": {"type": "string", "enum": list(_PROPERTY_TYPES)},
        "purchase_price": {"type": "number"},
        "monthly_rent": {"type": "number"},
        "arv": {"type": "number"},
        "year_built": {"type": "integer"},
        "beds": {"type": "integer"},
        "baths": {"type": "number"},
    },
    "additionalProperties": False,
}

_SCHEMA = {
    "type": "object",
    "properties": {"properties": {"type": "array", "items": _PROPERTY_SCHEMA}},
    "required": ["properties"],
    "additionalProperties": False,
}


def _clean(prop: dict) -> dict:
    """Keep only recognized, non-null fields."""
    return {k: v for k, v in prop.items() if k in _EXTRACT_FIELDS and v not in (None, "")}
