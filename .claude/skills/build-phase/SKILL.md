---
name: build-phase
description: Implement the next unbuilt phase of a feature's phased plan in ./plans/, run the project's mandatory gates, check off acceptance criteria, and commit+push. Use when the user invokes /build-phase with a feature slug (e.g. /build-phase lender-comparison) or asks to implement, build, or continue the next phase of a planned feature.
---

# Build Phase

Implements exactly one phase — the next incomplete one — of a feature plan produced by /plan-phases.

**Argument**: the feature slug. The plan file is `plans/<slug>.md`. If no argument is given or the file doesn't exist, list the files in `plans/` and ask which feature to build.

## Process

### 1. Load context (in this order)

1. `CLAUDE.md` — follow its Required Reading Order and Ground Rules; they override everything here.
2. `CONTEXT.md` — use the glossary's canonical terms in code, UI copy, and commit messages.
3. `docs/adr/*.md` — respect every accepted decision; never "fix" something an ADR declares deliberate.
4. `plans/<slug>.md` — the plan. Its **Architectural decisions** header applies to every phase.

### 2. Find the current phase

The current phase is the **first** phase whose acceptance criteria contain an unchecked `- [ ]` box.

- If every box in the plan is checked: report that the feature is complete and stop.
- If an earlier phase has a mix of checked and unchecked boxes, that phase is still current — finish it; don't skip ahead.

### 3. Confirm scope, then implement

State which phase you're building and what it covers, then implement **that phase only**. Do not start the next phase, do not refactor beyond the phase's scope, and do not revisit decisions recorded in the plan header, CONTEXT.md, or ADRs.

### 4. Run the mandatory gates

Run every verification the project requires (per CLAUDE.md), typically:

```bash
npm test          # golden tests — all green
npx tsc --noEmit  # zero errors
npm run build     # must succeed
```

All gates must pass. Zero tolerance — a failure stops work until fixed. Never weaken a gate to make it pass.

### 5. Check off acceptance criteria

For each criterion in the current phase, verify it against the running behavior — not against "the code looks right". Mark verified criteria `- [x]` in `plans/<slug>.md`. A criterion you could not genuinely verify stays unchecked; say so in the report rather than checking it optimistically.

### 6. Commit and push

Commit the implementation **together with the plan-file checkbox updates**. Message format:

```
<Feature name> phase <N>: <short description of the slice>
```

Push per the project's git process (see CLAUDE.md).

### 7. Report

End with: which phase was built, criteria status (checked / left unchecked and why), gate results, and which phase is next (or that the plan is complete).

## Rules

- One phase per invocation. Finishing early is not a license to start the next phase.
- If a phase's acceptance criteria conflict with reality (e.g. the codebase changed since planning), stop and surface the conflict instead of silently reinterpreting the plan.
- If implementation reveals a needed change to a durable decision in the plan header, stop and ask — that's a planning decision, not an implementation detail.
