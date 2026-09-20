## What this changes

<!-- One or two sentences. What is different after this merges? -->

## Authorising RFC

<!--
Changes to src/, schemas/, templates/ or skills/ require an accepted RFC. Cite it as
RFC-NNNN (four digits) — CI checks that it exists and is accepted. Everything under
skills/ is gated, including SKILL.md.

If this change is exempt (docs, tests for existing behaviour, CI, typos, dependency
bumps), delete the line below and say which exemption applies.
-->

RFC-

## Checklist

- [ ] `make check` passes locally
- [ ] Generated output is unchanged, or the changed golden fixtures are included and the
      diff is explained above
- [ ] No secret value is written into output, fixtures, tests or logs
- [ ] Nothing added here mutates infrastructure or requires cloud credentials
      (see [AGENTS.md](../AGENTS.md#the-declarative-constraint))
