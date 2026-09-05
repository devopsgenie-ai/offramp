# The RFC process

Substantial changes to `offramp` are designed in public, in writing, before they are
built. This document is the process. It is short on purpose.

## When you need an RFC

You need one to change **what the tool produces or how it decides** — a new scenario, a
new target, a change to the AppSpec, a change to the gap model, anything that alters
generated output, and any change to this process itself.

You do not need one for documentation, tests covering existing behaviour, CI changes,
typo and formatting fixes, dependency bumps, or a bug fix that makes the code match an
already-accepted RFC.

When in doubt, write one. They are cheap, and "rejected" is a perfectly good outcome that
leaves a record of why.

## Lifecycle

```
draft ──▶ review ──▶ accepted ──▶ implemented
             │
             └────▶ rejected

accepted ──▶ superseded   (by a later RFC)
```

| Status | Meaning |
|---|---|
| `draft` | Being written. Open a PR early; drafts are for thinking in public. |
| `review` | Author is asking for a decision. Comment period is open. |
| `accepted` | Approved. Implementation may begin, and must follow it. |
| `rejected` | Not proceeding. The document stays as a record of the reasoning. |
| `implemented` | Shipped. The code is now the authority on detail; the RFC records intent. |
| `superseded` | Replaced by a later RFC, named in `superseded_by`. |

An accepted RFC is **immutable in substance**. Fix typos and broken links freely; change
a decision by writing a new RFC that supersedes it.

## How to submit one

1. Copy `0000-template.md` to `NNNN-short-slug.md`, where `NNNN` is the next unused
   number, zero-padded to four digits.
2. Fill in the frontmatter and every section. Delete no headings — if a section does not
   apply, say so and why in one line.
3. Open a pull request titled `RFC-NNNN: <title>` with `status: draft`.
4. Move to `status: review` when you want a decision. Say so in the PR.
5. A maintainer merges with `status: accepted` or `status: rejected`. The merge is the
   decision; the PR thread is the record of how it was reached.

## Review criteria

An RFC is judged on whether it:

- states the problem before the solution, in terms of what a user cannot do today
- names at least one alternative that was seriously considered, and why it lost
- respects the declarative constraint in `AGENTS.md`, or argues explicitly for an
  exception
- says how the change will be tested — for anything touching generated output, that
  means golden fixtures
- says what it deliberately leaves out

The most common reason to reject is scope: an RFC that changes three things is three
RFCs.

## Implementation

Implementation pull requests reference the RFC number in the body, as `RFC-0001`. CI
checks that the referenced RFC exists and is `accepted`.

When implementation is complete, a follow-up PR moves the RFC to `status: implemented`
and adds a link to the code. From that point the code is the authority on detail and the
RFC records intent — do not update an RFC to match code that drifted. Write a new one.

## Frontmatter reference

Every RFC begins with YAML frontmatter. `scripts/check_rfcs.py` validates it.

| Field | Required | Notes |
|---|---|---|
| `rfc` | yes | Integer, matches the filename. |
| `title` | yes | Sentence case, no trailing period. |
| `status` | yes | One of the six statuses above. |
| `authors` | yes | List of GitHub handles. |
| `created` | yes | `YYYY-MM-DD`. |
| `updated` | yes | `YYYY-MM-DD`. |
| `supersedes` | no | RFC number, if this replaces one. |
| `superseded_by` | no | RFC number. Required when `status: superseded`. |
| `tracking_issue` | no | Issue number for implementation. |

## Index

| RFC | Title | Status |
|---|---|---|
| [0001](0001-architecture.md) | Architecture: detectors, AppSpec, plan and apply, renderers, gaps | draft |
