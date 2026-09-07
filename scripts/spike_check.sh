#!/usr/bin/env bash
# SPIKE ONLY. Reproduces every claim in SPIKE.md. Read-only: it touches no real
# infrastructure. Docker steps are skipped when no daemon is running.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; docker rm -f offramp-smoke >/dev/null 2>&1 || true' EXIT
S="$ROOT/skills/offramp/scripts"
SCHEMA="$ROOT/skills/offramp/schemas/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json"
PLAN="$ROOT/fixtures/plans/emergent-fastapi-mongo.plan.json"
APP="$WORK/emergent-fastapi-mongo"
NOW="2026-09-07T00:00:00Z"

step() { printf '\n\033[1m== %s\033[0m\n' "$1"; }
fail() { printf '\033[31mFAIL: %s\033[0m\n' "$1"; exit 1; }
stamp() {  # stamp a plan's basis from a scan: the basis moves whenever the spec does
  python3 -c "
import json,sys; sys.path.insert(0,'$S')
from render import load_appspec; from pathlib import Path
plan=json.load(open(sys.argv[1])); _,sha=load_appspec(Path(sys.argv[2]))
plan['basis']['content_sha256']=sha; json.dump(plan, open(sys.argv[3],'w'), indent=2)" "$@"
}
gapcount() { python3 -c "import json;print(len(json.load(open('$1/gaps.json'))))"; }

mkdir -p "$APP"; cp -R "$ROOT/fixtures/emergent-fastapi-mongo/." "$APP/"

step "scan (bare, no answers)"
python3 "$S/scan.py" "$APP" --out "$WORK/bare"
BARE=$(gapcount "$WORK/bare")

step "render"
python3 "$S/render.py" "$WORK/bare/appspec.json" --out "$APP" >/dev/null
echo "rendered $(python3 -c "import json;print(len(json.load(open('$APP/.offramp/manifest.json'))['files'])+1)") files"

step "verify: output tree and scan artifacts, both re-derived (#9)"
python3 "$S/verify.py" "$WORK/bare" --tree "$APP" --repo "$APP"

step "determinism: render twice, diff the trees"
python3 "$S/render.py" "$WORK/bare/appspec.json" --out "$WORK/d1" >/dev/null
python3 "$S/render.py" "$WORK/bare/appspec.json" --out "$WORK/d2" >/dev/null
diff -r "$WORK/d1" "$WORK/d2" && echo "byte-identical"

step "determinism vs. the clone directory name (#17)"
cp -R "$ROOT/fixtures/emergent-fastapi-mongo" "$WORK/a-different-name"
python3 "$S/scan.py" "$WORK/a-different-name" --out "$WORK/spec2" >/dev/null
python3 "$S/render.py" "$WORK/spec2/appspec.json" --out "$WORK/tree2" >/dev/null
CHANGED=$(diff -rq "$WORK/d1" "$WORK/tree2" 2>/dev/null | wc -l | tr -d ' ' || true)
echo "same repository in a differently-named directory: $CHANGED of $(find "$WORK/d1" -type f | wc -l | tr -d ' ') files differ"
[ "$CHANGED" = "0" ] || fail "#17 regressed"

step "every gap named in the tree exists in gaps.json (#7, #13, #22)"
MARKED=$(grep -rhoE "offramp: unresolved gap [A-Za-z0-9._-]+" "$APP/k8s" "$APP/services" "$APP/gitops" | awk '{print $NF}' | sort -u)
[ -n "$MARKED" ] || fail "no gap markers in the tree at all"
for id in $MARKED; do
  python3 -c "
import json,sys
sys.exit(0 if '$id' in {g['id'] for g in json.load(open('$WORK/bare/gaps.json'))} else 1)" \
    || fail "tree names gap '$id', which scan never emitted"
done
echo "$(echo "$MARKED" | wc -l | tr -d ' ') distinct gap ids named in the tree, all present"

step "verify fails on a tampered file"
sed -i.bak 's|path: /api/health|path: /health|g' "$APP/k8s/base/backend-deployment.yaml"; rm -f "$APP"/k8s/base/*.bak
python3 "$S/verify.py" "$WORK/bare" --tree "$APP" >/dev/null 2>&1 && fail "verify missed the tamper"
echo "verify exited non-zero, as it must"
python3 "$S/render.py" "$WORK/bare/appspec.json" --out "$APP" --force >/dev/null

step "render refuses to overwrite a file it did not write (#14)"
mkdir -p "$WORK/collide"; echo "mine, not offramp's" > "$WORK/collide/.dockerignore"
python3 "$S/render.py" "$WORK/bare/appspec.json" --out "$WORK/collide" >/dev/null 2>&1 \
  && fail "render clobbered a pre-existing file"
grep -q "mine, not offramp" "$WORK/collide/.dockerignore" || fail "the file changed anyway"
echo "render exited non-zero and left the file alone"

step "apply: every rejection path RFC-0001 names"
SHA=$(python3 -c "import sys;sys.path.insert(0,'$S')
from render import load_appspec; from pathlib import Path
print(load_appspec(Path('$WORK/bare/appspec.json'))[1])")
mkplan() { python3 -c "
import json,sys
json.dump({'basis':{'content_sha256':sys.argv[1],'repo_commit':None,'detectors_version':sys.argv[2]},
           'entries':json.loads(sys.argv[3])}, open('$WORK/p.json','w'))" "$@"; }
expect_reject() {
  python3 "$S/apply.py" "$WORK/p.json" --scan "$WORK/bare" --repo "$APP" --now "$NOW" \
    --out "$WORK/o.json" --accept-all-resolves >/dev/null 2>"$WORK/err" && fail "$1 was accepted"
  grep -q . "$WORK/err" || fail "$1 produced no rejection message"
  printf '  rejected: %s\n' "$1"
}
E='[{"target":"app.name","kind":"resolve","current":null,"proposed":"x","rationale":"r","evidence":[],"confidence":"high"}]'
mkplan deadbeef spike-2 "$E";                  expect_reject "basis does not match"
mkplan "$SHA" spike-1 "$E";                    expect_reject "plan made against other detectors"
mkplan "$SHA" spike-2 '[{"target":"service.backend.replicas","kind":"resolve","current":9,"proposed":3,"rationale":"r","evidence":[],"confidence":"high"}]'
                                               expect_reject "compare-and-swap failed"
mkplan "$SHA" spike-2 '[{"target":"source.commit","kind":"resolve","current":null,"proposed":"a","rationale":"r","evidence":[],"confidence":"high"}]'
                                               expect_reject "target outside the answerable set"
mkplan "$SHA" spike-2 "[${E:1:${#E}-2},${E:1:${#E}-2}]"
                                               expect_reject "two entries target the same id"
mkplan "$SHA" spike-2 '[{"target":"app.name","kind":"resolve","current":null,"proposed":"a","rationale":"r","evidence":["backend/nope.py:9999"],"confidence":"high"}]'
                                               expect_reject "evidence does not resolve"
mkplan "$SHA" spike-2 '[{"target":"service.backend.replicas","kind":"override","current":1,"proposed":5,"rationale":"r","evidence":[],"confidence":"high"}]'
                                               expect_reject "kind override, in a v1 that accepts none"
python3 "$ROOT/scripts/spike_secret_guard.py" "$WORK/bare" "$APP" || fail "the secret guard did not fire"

step "an unstamped plan is refused where evidence is load-bearing (#34)"
stamp "$PLAN" "$WORK/bare/appspec.json" "$WORK/plan.json"
python3 "$S/apply.py" "$WORK/plan.json" --scan "$WORK/bare" --repo "$APP" --now "$NOW" \
  --out "$WORK/unstamped.json" --accept-all-resolves >/dev/null 2>"$WORK/err" \
  && fail "an unstamped plan was applied"
grep -q "carries no hash" "$WORK/err" || fail "the rejection was not about the missing hash"
echo "  refused: apply cannot tell whether an unhashed citation still says what the plan read"

step "apply the checked-in plan: 22 gaps answered one by one"
python3 "$ROOT/scripts/stamp_plan.py" "$WORK/plan.json" "$APP" | sed 's/^/  /'
python3 "$S/apply.py" "$WORK/plan.json" --scan "$WORK/bare" --repo "$APP" --now "$NOW" \
  --out "$APP/answers.json" --accept-all-resolves
python3 "$S/scan.py" "$APP" --answers "$APP/answers.json" --out "$WORK/ans"
ANSWERED=$(gapcount "$WORK/ans")
echo "bare gap count $BARE -> answered gap count $ANSWERED"
[ "$ANSWERED" = "0" ] || fail "expected every gap answered, $ANSWERED remain"

step "render and verify the answered tree"
python3 "$S/render.py" "$WORK/ans/appspec.json" --out "$APP" --force >/dev/null
python3 "$S/verify.py" "$WORK/ans" --tree "$APP" --repo "$APP" --answers "$APP/answers.json"
if grep -rn "REPLACE-ME\|replace-me\|unresolved gap" "$APP/k8s" "$APP/services" "$APP/gitops" 2>/dev/null; then
  fail "placeholders or unresolved-gap markers remain in an answered tree"
fi
echo "no placeholders and no unresolved-gap markers remain"

step "an answer can be revised (#32), and detected stays the detector's (#33)"
python3 -c "
import json,sys; sys.path.insert(0,'$S')
from render import load_appspec; from pathlib import Path
_,sha=load_appspec(Path('$WORK/ans/appspec.json'))
json.dump({'basis':{'content_sha256':sha,'repo_commit':None,'detectors_version':'spike-2'},
 'entries':[{'target':'route.host','kind':'resolve','current':'status-board.example.com',
 'proposed':'status.example.com','rationale':'r','evidence':[],'confidence':'high'}]},
 open('$WORK/revise.json','w'))"
python3 "$S/apply.py" "$WORK/revise.json" --scan "$WORK/ans" --repo "$APP" --now "$NOW" \
  --answers "$APP/answers.json" --out "$APP/answers.json" --accept-resolve route.host >/dev/null
python3 "$S/scan.py" "$APP" --answers "$APP/answers.json" --out "$WORK/ans" >/dev/null
[ "$(gapcount "$WORK/ans")" = "0" ] || fail "revising an answer left gaps behind"
python3 "$S/render.py" "$WORK/ans/appspec.json" --out "$APP" --force >/dev/null
grep -q "host: status.example.com" "$APP/k8s/base/ingress.yaml" || fail "the revision did not reach the Ingress"
echo "revised route.host, 0 gaps, and the Ingress followed"

step "a build deploys through apply, and verify stays green (#26)"
# The decision on #26: a per-build value reaches the tree as an answer, never as an
# edit to generated output. This is that loop, with no model and no key.
grep -q "newTag: unbuilt" "$APP/k8s/overlays/production/kustomization.yaml" \
  || fail "expected the placeholder tag before the first build"
python3 -c "
import json,sys; sys.path.insert(0,'$S')
from render import load_appspec; from pathlib import Path
_,sha=load_appspec(Path('$WORK/ans/appspec.json'))
json.dump({'basis':{'content_sha256':sha,'repo_commit':None,'detectors_version':'spike-2'},
 'entries':[{'target':'delivery.image_tag','kind':'resolve','current':'unbuilt',
 'proposed':'9f3a1c2','rationale':'the commit CI just built','evidence':[],
 'confidence':'high'}]}, open('$WORK/build.json','w'))"
python3 "$S/apply.py" "$WORK/build.json" --scan "$WORK/ans" --repo "$APP" --now "$NOW" \
  --answers "$APP/answers.json" --out "$APP/answers.json" \
  --accept-resolve delivery.image_tag --accepted-by ci >/dev/null
python3 "$S/scan.py" "$APP" --answers "$APP/answers.json" --out "$WORK/ans" >/dev/null
python3 "$S/render.py" "$WORK/ans/appspec.json" --out "$APP" --force >/dev/null
python3 "$S/verify.py" "$WORK/ans" --tree "$APP" --repo "$APP" --answers "$APP/answers.json" >/dev/null \
  || fail "verify is red after a CI-driven build"
grep -q "newTag: 9f3a1c2" "$APP/k8s/overlays/production/kustomization.yaml" \
  || fail "the built tag did not reach the overlay"
[ "$(gapcount "$WORK/ans")" = "0" ] || fail "the build left gaps behind"
python3 -c "
import json,sys
by={a['target']:a for a in json.load(open('$APP/answers.json'))['answers']}
ci=[t for t,a in by.items() if a['accepted_by']=='ci']
human=[t for t,a in by.items() if a['accepted_by']=='human']
print(f'  tag reached the overlay, verify green, 0 gaps')
print(f'  answers: {len(human)} accepted by a human, {len(ci)} by ci -> {ci}')
sys.exit(0 if ci==['delivery.image_tag'] else 1)" || fail "accepted_by is not recording provenance"

step "editing the overlay directly is still rejected (#26, the other half)"
sed -i.bak 's/newTag: 9f3a1c2/newTag: deadbee/' "$APP/k8s/overlays/production/kustomization.yaml"
rm -f "$APP"/k8s/overlays/production/*.bak
python3 "$S/verify.py" "$WORK/ans" --tree "$APP" >/dev/null 2>&1 \
  && fail "verify accepted a hand-edited image tag"
echo "  verify rejects a tag edited into generated output, as it must"
python3 "$S/render.py" "$WORK/ans/appspec.json" --out "$APP" --force >/dev/null

step "cited evidence: drift is refused, padding is inert (#34)"
# Drift: the line a plan cited changes between review and apply. The basis check
# does not cover this -- a dependency bump moves no AppSpec field -- so nothing did.
cp -R "$APP" "$WORK/drift"
python3 -c "
import json,sys; sys.path.insert(0,'$S')
from render import load_appspec; from pathlib import Path
_,sha=load_appspec(Path('$WORK/ans/appspec.json'))
json.dump({'basis':{'content_sha256':sha,'repo_commit':None,'detectors_version':'spike-2'},
 'entries':[{'target':'service.backend.runtime.version','kind':'resolve','current':'3.11',
 'proposed':'3.12','rationale':'r','evidence':['backend/requirements.txt:1'],
 'confidence':'high'}]}, open('$WORK/drift.json','w'), indent=2)"
python3 "$ROOT/scripts/stamp_plan.py" "$WORK/drift.json" "$WORK/drift" >/dev/null
python3 -c "
p='$WORK/drift/backend/requirements.txt'; r=open(p).read().splitlines()
r[0]='fastapi==0.999.0'; open(p,'w').write(chr(10).join(r)+chr(10))"
python3 "$S/apply.py" "$WORK/drift.json" --scan "$WORK/ans" --repo "$WORK/drift" --now "$NOW" \
  --answers "$WORK/drift/answers.json" --out "$WORK/drift/answers.json" \
  --accept-resolve service.backend.runtime.version >/dev/null 2>"$WORK/err" \
  && fail "a plan whose evidence moved between review and apply was accepted"
grep -q "has changed since the plan was written" "$WORK/err" || fail "wrong rejection reason"
echo "  refused: a cited line that moved between the plan being written and applied"

# Padding: out-of-scope citations are carried as context, never hashed, so they can
# never re-ask the question they had nothing to do with.
python3 -c "
import json,sys
by={a['target']:a for a in json.load(open('$APP/answers.json'))['answers']}
name=by['app.name']
if name['evidence']:
    print('  app.name hashed a citation it should only have shown:', name['evidence']); sys.exit(1)
if not name['context']:
    print('  app.name lost the citations that justify it'); sys.exit(1)
print('  app.name: hashed none, shows', name['context'])
ver=by['service.backend.runtime.version']
if not ver['evidence']:
    print('  an in-scope citation was not hashed'); sys.exit(1)
print('  service.backend.runtime.version: hashed', [e['path'] for e in ver['evidence']])"  \
  || fail "#34: the shown/hashed split is not working"

step "a stale answer becomes a gap, it is not merged (#11)"
cp -R "$APP" "$WORK/stale"
python3 -c "
p='$WORK/stale/backend/requirements.txt'
rows=open(p).read().splitlines(); rows[0]='fastapi==0.115.0'
open(p,'w').write(chr(10).join(rows)+chr(10))"
python3 "$S/scan.py" "$WORK/stale" --answers "$WORK/stale/answers.json" --out "$WORK/stale-scan" >/dev/null
python3 -c "
import json,sys
rows=[g for g in json.load(open('$WORK/stale-scan/gaps.json')) if g['origin']=='stale_answer']
print(f'  {len(rows)} stale_answer gap(s) after moving one cited line:', [g['id'] for g in rows])
sys.exit(0 if rows else 1)" || fail "moving cited evidence did not invalidate its answer"

step "an orphaned answer becomes a gap, it is not dropped"
cp -R "$APP" "$WORK/orph"; mv "$WORK/orph/backend" "$WORK/orph/api"
python3 "$S/scan.py" "$WORK/orph" --answers "$WORK/orph/answers.json" --out "$WORK/orph-scan" >/dev/null
python3 -c "
import json,sys
rows=[g for g in json.load(open('$WORK/orph-scan/gaps.json')) if g['origin']=='orphaned_answer']
print(f'  {len(rows)} orphaned_answer gap(s) after renaming a service')
sys.exit(0 if rows else 1)" || fail "renaming a service silently dropped its answers"

fake_remote() {  # $1 = directory, $2 = repository name in the remote URL
  mkdir -p "$1/.git/refs/heads"
  printf '[remote "origin"]\n\turl = https://git.example.com/example-org/%s.git\n' "$2" > "$1/.git/config"
  printf 'ref: refs/heads/main\n' > "$1/.git/HEAD"
  printf '%040d\n' 0 > "$1/.git/refs/heads/main"
}

step "a graduated detector that DISAGREES reads as stale, not as an orphan (#30)"
cp -R "$APP" "$WORK/dis"; fake_remote "$WORK/dis" status-board-app
python3 "$S/scan.py" "$WORK/dis" --answers "$WORK/dis/answers.json" --out "$WORK/dis-scan" >/dev/null
python3 -c "
import json,sys
rows=json.load(open('$WORK/dis-scan/gaps.json'))
stale=[g['id'] for g in rows if g['origin']=='stale_answer']
orph=[g['id'] for g in rows if g['origin']=='orphaned_answer']
ids=[g['id'] for g in rows]
print('  stale:', stale, ' orphaned:', orph)
if len(ids)!=len(set(ids)): print('  duplicate gap ids:', ids); sys.exit(1)
sys.exit(0 if 'app.name' in stale and not orph else 1)" \
  || fail "a graduated detector that disagrees was mishandled"

step "a graduated detector that AGREES is merged, not re-asked (#31)"
cp -R "$APP" "$WORK/grad"; fake_remote "$WORK/grad" status-board
python3 "$S/scan.py" "$WORK/grad" --answers "$WORK/grad/answers.json" --out "$WORK/grad-scan" \
  | tail -2 | sed 's/^/  /'
python3 -c "
import json,sys
rows=json.load(open('$WORK/grad-scan/gaps.json'))
if rows:
    print('  a detector improving raised the answered gap count:', [g['id'] for g in rows])
    sys.exit(1)
md=open('$WORK/grad-scan/gaps.md').read()
sys.exit(0 if 'answers a detector has caught up with' in md and 'app.name' in md else 1)" \
  || fail "#31: a detector agreeing with an answer did not merge cleanly"
echo "  answered gap count stayed 0 when a detector improved"

step "detected facts match hand-written ground truth (#23)"
python3 "$ROOT/scripts/check_fixtures.py" truth | sed 's/^/  /'
# A checker that cannot fail is decoration. Sabotage the credential detector: the
# bare gap count *falls*, which the gap-count series reads as an improvement, and
# only truth.yaml objects. That is the argument RFC-0001 makes for having it.
cp -R "$ROOT/skills/offramp/scripts" "$WORK/scripts-backup"
python3 -c "
from pathlib import Path
p = Path('$ROOT/skills/offramp/scripts/detect.py')
p.write_text(p.read_text().replace(
    'def detect_committed_credentials(root: Path) -> list[Gap]:',
    'def detect_committed_credentials(root: Path) -> list[Gap]:\n    return []', 1))"
OUT=$(python3 "$ROOT/scripts/check_fixtures.py" truth 2>&1 || true)
cp -R "$WORK/scripts-backup/." "$ROOT/skills/offramp/scripts/"
python3 "$ROOT/scripts/check_fixtures.py" truth >/dev/null || fail "could not restore the detector"
echo "$OUT" | grep -q "committed credential backend/.env:MONGO_URL was not reported" \
  || fail "truth.yaml did not catch a detector that stopped reporting a credential"
echo "$OUT" | grep -q "blocking=6" \
  || fail "expected the sabotage to LOWER the blocking gap count"
echo "  sabotaging the credential detector drops blocking gaps 7 -> 6 (reads as an"
echo "  improvement to the gap series) and truth.yaml catches it"

step "weakening an assertion costs a sentence (#23)"
# Needs a base that actually has a truth.yaml, so this runs in a scratch repo.
G="$WORK/guarded"; mkdir -p "$G"
tar -cf - -C "$ROOT" $(cd "$ROOT" && git ls-files --cached --others --exclude-standard) | tar -xf - -C "$G"
( cd "$G" && git init -q . && git add -A \
    && git -c user.email=s@example.com -c user.name=spike commit -qm base )
sed -i.bak 's|liveness: /api/health|liveness: /health|' "$G/fixtures/emergent-fastapi-mongo/truth.yaml"
rm -f "$G/fixtures/emergent-fastapi-mongo/"*.bak
printf 'Fixes a thing.\n' > "$G/body.txt"
( cd "$G" && python3 scripts/check_fixtures.py guard --base HEAD --body-file body.txt ) \
  >/dev/null 2>&1 && fail "ground truth was weakened with no acknowledgement"
echo "  refused: ground truth changed with nothing said about it"
printf 'Fixes a thing.\n\nAssertion-change: the probe path was recorded wrong.\n' > "$G/body.txt"
( cd "$G" && python3 scripts/check_fixtures.py guard --base HEAD --body-file body.txt ) >/dev/null \
  || fail "an acknowledged assertion change was still refused"
echo "  accepted once the pull-request body says why"

step "no secret value in any generated file, or in the committed answers file"
for needle in NOT_A_REAL_PASSWORD_2f9c demo_user 'mongodb://' emergentagent; do
  grep -R -q -F "$needle" "$APP/k8s" "$APP/services" "$APP/gitops" "$APP/.offramp" \
    "$APP/answers.json" "$WORK/ans" 2>/dev/null && fail "'$needle' reached generated output"
  echo "  absent: $needle"
done

step "kustomize build + kubeconform"
kustomize build "$APP/k8s/overlays/production" > "$WORK/built.yaml"
kubeconform -strict -summary -kubernetes-version 1.30.0 "$WORK/built.yaml"
echo "and the ApplicationSet, against the checked-in CRD schema (#18):"
kubeconform -strict -summary -schema-location default -schema-location "$SCHEMA" \
  "$APP/gitops/applicationset.yaml"

if ! docker info >/dev/null 2>&1; then
  step "docker"; echo "skipped: no docker daemon"
  printf '\n\033[1mspike checks passed\033[0m\n'; exit 0
fi

step "docker build, then: does the image start and answer its own probe path? (#16)"
( cd "$APP" && docker build -q -f services/frontend/Dockerfile -t offramp-spike-frontend . >/dev/null )
docker rm -f offramp-smoke >/dev/null 2>&1 || true
docker run -d --name offramp-smoke -p 18080:8080 offramp-spike-frontend >/dev/null
for _ in $(seq 15); do curl -sf -m 1 http://127.0.0.1:18080/ >/dev/null 2>&1 && break; sleep 1; done
CODE=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:18080/)
docker rm -f offramp-smoke >/dev/null
[ "$CODE" = "200" ] || fail "frontend image does not answer its own probe path (got $CODE)"
echo "frontend: probe path / -> HTTP 200, with REACT_APP_BACKEND_URL baked in from the answer"

( cd "$APP" && docker build -q -f services/backend/Dockerfile -t offramp-spike-backend . >/dev/null )
docker run --rm --entrypoint python offramp-spike-backend -c "import server" >/dev/null 2>&1 \
  && fail "#16 no longer reproduces: the under-pinned backend imports cleanly"
echo "backend as committed: builds, then fails on import -- #16 still reproduces"

cp -R "$APP" "$WORK/pinned"; echo 'pymongo==4.6.3' >> "$WORK/pinned/backend/requirements.txt"
( cd "$WORK/pinned" && docker build -q -f services/backend/Dockerfile -t offramp-spike-backend-pinned . >/dev/null )
docker rm -f offramp-smoke >/dev/null 2>&1 || true
docker run -d --name offramp-smoke -p 18001:8001 -e MONGO_URL="mongodb://127.0.0.1:27017" \
  -e DB_NAME=smoke offramp-spike-backend-pinned >/dev/null
for _ in $(seq 15); do curl -sf -m 1 http://127.0.0.1:18001/api/health >/dev/null 2>&1 && break; sleep 1; done
GOOD=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:18001/api/health)
BAD=$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:18001/health)
docker rm -f offramp-smoke >/dev/null
echo "backend with the pinning gap's advice applied:"
echo "  /api/health (what the detector composed) -> HTTP $GOOD"
echo "  /health     (the decorator literal)      -> HTTP $BAD"
[ "$GOOD" = "200" ] || fail "the composed probe path does not answer"
[ "$BAD" = "404" ] || fail "the decorator literal answers; the fixture no longer tests composition"

printf '\n\033[1mspike checks passed\033[0m\n'
