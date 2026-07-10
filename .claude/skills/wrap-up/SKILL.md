---
name: wrap-up
description: Compact the current conversation into a structured handoff document for the next session to pick up. Use when user types /wrap-up, wants to wrap up a session, close out, or asks for an end-of-session summary. Surveys git state and tasks, distils only load-bearing context for the next session, and asks before committing.
argument-hint: "What will the next session be used for?"
---

# Handoff

Produce a session handoff document that a cold reader absorbs in under 30 seconds and acts on. **Filter aggressively** — most of a session is forgettable execution; preserve only the ~5% that changes what the next session does.

Write it to a file in the OS temp directory (`$TMPDIR`, falling back to `/tmp`), named `handoff-<branch>-<YYYY-MM-DD>.md` — not the current workspace.

Include a "suggested skills" section in the document, which suggests skills that the agent should invoke.

Do not duplicate content already captured in other artifacts (PRDs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.

## 1. Survey what happened

There is no git-native "session start", so gather state from several angles and reconcile:

```bash
git status --short                # uncommitted + untracked
git log @{u}..HEAD --oneline      # commits not yet pushed (usually = this session)
git log --oneline -15             # recent history for context if no upstream
git diff --stat HEAD              # size/shape of uncommitted work
```

If `@{u}` errors (no upstream), fall back to `git log --oneline -15` and judge which are this session's from the conversation. Also reconcile against what you actually did this turn — the conversation is the ground truth for "this session", git is corroboration.

Check task state: which TODOs got marked done vs. are still in-flight.

## 2. Distil load-bearing context

Keep only what a cold reader **needs** to act correctly next time:

- **Decisions** made, with the *why* (especially ones that look arbitrary later)
- **Constraints** discovered that rule approaches in or out
- **Half-finished work** — where it stopped, what's left, how to resume
- **Footguns** — surprising state, broken assumptions, things that bit you

Discard: commands run, files read, passing tests, anything already legible in the code or git log. **If the session produced nothing load-bearing, say so — do not manufacture content.**

## 3. Offer to commit (don't commit silently)

If `git status` shows uncommitted changes worth committing (a coherent completed unit — not scratch/WIP), summarize them and **ask** before committing. If it's clearly in-progress work, leave it and just note it in the handover.

## 4. Write and print the handoff note

The file content **is** the note below; print the same content to the user. Tight bullets, 1–3 per section. Omit any section that's genuinely empty rather than padding it.

Finish with a single copy-paste sentence — a prompt the user pastes verbatim into the next session — that states the absolute file path **and** how to use it, e.g.:

> Read `/tmp/handoff-<branch>-<date>.md` for full session context, then pick up the in-flight work described there.

---

## Handoff — [project / branch]

**Shipped** · commits, deploys
- `[hash]` [one line]  — or "nothing committed this session"

**Decisions** · choices made and the *why*
- [decision — why, especially if it'll look arbitrary later]

**Constraints** · what rules approaches in or out
- [constraint discovered this session]

**In flight** · enough context to resume cold
- [what's half-done, where it stopped, next step]

**Watch-outs** · gotchas, surprising state, broken assumptions
- [only if real]

**Open questions for you**
- [decisions deferred to the human]

**Suggested skills** · what the next session should invoke
- [skill — why it's relevant]

---

When in doubt, cut. A 30-second note that's all signal beats a complete one nobody rereads.
