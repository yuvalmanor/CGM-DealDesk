# Reference — report schema, pricing, cost

## JSON report schema (`--report PATH`)

Written by `dealdesk run ... --report PATH` ([cli.py](../../../src/dealdesk/cli.py)
`_write_report`). Top-level fields:

| Field | Meaning |
|---|---|
| `model` | AI-fallback model id (drives which price row to use), e.g. `claude-opus-4-8`. |
| `emails_processed` | Count of emails triaged this run. |
| `counts` | `{bucket: n}` — the category breakdown. |
| `ai_used` | Emails on which the AI fallback fired. |
| `ai_calls` | Fallback API calls (≈ `ai_used`). |
| `ai_tokens` | `{input, output}` — summed real token usage across all calls. |
| `ai_by_bucket` | `{bucket: {total, ai}}` — per-bucket AI-fallback rate numerator/denominator. |
| `fallback_reasons` | `{reason: count}`, already sorted high→low. Key = the missing must-have field(s) that triggered AI (`" + "`-joined; `unknown` if none recorded). |
| `emails[]` | Per email: `bucket`, `used_ai`, `ai_missing_fields[]`, `sender`, `subject`, `error`, and `properties[]` each `{address, verdict, calc_ready, fed, reasons[]}`. |

The `reasons[]` on each property are the **evaluation** reasons (why the Buy Box
gate passed/failed, e.g. `year_built 1970 fails >= 2000`). `ai_missing_fields` /
`fallback_reasons` are the **extraction-time trigger** (why AI was called) — a
different thing; use each where the report asks.

## Pricing (per 1,000,000 tokens, USD)

Verify current rates with the `claude-api` skill before quoting; these are the
snapshot to fall back on.

| Model | Input | Output |
|---|---|---|
| `claude-opus-4-8` | $5.00 | $25.00 |
| `claude-haiku-4-5-20251001` | $1.00 | $5.00 |

The pipeline's model is set by `[ai].model` in `config/dealdesk.toml` and echoed in
the report's `model` field — pick the matching row.

## Cost formula

```
cost = ai_tokens.input  / 1_000_000 * input_rate
     + ai_tokens.output / 1_000_000 * output_rate
```

Report input cost, output cost, and total. Also useful: cost per AI email
(`cost / ai_used`) and cost per email overall (`cost / emails_processed`). No prompt
caching is used, so every input token is billed at full rate — worth noting if the
operator asks how to cut cost (cache the fixed prompt prefix, or switch `[ai].model`
to Haiku).
