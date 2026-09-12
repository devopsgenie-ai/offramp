---
rfc: 0000
title: Template
status: draft
authors: [your-github-handle]
created: 2026-09-05
updated: 2026-09-05
---

> Delete this quote block. One or two sentences: what changes if this is accepted?

## Problem

What can a user not do today, or what goes wrong? Describe the situation, not the
solution. If you cannot state the problem without naming your fix, you do not yet
understand it well enough to write the RFC.

Include evidence — a real repository that fails, an error, a manual step someone performs
today.

## Proposal

What you intend to build. Be concrete: name the modules, the data shapes, the file paths,
the command-line surface. Show the generated output where it clarifies.

## Alternatives considered

At least one, with the reason it lost. "We could do nothing" is a legitimate alternative
and should be considered honestly.

## Impact on generated output

Does this change what the tool emits for an existing scenario? If yes, name the fixtures
whose golden files will change, and say whether the change is additive or breaking for
someone who already ran the tool.

If no, say "None."

## Testing

How will we know this works, and keep knowing? For anything touching generated output,
name the fixtures and what they assert. State how a reviewer can verify the claim
themselves.

## Declarative constraint

Confirm this proposal emits only configuration and performs no mutating operation, per
`AGENTS.md`. If it requires an exception, argue for it here explicitly — that is the
section reviewers will read hardest.

## Out of scope

What this deliberately does not do. Naming it prevents scope creep during review and
tells the next author where the seams are.

## Open questions

Anything you want reviewers to decide. Remove the section if empty.
