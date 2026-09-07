# SPIKE — a proof of concept for RFC-0001

**This branch is a throwaway. It is not the implementation, and it must not be merged.**

RFC-0001 is `status: review`. `AGENTS.md` §1 says no implementation lands without an
*accepted* RFC, and `scripts/check_rfcs.py gate` enforces it: `GATED_ALWAYS = ("skills/",)`
carries no suffix exemption, so a pull request touching `skills/` while RFC-0001 is in
review fails with *"Implementation may not begin until the RFC is accepted."* That is
correct and this branch does not try to get around it. **No pull request has been opened
against `main`, and `rfcs/0001-architecture.md` is untouched.**

The branch exists for one reason: the architecture had never been run, and it should not be
accepted on paper. Everything below is evidence for or against it.

## Two revisions

**Revision 1** implemented RFC-0001's AppSpec as literally as it could and recorded where it
broke: 25 findings, of which three were structural.

**Revision 2** fixed them, and then answered the gap list one gap at a time. Doing that
turned up eight more findings that only appear once the plan/apply cycle actually runs —
#26 to #33 below, several of them sharper than anything in the first pass. The shape in
`spec.py` is now a **counter-proposal** to the RFC rather than a transcription of it.

Findings are numbered once and keep their numbers. Every one is cross-referenced from the
code at the point where it bit.

## What was built

```
fixtures/emergent-fastapi-mongo/    a synthetic Emergent app (invented; no real
                                    credential, hostname or account identifier)
fixtures/plans/                     a checked-in plan fixture answering every gap
skills/offramp/scripts/
  spec.py         AppSpec, Gap, Evidence, enums, canonical JSON
  detect.py       detectors — repository in, facts and gaps out
  answers.py      the answers file and the merge rule
  scan.py         entrypoint: appspec, gaps, evidence, answerable set, detected
  renderers.py    pure AppSpec -> {path: content}
  render.py       entrypoint: writes the tree and .offramp/manifest.json
  apply.py        entrypoint: accepted plan entries -> answers.json
  verify.py       entrypoint: re-derives the tree *and* the scan output, and diffs
skills/offramp/schemas/             an ApplicationSet CRD schema, so #18 can be checked
scripts/check_fixtures.py           truth.yaml assertions and the assertion guard (#23)
scripts/stamp_plan.py               stands in for `show` until it exists (#34)
scripts/spike_check.sh              reproduces every claim below
```

`show` and the skill are still not built. Everything else in RFC-0001's pipeline is.

## How to run it

```bash
make spike-check
```

Read-only throughout: it renders into a temp directory, runs `kustomize build`,
`kubeconform` and `docker build`, and touches no real infrastructure. Docker steps are
skipped if no daemon is running.

---

## What works

Each of these is asserted by `scripts/spike_check.sh`.

**The route-prefix case resolves correctly — the one the RFC's Problem section is about.**
`server.py` decorates a handler `@api_router.get("/health")` and mounts the router with
`APIRouter(prefix="/api")`. The detector parses the module with `ast`, tracks router
variables, their prefixes and their `include_router` mount prefixes, and composes them, so
the AppSpec records `/api/health`. Confirmed against a running container:

```
/api/health (what the detector composed) -> HTTP 200
/health     (the decorator literal)      -> HTTP 404
```

When the prefix is *not* a literal — `app.include_router(router, prefix=settings.api_prefix)`
— the detector refuses to compose and records why, instead of reporting `/health`
confidently.

**The whole loop runs, and the gap list goes to zero.** `scan → plan → apply → scan →
render → verify`, with 22 bare gaps answered one at a time by a checked-in plan fixture:

```
bare gap count 22 -> answered gap count 0
no placeholders and no unresolved-gap markers remain
```

One of the 22 was never answered: `datastore.mongodb.version` disappeared on its own when
`mode` was answered `managed`, which is #21 working.

**Rendering is byte-stable, and no longer depends on the checkout.** Two renders of the same
AppSpec produce identical trees. The same repository in a differently-named directory now
produces `0 of 16 files differ`, against `10 of 15` in revision 1.

**`verify` covers the scan output as well as the tree.** Green on a clean render; exits 1
with a diff when the generated liveness probe is edited from `/api/health` to `/health`.
`render` separately refuses to overwrite that file, *and* refuses to overwrite a
pre-existing file it never wrote.

**Every rejection path in `apply` fires**, including the one that cannot be reached through
a normal plan:

```
rejected: basis does not match
rejected: plan made against other detectors
rejected: compare-and-swap failed
rejected: target outside the answerable set
rejected: two entries target the same id
rejected: evidence does not resolve
rejected: kind override, in a v1 that accepts none
rejected: sensitive value refused (source='datastore', sensitive=True)
```

That last line is finding #4 demonstrated: under RFC-0001's single enum `MONGO_URL` is
`source: datastore`, so a rule keyed on `source == "secret"` would not have fired for the
one value it most needs to protect.

**Staleness, orphaning and graduation all behave.** Moving a cited line invalidates the
answers citing it. Renaming a service produces six `orphaned_answer` gaps rather than
silently dropping them. And when a detector graduates — the repository gains a git remote,
so `app.name` becomes detectable — the stored answer reads as `stale_answer`, not as an
orphan.

**The generated config validates, including the ApplicationSet.** `kubeconform -strict`
reports 7 valid / 0 invalid for the overlay, and the ApplicationSet is now `Valid: 1,
Skipped: 0` against a checked-in CRD schema, where revision 1 could only get `Skipped: 1`.

**`binding: build_arg` works.** The frontend Deployment has no `env:` block. The answered
value reaches the image as `ARG REACT_APP_BACKEND_URL=""`, the compiled bundle contains
`"/api"` and no Emergent host, and the image serves HTTP 200 on its own probe path.

**No secret value reaches the output — including the answers file.** Grepping the tree, the
manifest, the AppSpec, the gap report, the evidence sidecar and the committed `answers.json`
for the password, the username, the `mongodb://` scheme and the Emergent hostname returns
nothing.

---

## Findings

| # | Finding | Status |
|---|---|---|
| 1 | `EnvVar` has no field for a value | fixed: `EnvVar.value` |
| 2 | No field for the static build output directory | fixed: `Build.output_dir` |
| 3 | Ports and probes of a built-then-served service belong to neither layer | fixed: left empty, renderer fills under #22 |
| 4 | `EnvVar.source` conflates provenance with sensitivity | fixed: `EnvVar.sensitive`; guard re-keyed and proven |
| 5 | Deployment inputs have no home, so cannot be gapped | fixed: `Delivery` block |
| 6 | `Build.args` typed `[str]` | superseded by #29 — the field is gone |
| 7 | Renderers must name gaps but are given only the AppSpec | fixed: structural ids, asserted in CI |
| 8 | `Gap.pointer` is one-to-one; real gaps are zero-to-many | fixed: `Gap.pointers` |
| 9 | The human-readable gap report has no defined producer | fixed: `scan.json`, `verify --repo` |
| 10 | `AppSpec.source.commit` is not determinism-safe | fixed: content hash excludes `source` |
| 11 | Detected facts carry no evidence, and staleness needs it | fixed: `evidence.json`, hashed by line |
| 12 | `Gap.origin` omits `orphaned_answer` | fixed: `GAP_ORIGINS` + `check_enums` |
| 13 | Renderer defaults are invisible to the gap report | fixed: `Default` table + marker assertion |
| 14 | The RFC never says where the output tree sits | fixed: decided, documented, collision-checked |
| 15 | `.offramp/manifest.json` underspecified twice | fixed: excludes itself, records a content hash |
| 16 | `docker build` proves little; nothing detects pinning | fixed: detector + a smoke check that starts the image |
| 17 | `AppSpec.name` has no specified derivation | fixed: git remote or a blocking gap; 10/15 → 0/16 |
| 18 | The `ApplicationSet` is validated by nothing | fixed: checked-in CRD schema |
| 19 | The repo's `.gitignore` blocks the fixture the RFC asks for | fixed: `!fixtures/**/.env` |
| 20 | Several gaps are instructions, not values | fixed: `Gap.kind`, `Answer.kind` |
| 21 | Gaps have dependencies; the model is flat | fixed: `depends_on` + mootness |
| 22 | No rule for when a default stops being a guess | fixed: rule stated; count rose 18 → 22 |
| 23 | The artifacts that define "correct" are ungated | fixed: `truth.yaml` asserted; weakening one costs a sentence |
| 24 | `Service.health` is a single probe | fixed: `Service.probes` |
| 25 | `Datastore` has no identity | fixed: `Datastore.name` |
| 26 | `Delivery.image_tag` is a per-build fact in a per-scan field | fixed: CI answers it and re-renders; `Answer.accepted_by` |
| 27 | A composite answer cannot be applied | fixed by decomposing into one gap per field |
| 28 | The AppSpec is a typed tree and answers are JSON | fixed: one deserialiser, round-trip after merge |
| 29 | `Build.args` duplicates `EnvVar` — two sources for one fact | fixed: field deleted |
| 30 | Detector graduation is indistinguishable from an orphan | fixed: resolve targets against the AppSpec |
| 31 | Staleness re-asks even when the detector agrees | fixed: agreement merges and is reported, not re-asked |
| 32 | An answered id leaves the address space, so nothing can be revised | fixed: answers stay declared |
| 33 | `Answer.detected` and a plan's `current` are not the same value | fixed: `detected.json` |
| 34 | `apply` checks that cited evidence *resolves*, never that it *supports the claim* | fixed: drift refused; citations split into hashed and shown |
| 35 | The fixture is single-file, so every module-scoped detector was validated against a shape real repositories do not have | **open** — the most serious finding here |
| 36 | The datastore detector keys off dependencies, which lag reality | **open** |
| 37 | A `build_arg` is public by construction; the credential detector does not know it | **open** |
| 38 | v1 is scoped greenfield, and a real Emergent repository is not greenfield | **open** |
| 39 | The tree walk excludes `node_modules` but not `.venv`, `build/`, `dist/` or `.git` | **open** |

### The three that mattered most in revision 1

**#17 — `AppSpec.name` has no specified derivation.** The RFC fixes `Service.name`'s
derivation in the document and calls changing it breaking, because it orphans answers.
`AppSpec.name` gets none of that care — and it is baked into every label, the namespace, the
Ingress, the `ApplicationSet` and the Secret the backend reads its connection string from.
Nothing in an Emergent repository states a name, so revision 1 used the directory, and
copying the identical fixture into two differently-named directories changed **10 of 15
files** — a straight violation of `AGENTS.md` §3 reachable on the first run, with no answers
file and no model involved.

Fixed by taking the name from the git remote, which is a property of the repository rather
than of the checkout, and emitting a blocking gap when there is no remote rather than
guessing. Now `0 of 16 files differ`.

**#5 — Deployment inputs have no home in the AppSpec, so they cannot be gapped either.** A
Kustomize overlay needs a namespace, a registry and a tag; an `ApplicationSet` needs the URL
of the *deployment* repository and a destination cluster; the Ingress needs a class. None is
a property of the application, none can be detected, and `AppSpec.source.repo_url` is
provenance for the *source* repository and is declared never answerable. Since `Gap.pointer`
addresses the AppSpec, a thing with no field cannot be pointed at, cannot be a well-formed
gap, and cannot be answered. Four of revision 1's six blocking gaps were in that position.
Fixed with a `Delivery` block. **The rule the RFC is missing: the AppSpec must be able to
hold every input the renderers consume, including the ones that describe the destination
rather than the application.**

**#1 — `EnvVar` has no field for a value.** `EnvVar {name, source, binding}` cannot hold
`DB_NAME=emergent_status_board`, so nothing can be rendered into a ConfigMap — and
`binding: build_arg`, the field the RFC introduces specifically to carry a value into the
Dockerfile, has no value to carry. A one-word fix, but the AppSpec as printed in the RFC
cannot render a working application.

### What only appeared once the loop ran

These eight are revision 2's return on actually building `apply`. Every one was found by a
check failing, not by reading.

**#28 — the AppSpec is a typed tree and the answers file is JSON, and the RFC never says
which representation comparisons happen in.** This bit three times in a row, each time
looking like a different bug: compare-and-swap rejected a structured answer that matched;
the staleness rule reported a detector disagreeing when it had not; and an attribute access
raised, because merging had written a plain dict into the middle of a dataclass tree. The
fix is one deserialiser (`spec.from_jsonable`) that a merge round-trips through. RFC-0001
calls the AppSpec "a plain data structure, serialisable to JSON" and then specifies
compare-and-swap and a hash-based staleness rule over it without saying what either compares.

**#33 — `Answer.detected` and a plan entry's `current` look like the same value and are
not.** `current` is the compare-and-swap precondition: what the human reviewed, which after
a merge is a previously-answered value. `detected` is "what the detector produced when this
was answered". Filling `detected` from the live AppSpec — the obvious implementation, since
that is what `apply` is handed — records an answer as though a detector had produced it, and
every later scan then reports the real detector as disagreeing. **Revising a single answer
was enough to trigger it**, and it broke exactly the machinery RFC-0001 calls "the
load-bearing part of this design". Fixed by having `scan` write `detected.json` from the
bare, pre-merge spec.

**#30 — detector graduation is indistinguishable from an orphan, if you resolve answers
against the gap list.** RFC-0001 separates a *stale* answer from an *orphaned* one, and the
natural way to tell them apart is "is there still a gap for this?". That is wrong, because a
gap disappearing is also what success looks like: the RFC itself calls out detector
graduation as something the design makes work. Resolving against the gap list turns every
graduation into an orphan report. The target has to be resolved against the **AppSpec**,
which means the id → pointer function is load-bearing and must exist independently of gaps.

**#32 — an answered id leaves the address space, so an answer can never be revised.** The
answerable set is derived from open gaps; answering a gap retires it; a later plan targeting
it is rejected as out-of-address-space. The only way to change your mind is to hand-edit the
file the whole mechanism exists to keep hand-editing out of. Fixed by keeping stored answers
in the declared set.

**#29 — `Build.args` duplicates `EnvVar`, and the renderer read the stale copy.** RFC-0001
says `binding: build_arg` "routes the value into `build.args` and the Dockerfile". That
first clause creates a second home for a fact `EnvVar` already holds: answering the build
arg set `EnvVar.value` and left `Build.args` holding a null, so the Dockerfile still carried
an unresolved-gap marker for a question that had been answered. Revision 1's #6 fixed the
*type* of `Build.args`; the right fix was to delete it. A build argument is exactly "an
`EnvVar` with `binding: build_arg`".

**#27 — a composite answer cannot be applied.** Revision 1 asked one question about how the
compiled frontend is served, whose answer was a server, a port and a probe path — three
fields of different shapes. The answers model is one value at one pointer, so it could not
be applied at all; and because acknowledging it left no trace in the AppSpec, the renderer
went on marking it unresolved. One human decision therefore has to become one gap per field
it sets. That is a real cost: it inflates the question count for decisions a person
experiences as single.

**#26 — `Delivery.image_tag` is a per-build fact living in a per-scan field.** The honest
answer to "what should the image tag be" is "whatever CI just built" — a commit SHA — and
the AppSpec holds a literal. The constant revision 2 first answered with (`production`)
meant every build overwrote the tag, so a rollback had nothing to roll back to; worse, the
generated Deployment sets `imagePullPolicy: IfNotPresent`, so against a moving tag a node
that already cached it goes on running the old image with no error anywhere.

The decision it forced is not about tags. Standard GitOps has CI write the new tag into the
manifests and commit, and that collides head-on with `verify`'s invariant — *every byte
under the output tree is a pure function of (AppSpec, answers, renderer version)*. Patching
`newTag` the way `kustomize edit set image` would makes `verify` report a renderer bug, and
it is right to: it cannot tell a build pipeline apart from a model editing output it should
not have touched. RFC-0001 addresses this nowhere, and v1 emits no CI workflow, so the user
meets it on their first deploy.

**Resolved by keeping the field and having CI answer it.** A build runs `scan → one-entry
plan → apply --accepted-by ci → scan → render → commit`. The tag is then a genuine AppSpec
change, `verify` stays green with no carve-out, and no model or key is involved. Two
consequences worth carrying into the RFC:

- The answers file is described as *"the accumulated, **human-accepted** result of applied
  plans"*, and that has to widen to "accepted". `Answer.accepted_by: human | ci` keeps the
  distinction visible, so a reviewer can still see which answers a person decided. Adding it
  with a default of `human` is not a breaking change, so the format version does not move.
- An immutable tag is what makes `imagePullPolicy: IfNotPresent` correct rather than
  dangerous. The two are coupled, and the generated Deployment now says so at the line.

The alternatives, for the record: removing the field and leaving the tag to an external
image updater keeps `verify` absolute by construction but hands the user a required step
with no runbook to carry it; generalising `binding` to build-time fields names the whole
category (digests, build numbers, config checksums) but puts a deliberate hole in `verify`,
which is the erosion `verify` exists to prevent.

**#31 — the merge rule re-asked even when the detector agreed with the stored answer.**
RFC-0001: an answer merges only when the detector still produces the value recorded in
`detected`. When a detector graduates and produces *exactly what the human already
answered*, `detected` has moved from null to a value, so the answer was refused and the
question asked again — with the same answer on both sides.

That is correct by the letter of the rule and wrong in effect, for a reason the RFC
already articulates about a different rule. It rejects "answers win over detection" partly
because *"gap count falls when it happens"* — the metric moves the wrong way when something
bad occurs. This is the same inversion with the sign flipped: **a detector getting better
raised the answered gap count.** Measured on the fixture, giving the repository a git remote
so the `app.name` detector graduates:

```
bare, before the detector improved:      22
answered, before the detector improved:   0
answered, AFTER the detector improved:    3   <- two of them with identical values on both sides
```

So the rule taxed exactly the improvement the whole gap-tracking exercise exists to produce:
shipping a detector that retires a recurring plan entry sent every user who had answered
that question a confirm-what-you-already-said gap. And a stream of questions where both
sides read identically is how you teach a reviewer to click through — on the same surface
that has to catch a real disagreement.

**Resolved by letting agreement win.** When the detector produces exactly the answered
value, the answer merges and is reported under *"answers a detector has caught up with"* —
a note, not a gap. The answer stays in the file as a guard: if the detector later moves to a
*different* value, that disagreement still becomes a `stale_answer` gap, which
`spike_check.sh` asserts separately. Agreement overrides the evidence test too, on the
grounds that a detector producing the value *is* the evidence, and the human's citation no
longer has to hold. Action answers are excluded, since they carry no value and no pointer.
The answered gap count now stays at 0 when a detector improves.

**A duplicate found while measuring, fixed either way.** A stale answer was re-emitted as a
gap carrying the original question *and* the original gap was still emitted, so the same
question appeared twice in one report. The second-pass filter now suppresses any gap a
stale answer supersedes.

**#34 — `apply` checked that evidence resolved, never that it matched or that it was
relevant.** Found while measuring #31, and it turned out to be two problems.

*The RFC's "and to match" had no meaning.* `PlanEntry.evidence` is specified as
*"file:line, checked by apply to exist and to match"* — match what is never said, so it
matched nothing: `apply` re-hashed whatever was on disk at apply time and stored that. A
cited line could change between the plan being reviewed and the plan being applied, and the
answer would record evidence no human had seen:

```
the line the plan cited, at plan time:  fastapi==0.110.1
the same line, at apply time:           fastapi==0.999.0  # changed after the plan was written
apply exit: 0
```

The basis check does not cover this, and correctly so — a dependency bump moves no AppSpec
field, so `content_sha256` is unchanged. **The basis guards the AppSpec; nothing guarded the
files.** Those are two different surfaces and the RFC has machinery for one.

*And nothing constrained relevance.* A plan answering "which Python version does the backend
need" could cite `frontend/src/App.css:1`, and it was accepted and hashed — making a CSS
line a permanent re-ask trigger for a question about Python. Padding a citation list was not
cosmetic; it was a cost imposed on the user forever, invisibly.

**Resolved by matching on drift, and by making padding harmless rather than forbidden.** A
citation now carries the hash the plan saw and a mismatch is a rejection. For relevance,
each gap declares an `evidence_scope` — path prefixes within which a citation bears on the
question — and citations outside it are kept as `Answer.context`: shown to the reviewer,
never hashed, never a reason to re-ask.

The alternative, rejecting out-of-scope citations outright, is what I first reached for and
it is wrong. The plan that answers `app.name` cites `backend/server.py` and
`frontend/src/App.js` to argue this application is a status board. That is good work and
exactly what a model is for — reasoning across a repository the detectors read narrowly —
and any scope rule tight enough to catch real padding rejects it too. Those two citations
are now carried as context: the reviewer sees the argument, and a CSS refactor never re-asks
what the application is called.

No mechanism can check that a citation *supports* a claim; that is a semantic judgement, and
it is worth saying so rather than implying the scope rule does more than it does. What the
checks give is narrower and still worth having: a citation cannot drift between review and
apply, and a bad citation cannot cost anything.

A consequence to note: a model cannot hash a line by hand, and should not be asked to. In
the finished design this stamping belongs in `show`, which is the script that renders a plan
for a human and therefore the one thing that knows what they actually read. `show` is not
built here, so `scripts/stamp_plan.py` stands in for it.

### Everything else, briefly

**#3** — a service compiled to static assets has no port and no probes in its source: the
only port there is the CRA dev server's 3000, which is not what ships. Both are now left
empty by the detector and supplied by the renderer under #22, and `Route.port` is nullable
so the Ingress wires to the Service's *named* port. The RFC's remedy for a boundary
problem — "the AppSpec is missing a field" — does not apply when the missing value is not in
the repository to be detected.

**#7 / #13 / #22** — renderers are pure functions of the AppSpec and are also asked to mark
unresolved gaps in the output, but gaps are not in the AppSpec. Gap ids are therefore
structural, built by one shared `gap_id()`, and `spike_check.sh` asserts every id named in
the tree exists in `gaps.json`. That one assertion also enforces #13 (no renderer default
without a gap behind it) and #22 (the rule: *if the tool puts a value in the output that it
did not detect, it emits a gap*). Applying that rule honestly raised the bare count from 18
to 22, which is the metric working — it had been under-counting.

**#9** — the gap report is, in v1, the entire description of the non-declarative work, and
it sits outside the output tree because `render` cannot see gaps. `scan` now writes
`scan.json` and `verify --repo` re-derives all six scan artifacts.

**#16** — the generated backend image builds cleanly and dies on `import server`, because
`requirements.txt` pins `motor` and not `pymongo`. That is the normal case for these
repositories: the platform froze the transitive set inside a prebuilt image. Two
consequences. The RFC's validation set (`kustomize build`, `kubeconform`, `docker build`)
has a hole one layer deep — **the check that catches it is "the image starts and answers its
own probe path"**, which is also the check that catches a confidently wrong probe, and it
touches nothing real. And the detector list needs dependency *pinning* alongside "dependency
manifest and install command". The fixture is deliberately left under-pinned so the check
stays honest; `spike_check.sh` asserts both that it still fails and that the gap's own
advice fixes it.

**#23 — the artifacts that define "correct" are ungated.** Run against the real gate with
no RFC cited:

```
a renderer                        blocked (RFC required)
SKILL.md                          blocked (RFC required)
a fixture input (the app itself)  PASSES the gate
a golden output tree              PASSES the gate
truth.yaml — the ground truth     PASSES the gate
a recorded gap-count baseline     PASSES the gate
```

The golden trees turn out to be the weakest part of this: changing generated output means
changing a renderer or a detector, both of which live under `skills/` and are already gated.
The exposure is the other two, because they are **assertions, not outputs**. RFC-0001 has CI
gate on three series — golden trees, agreement with `truth.yaml`, and bare gap count by
severity — and each compares the code against a checked-in file that any pull request can
edit. `truth.yaml` is the one that matters: it is hand-written, deliberately independent of
anything a tool produces, and it is the only thing that fails when a detector guesses.
Lowering it makes a regression green, which is the failure the RFC's own Problem section
names — *"a golden-file test locks the wrong answer in and defends it against correction"* —
arriving through the file designed to prevent it.

Demonstrated by sabotaging the credential detector so it reports nothing:

```
sabotaging the credential detector drops blocking gaps 7 -> 6 (reads as an
improvement to the gap series) and truth.yaml catches it
```

The gap-count series read a removed safety check as progress. Only ground truth objected.

There is a second vector the RFC nearly closes. It guards carefully against gaming the
metric through *answers* — *"a contributor whose change trips the gate could accept a plan
entry that echoes a gap's own `proposed` value"* — and solves it with the bare/answered
split. The same move works through **fixture inputs**, which are equally ungated: adding a
`.python-version` file retires the runtime-version gap, the bare count falls, no detector was
touched and no answer was involved. Relatedly the RFC states an invariant — *"For every
field a detector is supposed to determine, at least one corpus fixture must carry no answer
for it"* — that nothing checks.

**Resolved by making it loud rather than blocked.** `scripts/check_fixtures.py truth`
asserts every fixture's detected facts against its hand-written `truth.yaml` and is wired
into `make check`; `check_fixtures.py guard` reports what a change does to the assertions and
fails unless the pull-request body says why, on a line beginning `Assertion-change:`. There
is no checked-in gap-count baseline — a number a human can edit is a number a human can
lower — so it is computed from the base commit, each side measured with its own detectors.

Gating `fixtures/` was the obvious alternative and is out of proportion: adding a fixture is
a normal, desirable contribution, and correcting a typo in ground truth would need an
accepted RFC. Over-gating teaches people to write throwaway RFCs, which devalues the gate it
is meant to protect. There is also a practical asymmetry worth recording — `rfcs/README.md`
requires an RFC for *"any change to this process itself"* and exempts *"CI changes"*, so
adding paths to `GATED_PREFIXES` **cannot land without its own RFC**, while a new CI check
can. Gating remains the stronger guarantee and is worth revisiting once there is a real
corpus and some history of how these files actually get edited.

**A note on the answers file format.** RFC-0001 calls it `answers.yaml`; this spike writes
`answers.json`. The file is committed to the user's repository and rewritten by the tool, so
it has to be byte-stable across versions, and YAML has many valid serialisations of the same
data where JSON has one obvious one. Minor, but it is the kind of thing that is free to
decide now and expensive later.

---

## What a real repository did to it

Everything above was measured against the fixture. Running the same pipeline against a real
Emergent application — a 2.0G checkout, FastAPI + CRA + Mongo, the exact scenario v1
targets — produced findings #35 to #39. They matter more than most of the list above,
because they are not design disagreements: they are the tool being confidently wrong.

It crashed first. `KeyError: 'lineno'` — the port detector assumed a
`uvicorn.run(app, host=..., port=...)` call in `server.py`, which the fixture had because I
put it there. A real Emergent backend has no such call: it is started by a `Procfile` and a
`Dockerfile`. Fixed by reading those, and by emitting a blocking gap when neither exists.

**#35 — the fixture is single-file, and that validated every module-scoped detector against
a shape real repositories do not have.** The fixture keeps its routes, its env reads and its
app object in one `server.py`. The real application spreads them across `routes/*.py`,
`services/*.py` and `security/*.py`, and every detector that parses one module collapsed:

| | fixture | real repository |
|---|---|---|
| env vars found in the backend | 2 of 2 | **1 of 15** |
| route decorators found | 5 | **0** (8+ files under `routes/`, 2–8 decorators each) |
| health probe | `/api/health`, correct | **none found** |
| datastore env keys | `[DB_NAME, MONGO_URL]` | **`[]`** |
| `.env` keys reported as unused | 1, correctly | **14, every one a false positive** |

`JWT_SECRET` is read in `routes/auth.py`. `RESEND_API_KEY` in `services/resend_service.py`.
`FRONTEND_URL` in `routes/auth.py`. The tool tells the user to drop all fourteen.

And the output validates. The generated backend Deployment carries exactly one environment
variable — `CORS_ORIGINS`, the only one `server.py` happens to read — with no database
configuration, no JWT secret, no probes, and `kubeconform -strict` reports 7 valid,
0 invalid. That is the "confident and wrong" failure the RFC's Problem section is built
around, reproduced by the tool the RFC describes, on the scenario it targets.

The lesson is about fixtures, not about detectors. A fixture that is *plausible* is not
enough; it has to be *structurally* representative, and single-file was the one property
that mattered and the one I did not think to vary. Every golden test would have passed
forever.

**#36 — the datastore detector keys off dependencies, which lag reality.** It reports
`mongodb` because `motor` is in `requirements.txt`. The application has 76 files importing
motor or pymongo, **117** importing `firebase_admin` or `firestore`, and a
`migrations/mongo_to_firestore.py`. It is mid-migration, and the tool would generate a
MongoDB `secretKeyRef` nothing consumes while missing Firestore entirely. A dependency is
evidence that a datastore was *once* used, which is not the same claim.

**#37 — a `build_arg` is public by construction, and the credential detector does not know
it.** `REACT_APP_FIREBASE_API_KEY` was reported as a committed credential to rotate before
cutover. Firebase web API keys are designed to ship in the bundle; rotating one is not the
advice, and the false positive sits in the same list as five genuine ones
(`JWT_SECRET`, `GOOGLE_CLIENT_SECRET`, `RESEND_API_KEY`, `RESEND_WEBHOOK_SECRET`,
`GOOGLE_APPLICATION_CREDENTIALS`), diluting them. The rule to add is not subtle: anything
with `binding: build_arg` is compiled into a file served to every browser, so it can never
be secret — and if one *is* a real secret, the correct message is the opposite of rotation
advice, it is "this is already public".

**#38 — v1 is scoped greenfield, and a real Emergent repository is not greenfield.** This
one already has `backend/Dockerfile`, `backend/Procfile`, `deploy.sh` and `DEPLOYMENT.md`.
`render` would write a second, competing `services/backend/Dockerfile` beside the one that
already works, and nothing would say which is authoritative. The RFC puts "adopting an
existing deployment repository" out of scope as the *second*, more valuable mode for
established teams — but the first real repository to hand already needs it, which is worth
knowing before the greenfield assumption is baked in.

**#39 — the tree walk excludes `node_modules` but not `.venv`, `build/`, `dist/` or
`.git`.** The fixture has none of them. The real repository has three virtualenvs, one
inside `backend/`. It did not bite — no `.env` happened to live in a venv — but
`detect_committed_credentials` walks them, and a dependency shipping an example `.env` would
be reported as the user's committed credential.

**What did work, on a real repository**: the platform fingerprint; the app name derived from
the git remote rather than the directory (#17 holding up outside the fixture); provenance; the port
from `EXPOSE` and the start command from the `Procfile`; `yarn install --frozen-lockfile`
from a real lock file; five genuine committed credentials found and reported with the key
and the file; five `REACT_APP_*` build args identified. The whole scan took 1.7 seconds
across 2.0G.

And the rule that matters most held. Checking all 18 credential values of length ≥ 8 from
the real `.env` files, plus `private_key`, `private_key_id` and `client_email` from a
committed Google service-account JSON, against every file the scan wrote:

```
checked 18 values of length >= 8 from the real .env files
leaks found: 0
private_key: absent from scan output
```

## The AppSpec, as it ended up

Every departure from RFC-0001 carries a `# RFC-0001: ...` note at its definition in
`spec.py`.

| Change | Why | Finding |
|---|---|---|
| `EnvVar.value` added | nowhere to put a literal | #1 |
| `EnvVar.sensitive` added; `source` reduced to provenance | one enum cannot say "from the datastore" *and* "is a credential" | #4 |
| `Build.output_dir` added | the static-build-output detector had nowhere to write | #2 |
| `Build.args` **removed** | duplicates `EnvVar` with `binding: build_arg` | #29 |
| `Build.start_cmd` nullable | a static service has no start command of its own | #3 |
| `Service.health` → `Service.probes` | liveness and readiness are different checks | #24 |
| `Service.ports` may be empty | the renderer owns the port of a built-then-served service | #3 |
| `Route.port` nullable | null means "wire to the Service's named port" | #3 |
| `Datastore.name` added | keyed on `kind`, two instances collide | #25 |
| `Environment.namespace` added | every overlay needs one | #5 |
| `Delivery` added, five fields | registry, tag, GitOps repo, ingress class, cluster | #5, #26 |
| `Answer.accepted_by` added | a per-build value is answered by CI, not by a person | #26 |
| `Gap.evidence_scope` added | where a citation bears on the question | #34 |
| `Answer.context` added | citations shown to a reviewer but never hashed | #34 |
| `PlanEntry.evidence` carries a hash | implements the RFC's undefined "and to match" | #34 |
| `AppSpec.name` nullable | null is a blocking gap; never derived from the filesystem | #17 |
| `Gap.pointer` → `Gap.pointers` | zero-to-many, not one-to-one | #8 |
| `Gap.kind` added | value or action | #20 |
| `Gap.depends_on` added | a gap another answer makes moot is not asked | #21 |
| `Answer.kind` added | an action is acknowledged, not valued | #20 |
| `evidence.json`, `answerable.json`, `detected.json` | the three sidecars the RFC's machinery assumes and never names | #11, #5, #33 |

## The gap list, answered

22 bare gaps: 7 blocking, 9 important, 6 cosmetic. Four are actions rather than values. The
full text is in `gaps.md` from any run; `fixtures/plans/emergent-fastapi-mongo.plan.json`
answers them, with a rationale per entry.

The answers are synthetic, as the fixture is: `example.com` is reserved for documentation
and names no real host or account.

| Gap | Answer |
|---|---|
| `app.name` | `status-board` |
| `route.host` | `status-board.example.com` |
| `service.frontend.env.REACT_APP_BACKEND_URL.value` | `""` — same origin, so the bundle calls `/api` on whatever host served it |
| `datastore.mongodb.mode` | `managed` — which retired `datastore.mongodb.version` unasked |
| `delivery.image_registry` | `registry.example.com/example-org` |
| `delivery.gitops_repo_url` | the application repository, per #14 |
| `delivery.ingress_class` | `nginx` |
| `delivery.image_tag` | `unbuilt` for the first render; CI overwrites it per build (#26) |
| `environment.production.namespace` | `status-board-production` |
| `service.{backend,frontend}.runtime.version` | `3.11`, `20` |
| `service.{backend,frontend}.resources` | backend CPU limit raised to a full core; frontend accepted |
| `service.{backend,frontend}.replicas` | 2 each, so a rolling deploy is not an outage |
| `service.frontend.{ports,probes}` | accepted as generated — 8080, probe on `/` |
| `secret.backend.env.MONGO_URL` | *action*: rotated |
| `service.backend.build.pinning` | *action*: transitive set frozen |
| `service.frontend.build.lockfile` | *action*: `yarn.lock` committed |
| `service.backend.env.CORS_ORIGINS.unused` | *action*: dropped |

Result: **0 gaps, no placeholders, no unresolved-gap markers**, `kustomize build` and
`kubeconform` clean, both images build, and the frontend serves.

## What this spike still does not test

- **`show`, and the skill.** The two remaining surfaces in RFC-0001's build order.
- **A second renderer.** The RFC's central claim is N+M rather than N×M, and one renderer
  cannot test it. Every field this spike had to add is Kubernetes-shaped, which is worth
  checking against a Compose or Terraform renderer before accepting.
- **A second scenario.** One fixture, one platform.
- **Multiple environments.** With a single `production` overlay, the overlay carries a
  namespace and an image transform and nothing else, so the claim that adding `staging` is
  "additive instead of structural" is untested.
- **The undecidable prefix case in a fixture.** The detector's refusal branch is exercised
  directly, but no fixture carries a settings-derived prefix. There should be one — it is the
  case the model surface exists to carry.
- **Golden trees and provenance sidecars.** `truth.yaml` and the gap-count series are now
  built (#23); `expected_bare/` and `expected_answered/` are not, and `spike_check.sh`
  asserts behaviour rather than bytes.
- **`kind: override`.** Specified in full by the RFC, excluded from v1, and rejected here.
- **Anything against a real cluster.** By design.
