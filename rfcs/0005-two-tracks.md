---
rfc: 0005
title: "Two tracks: an auditor and a generator over one shared core"
status: draft
authors: [ishantdeep-hue]
created: 2026-10-10
updated: 2026-10-10
---

> Makes `offramp` two first-class products in one tool. The **auditor** (`audit`, `fix`)
> tells a user whether their app is safe to keep running and repairs what it can prove.
> The **generator** (`render`, `verify`, and later `show`, `apply` and the skill)
> writes the deployment definition RFC-0001 set out to produce. Both stand on one shared
> core. This RFC records the boundary between them, replaces the build-order paragraphs
> of RFC-0001, RFC-0002 and RFC-0003 with two orders that run side by side, and labels
> every gap with the track that consumes it.

## Problem

The repository has two products and describes one.

RFC-0001 proposed a generator: read an application repository, build an AppSpec, and
render Kubernetes configuration from it. RFC-0002 reordered that work so a read-only
audit shipped first, and said the renderer, `verify`, `show`, `apply` and the skill
*"remain as RFC-0001 specifies them. This RFC moves them later in the build order and
does not cancel them."* RFC-0003 then made `fix` the first renderer, ahead of Kustomize.
RFC-0004 added another audit check, and names an edge-function check as the RFC after
it. Each of those decisions was argued and accepted on its own. None of them says where
the generator now stands, and the repository's own documents disagree:

- `README.md` is an auditor's README. Its first line asks *"Is the app you built on
  Lovable or Emergent safe to keep running, and what would it take to own it?"*
- `ROADMAP.md` still lists Emergent and the Kustomize target under **Now**. It does not
  mention RFC-0003 or RFC-0004, and it lists Lovable under **Next** although `scan`
  already detects it.
- `AGENTS.md` §8 lists only `rfcs/` and `scripts/` as existing and says *"Do not create
  `src/`, `schemas/`, `templates/` or `skills/`"*. All the code lives under `skills/`.

The cost of leaving this unstated is concrete:

- **Nobody can say whether the generator is deferred or abandoned.** Its build order was
  pushed back twice by documents about something else. A contributor reading the
  roadmap would start on Kustomize; one reading the RFCs would start on edge functions.
- **The two tracks share code without a rule for it.** The AppSpec, the detectors, the
  migration replay, the gap model and `verify` serve both. A change made for one track
  moves the other's golden files, and nothing says who has to agree to that.
- **One quality metric measures two things.** RFC-0001 makes the bare gap count the
  project's headline number. On `fixtures/lovable-vite-supabase`, four of the six
  blocking gaps are Kubernetes delivery questions (`delivery.gitops_repo_url`,
  `delivery.image_registry`, `delivery.ingress_class`,
  `environment.production.namespace`), which the audit report correctly hides. The
  other two are a datastore mode and a table owner. A change on either track moves the
  same number, so neither track can read its own progress from it.
- **The shared piece both tracks are waiting for has no slot.** `docs/research/
  fix-precision.md` found that of 478 RLS findings in the corpus, `fix` writes an
  executable fix for 2 and turns 253 into owner gaps. *"The yield rises only when a
  human answers the gaps, which needs RFC-0001's `apply`."* RFC-0001 schedules `apply`
  after the first deployment renderer, which has not started.

## Proposal

### Two tracks, one tool

`offramp` stays one repository, one set of scripts and one AppSpec. It has two
products, each with its own commands and roadmap:

| Track | Question it answers | Commands |
|---|---|---|
| **Auditor** | Is this app safe to keep running, and what can be fixed from the repository alone? | `audit`, `fix` |
| **Generator** | What would it take to run this app somewhere I own? | `render`, `verify`, and from the core `show` and `apply` |

Neither track is a step of the other. A user can audit an app they never move, and
generate config for an app they never audit. Both obey every rule in `AGENTS.md`
unchanged: declarative output, no network, no model call, determinism, no secret values,
gaps over guesses, and the RFC gate.

The public surface leads with the auditor, because it is what a user can run today.
`README.md` keeps its first line. The generator gets a section of its own once `render`
produces a tree a user can run, and not before. That is the same rule RFC-0003 applied to
`fix`.

### The boundary

Every path belongs to exactly one of three areas.

| Area | Paths |
|---|---|
| **Core** | `skills/offramp/scripts/spec.py`, `gaps.py`, `walk.py`, `scan.py`, `report.py`, `rls.py`, `verify.py`, `detect/`; `fixtures/*/expected_bare/`, `fixtures/*/truth.yaml`, `fixtures/*/gap_count.json`; `scripts/check_fixtures.py`, `scripts/check_rfcs.py` |
| **Auditor** | `audit.py`, `audit_report.py`, `findings.py`, `fix.py`, `fix_plan.py`, `fix_render.py`, `fix_sql.py`, `checks/`; `fixtures/*/expected_audit/`, `fixtures/*/expected_fix/`, `fixtures/*/behaviour.yaml`; `scripts/check_fix_behaviour.py`; `docs/research/` |
| **Generator** | New: `render.py`, `render/`, `fixtures/*/expected_render/`. Later: `show.py`, `apply.py`, `SKILL.md` |

Paths under `skills/offramp/scripts/` are written above without that prefix. Fixture
*inputs* (everything in a fixture directory that is not listed above) are core: both
tracks read them. `rls.py` is core although it is about row-level security, because
`detect/migrations.py` and `detect/owners.py` import it: the policy-expression
predicates are part of the replay.

One core module is not yet what its area says. `verify.py` imports `build` and
`MANIFEST` from `fix.py`, because `fix` is the only renderer it has had to check. It is
core by intent and auditor-bound in fact. The generator's first renderer is what
generalises it (see *Two build orders*).

Three rules govern the boundary:

1. **A change to a core path needs the approval of a reviewer for each track.** This is
   enforced by `.github/CODEOWNERS`, not by convention. Track paths keep today's rule of
   one maintainer's approval.
2. **The AppSpec is core.** A field one track needs is added under that track's RFC and
   reviewed as a core change. The AppSpec is where the two tracks can damage each other,
   and the rule puts the second track's reviewer in the room when it changes.
3. **Every PR keeps both tracks' golden files green.** No change to one track may leave
   the other's `expected_*` tree for someone else to repair.

`verify` is core because both tracks' renderers write a manifest and are held to it.
`show`, `apply` and the answers file are core because both tracks need answers. The
auditor needs them so a human can supply a table's owner (`write_scope`). The generator
needs them so a human can supply delivery settings and CI can supply `image_tag`.

### Two build orders, side by side

This replaces the build-order text of three earlier RFCs (see *What this replaces*).

**Generator.** RFC-0002 replaced RFC-0001's *Scope of the first implementation* for the
project as a whole. For the generator track, RFC-0001's scope bullets apply again as
written, except the build-order bullet, which this section replaces: Emergent (FastAPI,
Create React App, MongoDB) to Kubernetes through Kustomize and GitOps, one `production`
environment, greenfield deployment repository, `kind: resolve` only, clone-and-point,
Python.

1. The Kustomize renderer, as RFC-0001's *Renderers* section specifies, with
   `expected_render/` goldens for the Emergent fixtures. It writes the same
   `.offramp/manifest.json` that `fix` does. In the same change, `verify` stops
   importing `fix` and instead re-renders with the renderer the manifest names. The
   manifest gains a `renderer` field for that: a core change, reviewed as one, which
   moves `fix`'s golden manifests by one field. RFC-0003 expected the Kustomize renderer
   to reuse `verify` *"unchanged"*. This is the smallest change that makes that true
   for a second renderer. The renderer emits with unresolved gaps marked in the tree,
   which RFC-0001 already allows (*"`render` runs before any model does"*), so it does
   not wait for `apply`.
2. Consumption of answers from the core `apply`, once it exists: delivery answers from a
   human, `image_tag` from CI.
3. `SKILL.md`.

**Auditor.**

1. RFC-0003 completed: its Open question 6 decided, the defects filed against `fix`
   in review fixed, and the RFC moved to `implemented`. Then the README documents `fix`.
2. RFC-0004's remaining build steps: the check, its fixture and the corpus precision
   run.
3. Consumption of `write_scope` answers from the core `apply`, once it exists, so an
   answered owner becomes an executable fix.

**Core.**

1. `show`, `apply` and the answers-file merge in `scan`, as RFC-0001 specifies them in
   full. The first answerable field they carry is `write_scope`, because it is the only
   answerable field with a working consumer today. Delivery fields follow with no
   change to the mechanism, since RFC-0001's address space already declares them.
2. Per-track gap labels (below).

The three orders run in parallel. The only dependency between them is that each
track's step that consumes answers waits for core step 1. Nothing else on either track
waits for the other.

### Gaps carry a track

Every gap is labelled with the track that consumes it: `auditor`, `generator` or
`both`. The label is **derived from the gap's id**, not stored on it, so the Gap record,
`gaps.json` and every golden file are unchanged.

```
gaps.track(gap_id) -> "auditor" | "generator" | "both"
```

It is one ordered table in `gaps.py`. The first matching pattern wins:

| Pattern | Track | Why |
|---|---|---|
| `datastore.*.table.*.write_scope` | auditor | Only `fix` renders from it. |
| `credential.*` | both | `credential.committed` links it as a related gap. RFC-0001 requires it for cutover. |
| `service.*.probes`, `service.*.probes.*` | both | `service.no_health_endpoint` reads it. The generator renders probes from it. |
| `service.*.routes.*` | both | `service.no_health_endpoint` reads it to tell "no route" from "prefix unknown". |
| `*` | generator | Everything else describes what a deployment needs. |

The rule behind the table is consumption. A gap is `auditor` or `both` exactly when an
audit check reads it or links it in `related_gaps`. `datastore.*.mode` is therefore
`generator`: no check reads it, however much it matters to a user who is leaving.

What the label changes:

- **`scripts/check_fixtures.py` prints the bare gap count per track** next to the total
  it prints today. A `both` gap counts once toward each track's number. The total stays
  the only gated series, with the rule RFC-0001 set for it. Each track can now read its
  own trend.
- **`audit` and `render` filter by it.** The audit report already shows only the gaps
  its checks link. `render` will show `generator` and `both` gaps. `scan` keeps
  emitting every gap, so there is still one AppSpec and one honest list.
- **A gap whose label is wrong fails a test.** See *Testing*.

### What this replaces

`rfcs/README.md` provides for superseding a whole RFC and no smaller unit. RFC-0002's
Open question 1 asked for a way to amend part of one, and it is still unanswered. This
RFC does not answer it. It names exactly the paragraphs it replaces, leaves every
earlier RFC's status unchanged, and leaves the process question to its own RFC.

| RFC | Section replaced | Replaced by |
|---|---|---|
| RFC-0001 | The **Build order** bullet of *Scope of the first implementation*, and the closing paragraph of *Revisions from the spike* that repeats it | *Two build orders, side by side*, Generator and Core. The order of `show`/`apply` relative to the first deployment renderer is the change: they now run in parallel. The rest of that section applies again, to the generator track only (see *Generator* above). |
| RFC-0002 | *Build order*, step 6 (*"After that, RFC-0001's order resumes"*) | *Two build orders, side by side* |
| RFC-0003 | *Build order*, the sentence *"`fix` becomes the first renderer, ahead of Kustomize"* | `fix` is the auditor's renderer and Kustomize is the generator's. Neither is ahead of the other. |

Everything else in those RFCs stands: RFC-0001's AppSpec, gap model, plan/apply design,
`verify` invariant and scope, RFC-0002's finding model, and RFC-0003's evidence rule and
round trip.

### Documents that follow acceptance

These are documentation and CI changes that put this RFC into effect. They do not need
an RFC of their own, and they land after this one is accepted:

- `ROADMAP.md` becomes three tables, one for the auditor, one for the generator and one
  for the core, each with Now, Next and Later. The **Explicitly not planned** section
  stays as it is and applies to both.
- `AGENTS.md` §8 describes the layout that exists, and the core/auditor/generator
  boundary. The sentence forbidding `skills/` goes.
- `.github/CODEOWNERS` routes the core paths to both tracks' reviewers.
- `README.md` keeps leading with the auditor. It gains the generator section only when
  `render` ships, as above.

## Alternatives considered

**Keep one product, and let the generator wait.** Finish the auditor, then resume
RFC-0001. This costs nothing in process. It lost because the generator has already
waited through three RFCs that were not about it, and the order is not neutral: the
shared core gets shaped by one track's needs alone. The AppSpec gains fields the
auditor needs and none the generator needs, until the generator restarts against a core
that was never reviewed with it in mind.

**Two packages in one repository** (a shared core and two installable products). Each
gets its own docs, release and version. It lost on timing. There is no package today,
because distribution is clone-and-point, so the split would create release machinery
for releases that do not happen. It remains open once a distribution RFC exists.

**Two repositories, two names.** This positions the auditor most clearly to users who
never plan to leave a platform. It lost on the shared core. Both products would depend
on a versioned core whose AppSpec changes on almost every RFC, and every such change
would become a cross-repository release. RFC-0001 deliberately leaves the AppSpec
unversioned while the project is pre-release, and two repositories would force a
version on it.

**Reorganise the code into `core/`, `audit/` and `deploy/` directories now.** The
boundary would be visible in the tree. It lost on cost against benefit: about forty
file moves, a `sys.path` change in every entrypoint, and a diff that collides with any
work in flight, to express what `CODEOWNERS` expresses with no move at all. The
existing layout already separates by directory except for a handful of top-level
modules. It can be done later without changing anything this RFC decides.

**Store the track on the Gap record.** A `track` field would be explicit. It lost
because it changes `gaps.json` in every fixture for information that is a pure function
of the id, and a stored field can disagree with the id while a derived one cannot.

**Gate CI on each track's gap count.** Each track would then be held to its own
number. It is deferred, not rejected (see *Open questions*). Today one track's number
is a handful of gaps per fixture, and a per-track gate would mostly add noise to
`gap_count.json`.

## Impact on generated output

None from what this RFC itself changes. With the gap labels in place, `scan`, `audit`,
`fix` and `verify` write the same bytes for every fixture. `gaps.json`,
`gap_count.json`, `expected_bare/`, `expected_audit/` and `expected_fix/` do not move.
The only visible change is the console summary of `scripts/check_fixtures.py`, which
gains a per-track count. That summary is not generated output.

One change this RFC schedules does move goldens when it lands: adding `renderer` to
`.offramp/manifest.json` (generator step 1) changes every `expected_fix/.offramp/
manifest.json` by that one field. It is additive. A manifest written before it has no
`renderer`, and `verify` reads its absence as `fix`, the only renderer that existed.

## Testing

- **Every gap id has a deliberate label.** A test runs `scan` on every fixture and
  asserts that `gaps.track` returns a label for every gap id. It also asserts that every
  pattern in the table except the final `*` matches at least one gap in some fixture,
  so a stale pattern fails.
- **The label agrees with consumption.** A test runs `audit` on every fixture and
  asserts that every id in any finding's `related_gaps` is labelled `auditor` or `both`.
  A second test runs each check against a scan result whose `generator` gaps have been
  removed, and asserts its findings and assessment are unchanged, so a check that starts
  reading a `generator` gap fails here. Together they enforce the rule the table is
  built on, rather than the table's current contents.
- **Golden files do not move.** `make check` passes with every `expected_*` tree
  byte-identical, which is how this RFC's *Impact on generated output* claim is
  verified.
- **Boundary coverage.** A test asserts that every file under `skills/offramp/scripts/`
  and `scripts/` falls in exactly one area of the boundary table, by a pattern list in
  the test that `CODEOWNERS` mirrors, so a new module cannot land unassigned.

## Declarative constraint

This proposal complies. It changes ownership, ordering and a derived label. It adds no
operation of any kind, and both tracks remain bound by `AGENTS.md` §2 exactly as
before. The generator in particular stays a code author, not a deployer: nothing in
this RFC moves it closer to applying what it writes.

## Out of scope

- **The process for amending part of an RFC.** RFC-0002 Open question 1. It needs its
  own RFC, and this one works around it by naming what it replaces.
- **Any change to either track's scope.** The generator restarts on RFC-0001's scope as
  written. The auditor's next checks need their own RFCs.
- **The design of `show` and `apply`.** RFC-0001 specifies them. This RFC only moves
  them into the core and schedules them. Narrowing their first release to `write_scope`
  is a choice of which answerable field to exercise first, not a change to the design.
- **Distribution:** packaging, a GitHub Action, or a separate name for either track.
- **Who builds what.** This RFC fixes what is built and in what order, and who must
  review core changes. Assigning work is not a design decision.

## Open questions

1. **Per-track gating.** Should CI gate each track's bare gap count separately, once
   the generator's renderer exists and its count means something? The proposal records
   them and gates only the total.
2. **The README's generator section.** Is "once `render` produces a tree a user can
   run" the right trigger? The alternative is to name the generator in the README now,
   as planned work, so the auditor's readers know where the project is going.
