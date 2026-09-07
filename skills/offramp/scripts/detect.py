"""Detectors: read a repository, contribute facts to an AppSpec, emit gaps.

SPIKE revision 2. Detectors never write files (AGENTS.md §6). Everything here is
(bytes on disk) -> (facts, gaps, evidence), iterated in sorted order, with no
clock, no environment reads and no network.

Two rules this revision adds, both from SPIKE.md:

  #22  If the tool will put a value in the output that it did not detect, it emits
       a gap. No exceptions for "obvious" defaults -- the line between an obvious
       default and a guess was left to each contributor's judgement, which would
       make the headline gap-count metric drift for reasons unrelated to detection.

  #3   A value that is not in the source repository is not the detector's to
       invent. A service compiled to static assets leaves ports and probes empty
       and the renderer fills them, under rule #22.

All paths in evidence are relative to the scan root and POSIX-separated: an
absolute path would make the AppSpec depend on where the repo was cloned.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from spec import (
    Build,
    Datastore,
    Delivery,
    EnvVar,
    Environment,
    Evidence,
    Gap,
    Probe,
    Resources,
    Route,
    Runtime,
    Service,
    Source,
    gap_id,
)

PROPOSED_PYTHON = "3.11"
PROPOSED_NODE = "20"

HEALTH_NAMES = ("health", "healthz", "livez", "readyz", "ping", "_health")
DEV_ONLY_ENV = ("WDS_", "FAST_REFRESH", "CHOKIDAR_", "BROWSER", "GENERATE_SOURCEMAP")

SECRET_NAME_RE = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|CREDENTIAL|PRIVATE)")
CREDENTIAL_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
BUILD_ARG_PREFIXES = ("REACT_APP_", "VITE_", "NEXT_PUBLIC_")


def rel(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def dns_label(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return re.sub(r"-{2,}", "-", cleaned) or "app"


# ---------------------------------------------------------------------------
# Evidence (#11)
# ---------------------------------------------------------------------------

def cite(root: Path, path: Path, line: int) -> Evidence:
    """Hash the cited line so a stored answer can be checked for staleness.

    A line in a .env file is hashed by its key only. The hash of a full line is not
    the credential, but it verifies a guess at one, and AGENTS.md §4 says a secret
    value must not reach a fixture, a test, a log or an error message. Nothing is
    lost: an EnvVar whose value is sensitive is never answerable anyway.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    content = lines[line - 1] if 0 < line <= len(lines) else ""
    if path.name.startswith(".env"):
        content = content.split("=", 1)[0]
    return Evidence(
        path=rel(root, path),
        line=line,
        sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def as_text(items: list[Evidence]) -> list[str]:
    return sorted(f"{item.path}:{item.line}" for item in items)


# ---------------------------------------------------------------------------
# .env files
# ---------------------------------------------------------------------------

def parse_env_file(path: Path) -> list[tuple[str, str, int]]:
    entries = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, raw = stripped.partition("=")
        value = raw.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        entries.append((key.strip(), value, lineno))
    return entries


def is_sensitive(key: str, value: str) -> bool:
    if SECRET_NAME_RE.search(key.upper()):
        return True
    return bool(CREDENTIAL_URL_RE.match(value))


# ---------------------------------------------------------------------------
# FastAPI: the route table, after mounting
# ---------------------------------------------------------------------------

def _literal(node: ast.AST) -> Any:
    return node.value if isinstance(node, ast.Constant) else None


def _kwarg(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


class FastAPIModule:
    """The post-mounting route table of a single FastAPI module.

    This is the case RFC-0001's Problem section is about. `@api_router.get("/health")`
    reads as /health; the router carries prefix="/api" and is mounted, so the path
    that actually answers is /api/health. A detector reporting the decorator literal
    generates a probe that 404s, and nothing downstream objects.
    """

    def __init__(self, text: str, rel_path: str) -> None:
        self.rel_path = rel_path
        self.app_var: str | None = None
        self.routers: dict[str, str | None] = {}
        self.mounts: dict[str, tuple[str | None, int]] = {}
        self.routes: list[dict[str, Any]] = []
        self.listen: dict[str, Any] = {}
        self.env_reads: dict[str, int] = {}
        self.undecidable: list[str] = []
        self._walk(ast.parse(text))

    def _walk(self, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                self._assign(node)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._decorators(node)
            elif isinstance(node, ast.Call):
                self._call(node)
            elif isinstance(node, ast.Subscript):
                self._subscript(node)

    def _assign(self, node: ast.Assign) -> None:
        if not isinstance(node.value, ast.Call) or len(node.targets) != 1:
            return
        target = node.targets[0]
        if not isinstance(target, ast.Name):
            return
        func = node.value.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
        if name == "FastAPI":
            self.app_var = target.id
        elif name == "APIRouter":
            prefix_node = _kwarg(node.value, "prefix")
            if prefix_node is None:
                self.routers[target.id] = ""
            else:
                value = _literal(prefix_node)
                self.routers[target.id] = value if isinstance(value, str) else None
                if value is None:
                    self.undecidable.append(
                        f"{self.rel_path}:{prefix_node.lineno} APIRouter prefix is not a literal"
                    )

    def _call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "include_router" and node.args:
            arg = node.args[0]
            if isinstance(arg, ast.Name):
                prefix_node = _kwarg(node, "prefix")
                if prefix_node is None:
                    self.mounts[arg.id] = ("", node.lineno)
                else:
                    value = _literal(prefix_node)
                    self.mounts[arg.id] = (value if isinstance(value, str) else None, node.lineno)
                    if value is None:
                        self.undecidable.append(
                            f"{self.rel_path}:{prefix_node.lineno} "
                            "include_router prefix is not a literal"
                        )
        if isinstance(func, ast.Attribute) and func.attr == "run":
            base = func.value
            if isinstance(base, ast.Name) and base.id == "uvicorn":
                self.listen = {
                    "host": _literal(_kwarg(node, "host") or ast.Constant(None)),
                    "port": _literal(_kwarg(node, "port") or ast.Constant(None)),
                    "lineno": node.lineno,
                }
        if isinstance(func, ast.Attribute) and func.attr in {"get", "getenv"}:
            base = func.value
            is_environ_get = (
                isinstance(base, ast.Attribute)
                and base.attr == "environ"
                and isinstance(base.value, ast.Name)
                and base.value.id == "os"
            )
            is_os_getenv = isinstance(base, ast.Name) and base.id == "os" and func.attr == "getenv"
            if (is_environ_get or is_os_getenv) and node.args:
                key = _literal(node.args[0])
                if isinstance(key, str):
                    self.env_reads.setdefault(key, node.lineno)

    def _subscript(self, node: ast.Subscript) -> None:
        base = node.value
        if (
            isinstance(base, ast.Attribute)
            and base.attr == "environ"
            and isinstance(base.value, ast.Name)
            and base.value.id == "os"
        ):
            key = _literal(node.slice)
            if isinstance(key, str):
                self.env_reads.setdefault(key, node.lineno)

    def _decorators(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            func = decorator.func
            if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
                continue
            method = func.attr.upper()
            if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            path = _literal(decorator.args[0]) if decorator.args else None
            if isinstance(path, str):
                self.routes.append(
                    {
                        "owner": func.value.id,
                        "method": method,
                        "literal": path,
                        "lineno": decorator.lineno,
                    }
                )

    def effective_routes(self) -> list[dict[str, Any]]:
        resolved = []
        for route in sorted(self.routes, key=lambda r: (r["literal"], r["method"])):
            owner = route["owner"]
            if owner == self.app_var:
                prefix, mount_line = "", None
            elif owner in self.routers:
                router_prefix = self.routers[owner]
                mount_prefix, mount_line = self.mounts.get(owner, (None, None))
                if router_prefix is None or mount_prefix is None:
                    continue
                prefix = mount_prefix + router_prefix
            else:
                continue
            path = (prefix + route["literal"]).replace("//", "/")
            if len(path) > 1:
                path = path.rstrip("/")
            resolved.append({**route, "path": path, "mount_lineno": mount_line, "prefix": prefix})
        return resolved


# ---------------------------------------------------------------------------
# Identity and provenance (#17, #10)
# ---------------------------------------------------------------------------

def detect_identity(root: Path) -> tuple[str | None, str | None, str | None]:
    """-> (name, repo_url, commit), all null when there is no stable source.

    #17: revision 1 derived the app name from the scan directory, which made 10 of
    15 output files change when the same repository sat in a differently-named
    directory -- a straight violation of AGENTS.md §3 reachable on the first run.
    The name now comes from the git remote, which is a property of the repository
    rather than of the checkout. With no remote there is no stable source, and the
    honest answer is a blocking gap, not a guess.

    #10: .git is read only when the scan root is *itself* a repository. Walking
    upwards records the commit of whatever repository happens to contain the
    directory -- for a fixture, this tool's own. Nothing here shells out to git:
    a subprocess would search upwards by default.
    """
    config = root / ".git" / "config"
    head = root / ".git" / "HEAD"
    if not config.exists():
        return None, None, None

    repo_url = None
    in_origin = False
    for line in config.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_origin = stripped.replace(" ", "") == '[remote"origin"]'
        elif in_origin and stripped.startswith("url"):
            repo_url = stripped.split("=", 1)[1].strip()

    commit = None
    if head.exists():
        raw = head.read_text(encoding="utf-8").strip()
        if raw.startswith("ref: "):
            ref = root / ".git" / raw[5:]
            commit = ref.read_text(encoding="utf-8").strip() if ref.exists() else None
        elif re.fullmatch(r"[0-9a-f]{40}", raw):
            commit = raw

    name = None
    if repo_url:
        tail = repo_url.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
        name = dns_label(tail[:-4] if tail.endswith(".git") else tail)
    return name, repo_url, commit


# ---------------------------------------------------------------------------
# Platform fingerprint
# ---------------------------------------------------------------------------

def detect_platform(root: Path) -> tuple[str, list[Evidence]]:
    found: list[Evidence] = []
    manifest = root / ".emergent" / "emergent.yml"
    if manifest.exists():
        text = manifest.read_text(encoding="utf-8")
        # Named .yml, containing JSON. Parse as JSON; do not trust the suffix.
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
        if data.get("env_image_name"):
            found.append(cite(root, manifest, 1))
    gitconfig = root / ".gitconfig"
    if gitconfig.exists():
        for lineno, line in enumerate(gitconfig.read_text(encoding="utf-8").splitlines(), 1):
            if "github@emergent.sh" in line:
                found.append(cite(root, gitconfig, lineno))
    protocol = root / "test_result.md"
    if protocol.exists() and protocol.read_text(encoding="utf-8").startswith(
        "# START - Testing Protocol"
    ):
        found.append(cite(root, protocol, 1))
    return ("emergent" if found else "unknown"), found


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------

def detect_python_service(root: Path, directory: Path):
    context = rel(root, directory)
    entry = directory / "server.py"
    module = FastAPIModule(entry.read_text(encoding="utf-8"), rel(root, entry))
    evidence: dict[str, list[Evidence]] = {}
    gaps: list[Gap] = []

    requirements = directory / "requirements.txt"
    evidence["build.install_cmd"] = [cite(root, requirements, 1)]

    pinned = unpinned = 0
    for line in requirements.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            if "==" in stripped:
                pinned += 1
            else:
                unpinned += 1
    if pinned or unpinned:
        # #16: requirements.txt pins direct dependencies and says nothing about
        # their transitive ones. The platform froze those inside a prebuilt image;
        # a rebuild resolves them fresh, so the image builds clean and dies on the
        # first import. The spike hit exactly this.
        gaps.append(
            Gap(
                id=gap_id("service", context, "build", "pinning"),
                kind="action",
                pointers=[],
                question=(
                    f"{rel(root, requirements)} pins {pinned} package(s) exactly and leaves "
                    f"{unpinned} open, and says nothing at all about the packages those pull "
                    "in. On the platform those were frozen inside a prebuilt image; "
                    "rebuilding resolves them fresh, so the image can build cleanly and then "
                    "fail on the first import. Freeze the full set before cutover: install "
                    f"{rel(root, requirements)} in a clean virtualenv, run `pip freeze` and "
                    "commit the result."
                ),
                proposed="pip freeze > requirements.lock.txt",
                confidence="high",
                severity="important",
                evidence=[rel(root, requirements)],
            )
        )

    # Where the port and the start command come from, in order of directness.
    #
    # The fixture put `uvicorn.run(app, host=..., port=8001)` in a __main__ block,
    # which made this look solved. A real Emergent app has no such block: it is
    # started by a Procfile or a Dockerfile, and reading server.py tells you
    # nothing. The first real repository this ran against crashed here.
    port = module.listen.get("port")
    start_cmd = None
    if isinstance(port, int):
        ports = [port]
        evidence["ports"] = [cite(root, entry, module.listen["lineno"])]
        start_cmd = f"uvicorn {entry.stem}:{module.app_var} --host 0.0.0.0 --port {port}"
        evidence["build.start_cmd"] = [cite(root, entry, module.listen["lineno"])]
    else:
        ports = []
        for filename, pattern in (("Procfile", r"^\s*web:\s*(.+)$"), ("Dockerfile", r"^CMD\s+(.+)$")):
            candidate = directory / filename
            if not candidate.exists():
                continue
            for lineno, line in enumerate(candidate.read_text(encoding="utf-8").splitlines(), 1):
                found = re.match(pattern, line)
                if not found:
                    continue
                start_cmd = found.group(1).strip().strip('"')
                evidence["build.start_cmd"] = [cite(root, candidate, lineno)]
                break
            if start_cmd:
                break
        dockerfile = directory / "Dockerfile"
        if dockerfile.exists():
            for lineno, line in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), 1):
                found = re.match(r"^EXPOSE\s+(\d+)", line)
                if found:
                    ports = [int(found.group(1))]
                    evidence["ports"] = [cite(root, dockerfile, lineno)]
                    break
    if not ports:
        gaps.append(
            Gap(
                id=gap_id("service", context, "ports"),
                kind="value",
                pointers=[],
                question=(
                    f"Which port does {context} listen on? Nothing in {context}/ says: there "
                    "is no uvicorn.run() call, and no Procfile or Dockerfile EXPOSE to read "
                    "it from. Without it the Deployment has no container port and the "
                    "Service has nothing to route to."
                ),
                proposed=None,
                confidence="low",
                severity="blocking",
                evidence=[],
            )
        )
    if start_cmd is None:
        gaps.append(
            Gap(
                id=gap_id("service", context, "build", "start_cmd"),
                kind="value",
                pointers=[],
                question=(
                    f"What command starts {context}? On the platform this was supplied by "
                    "the runtime, and nothing in the repository records it."
                ),
                proposed=None,
                confidence="low",
                severity="blocking",
                evidence=[],
            )
        )

    # Health: the decorator literal composed with the router prefix and the mount.
    probes: list[Probe] = []
    for route in module.effective_routes():
        if route["method"] != "GET":
            continue
        if route["path"].rsplit("/", 1)[-1] in HEALTH_NAMES:
            cites = [cite(root, entry, route["lineno"])]
            if route["owner"] in module.routers:
                cites.append(cite(root, entry, module.mounts[route["owner"]][1]))
            # #24: liveness and readiness are separate probes. Same endpoint here,
            # different timings: readiness gates traffic and should react quickly;
            # liveness restarts the pod and should not react to a slow query.
            probes = [
                Probe("liveness", route["path"], port, "http", 15, 20),
                Probe("readiness", route["path"], port, "http", 5, 10),
            ]
            evidence["probes"] = cites
            break
    if not probes:
        gaps.append(
            Gap(
                id=gap_id("service", context, "probes"),
                kind="value",
                pointers=[],
                question=(
                    f"What path answers a health check on {context}? No route in the source "
                    "looks like one, so the generated Deployment has no probes: Kubernetes "
                    "will call the pod ready the moment the process starts, and will never "
                    "restart it if it wedges."
                ),
                proposed=None,
                confidence="low",
                severity="important",
                evidence=[],
            )
        )
    for note in module.undecidable:
        gaps.append(
            Gap(
                id=gap_id("service", context, "probes", "undecidable"),
                kind="value",
                pointers=[],
                question=(
                    "The router prefix for this service is computed at runtime, so the real "
                    "URL of the health endpoint cannot be read from the source. What path "
                    "answers a GET health check on the running app?"
                ),
                proposed=None,
                confidence="low",
                severity="blocking",
                evidence=[note],
            )
        )

    env_file = directory / ".env"
    declared = parse_env_file(env_file) if env_file.exists() else []
    declared_map = {key: (value, lineno) for key, value, lineno in declared}
    env: list[EnvVar] = []
    for name in sorted(module.env_reads):
        value, lineno = declared_map.get(name, (None, None))
        sensitive = bool(value is not None and is_sensitive(name, value))
        # #4: source is provenance, sensitive is the security axis. MONGO_URL is
        # both datastore-sourced and a credential; one enum could not say so.
        env.append(
            EnvVar(
                name=name,
                source="datastore" if name in {"MONGO_URL", "DB_NAME"} else "literal",
                binding="runtime",
                sensitive=sensitive,
                value=None if sensitive else value,
            )
        )
        cites = [cite(root, entry, module.env_reads[name])]
        if lineno:
            cites.append(cite(root, env_file, lineno))
        evidence[f"env.{name}"] = cites

    for key, _value, lineno in declared:
        if key in module.env_reads or key.startswith(DEV_ONLY_ENV):
            continue
        gaps.append(
            Gap(
                id=gap_id("service", context, "env", key, "unused"),
                kind="action",
                pointers=[],
                question=(
                    f"{rel(root, env_file)} sets {key}, but no code in {context}/ reads it. "
                    "Should it be carried over to the new deployment, or dropped?"
                ),
                proposed="drop",
                confidence="medium",
                severity="cosmetic",
                evidence=[f"{rel(root, env_file)}:{lineno}"],
            )
        )

    service = Service(
        name=dns_label(directory.name),
        role="api",
        runtime=Runtime(language="python", version=None),
        build=Build(
            context=context,
            dockerfile=None,
            install_cmd="pip install --no-cache-dir -r requirements.txt",
            build_cmd=None,
            start_cmd=start_cmd,
            output_dir=None,
        ),
        ports=ports,
        probes=probes,
        env=env,
        resources=Resources(
            requests={"cpu": "100m", "memory": "256Mi"},
            limits={"cpu": "500m", "memory": "512Mi"},
        ),
        replicas=1,
    )
    return service, gaps, evidence


def detect_node_service(root: Path, directory: Path):
    context = rel(root, directory)
    package_path = directory / "package.json"
    package = json.loads(package_path.read_text(encoding="utf-8"))
    scripts = package.get("scripts", {})
    deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
    evidence: dict[str, list[Evidence]] = {}
    gaps: list[Gap] = []

    lockfiles = {"package-lock.json": "npm", "pnpm-lock.yaml": "pnpm", "yarn.lock": "yarn"}
    found_lock = manager = None
    for name in sorted(lockfiles):
        if (directory / name).exists():
            found_lock, manager = name, lockfiles[name]
            break
    if found_lock:
        install_cmd = "yarn install --frozen-lockfile" if manager == "yarn" else "npm ci"
        evidence["build.install_cmd"] = [cite(root, directory / found_lock, 1)]
    else:
        manager, install_cmd = "yarn", "yarn install"
        evidence["build.install_cmd"] = [cite(root, package_path, 1)]
        gaps.append(
            Gap(
                id=gap_id("service", context, "build", "lockfile"),
                kind="action",
                pointers=[],
                question=(
                    f"No lock file is committed in {context}/, so two builds of the same "
                    "commit can install different dependency versions. Run `yarn install` "
                    "locally and commit the resulting yarn.lock before cutover."
                ),
                proposed="commit yarn.lock",
                confidence="high",
                severity="important",
                evidence=[rel(root, package_path)],
            )
        )

    build_cmd = f"{manager} build" if scripts.get("build") else None
    evidence["build.build_cmd"] = [cite(root, package_path, 1)]

    output_dir = None
    if "react-scripts" in deps:
        output_dir = "build"
    elif "vite" in deps:
        output_dir = "dist"
    if output_dir:
        evidence["build.output_dir"] = [cite(root, package_path, 1)]

    seen: dict[str, tuple[Path, int]] = {}
    for source_file in sorted(directory.rglob("*.js")) + sorted(directory.rglob("*.jsx")):
        if "node_modules" in source_file.parts:
            continue
        text = source_file.read_text(encoding="utf-8")
        for match in re.finditer(r"process\.env\.([A-Z][A-Z0-9_]*)", text):
            name = match.group(1)
            if name not in seen:
                seen[name] = (source_file, text.count("\n", 0, match.start()) + 1)

    env_file = directory / ".env"
    declared = parse_env_file(env_file) if env_file.exists() else []
    declared_map = {key: (value, lineno) for key, value, lineno in declared}
    env: list[EnvVar] = []
    for name in sorted(seen):
        if name.startswith(DEV_ONLY_ENV):
            continue
        source_file, lineno = seen[name]
        binding = "build_arg" if name.startswith(BUILD_ARG_PREFIXES) else "runtime"
        env.append(
            EnvVar(name=name, source="platform", binding=binding, sensitive=False, value=None)
        )
        evidence[f"env.{name}"] = [cite(root, source_file, lineno)]
        if binding == "build_arg":
            cites = [f"{rel(root, source_file)}:{lineno}"]
            old_lineno = declared_map.get(name, (None, None))[1]
            if old_lineno:
                cites.append(f"{rel(root, env_file)}:{old_lineno}")
            gaps.append(
                Gap(
                    id=gap_id("service", context, "env", name, "value"),
                    kind="value",
                    pointers=[],
                    question=(
                        f"What URL should the {context} bundle call the API on? {name} is "
                        "compiled into the JavaScript at build time, not read at runtime, so "
                        "it must be decided before the image is built -- setting it in a "
                        "ConfigMap has no effect. If the frontend and the API are served on "
                        "one hostname (they are, in the Ingress this run generated), the "
                        "answer is the empty string: the bundle then calls /api on whatever "
                        "host served it."
                    ),
                    proposed="",
                    confidence="medium",
                    severity="blocking",
                    evidence=sorted(cites),
                    # #21: the answer depends on how the app is served, which is the
                    # hostname question. Answering this first can be wasted work.
                    depends_on=[gap_id("route", "host")],
                )
            )

    # #3: a service compiled to static assets has no port and no probes in its
    # source. The port a detector can find is the CRA dev server's 3000, which is
    # wrong for the container that ships. Both belong to the renderer that chooses
    # the web server -- and per #22, the renderer filling them means a gap.
    # #3 / #27: a compiled-to-static service has no port and no probes in its
    # source. Revision 1 asked one "serving" question whose answer was a composite
    # -- a server, a port and a probe path -- spanning fields of three different
    # shapes, which the answers model (one value at one pointer) cannot apply. One
    # human decision therefore has to become one gap per field it sets.
    ports: list[int] = []
    probes: list[Probe] = []
    if output_dir:
        gaps.append(
            Gap(
                id=gap_id("service", context, "ports"),
                kind="value",
                pointers=[],
                question=(
                    f"{context} compiles to static files in {output_dir}/ and has no server "
                    "of its own, so offramp generated one: nginx, listening on 8080. The "
                    "port in the source (3000) is the development server's and is not what "
                    "ships. Change this only if something in your cluster expects a "
                    "different port."
                ),
                proposed=[8080],
                confidence="high",
                severity="cosmetic",
                evidence=[rel(root, package_path)],
            )
        )
        gaps.append(
            Gap(
                id=gap_id("service", context, "probes"),
                kind="value",
                pointers=[],
                question=(
                    f"How should Kubernetes check that {context} is healthy? It has no "
                    "health endpoint of its own, so the generated probes ask the web server "
                    "for / and pass as soon as the page is served -- which does not prove "
                    "the app can reach the API. Accept that, or add a health route to the "
                    "app and name it here."
                ),
                proposed=[
                    {
                        "role": "liveness", "path": "/", "port": 8080, "kind": "http",
                        "initial_delay_seconds": 15, "period_seconds": 20,
                    },
                    {
                        "role": "readiness", "path": "/", "port": 8080, "kind": "http",
                        "initial_delay_seconds": 5, "period_seconds": 10,
                    },
                ],
                confidence="high",
                severity="cosmetic",
                evidence=[rel(root, package_path)],
            )
        )

    service = Service(
        name=dns_label(directory.name),
        role="web",
        runtime=Runtime(language="node", version=None),
        build=Build(
            context=context,
            dockerfile=None,
            install_cmd=install_cmd,
            build_cmd=build_cmd,
            start_cmd=None,
            output_dir=output_dir,
        ),
        ports=ports,
        probes=probes,
        env=env,
        resources=Resources(
            requests={"cpu": "25m", "memory": "64Mi"},
            limits={"cpu": "200m", "memory": "128Mi"},
        ),
        replicas=1,
    )
    return service, gaps, evidence


# ---------------------------------------------------------------------------
# Datastore
# ---------------------------------------------------------------------------

MONGO_PACKAGES = ("motor", "pymongo")


def detect_datastores(root: Path, services: list[Service]):
    datastores: list[Datastore] = []
    evidence: dict[str, list[Evidence]] = {}
    for service in sorted(services, key=lambda s: s.name):
        requirements = root / service.build.context / "requirements.txt"
        if not requirements.exists():
            continue
        hit = None
        for lineno, line in enumerate(requirements.read_text(encoding="utf-8").splitlines(), 1):
            package = re.split(r"[=<>!\[ ]", line.strip(), maxsplit=1)[0].lower()
            if package in MONGO_PACKAGES:
                hit = cite(root, requirements, lineno)
                break
        if hit is None:
            continue
        # #25: a datastore needs identity of its own. Keying on kind alone collides
        # for two instances of the same engine and gives the Secret name no source.
        datastores.append(
            Datastore(
                name="mongodb",
                kind="mongodb",
                version=None,
                mode=None,              # never defaulted (RFC-0001)
                consumed_by=[service.name],
                env_keys=sorted(var.name for var in service.env if var.source == "datastore"),
            )
        )
        evidence["datastore.mongodb"] = [hit]
    return datastores, evidence


# ---------------------------------------------------------------------------
# Committed credentials
# ---------------------------------------------------------------------------

def detect_committed_credentials(root: Path) -> list[Gap]:
    """AGENTS.md §4: name the key and the file, say rotate before cutover, never the value."""
    gaps: list[Gap] = []
    for env_file in sorted(root.rglob(".env")):
        if "node_modules" in env_file.parts:
            continue
        for key, value, lineno in parse_env_file(env_file):
            if not is_sensitive(key, value):
                continue
            location = rel(root, env_file)
            gaps.append(
                Gap(
                    id=gap_id("secret", *location.replace(".env", "env").split("/"), key),
                    # #20: not a value to supply. Nothing goes in the answers file;
                    # the user does a thing and says they did it.
                    kind="action",
                    pointers=[],
                    question=(
                        f"{location} has a working credential committed to the repository in "
                        f"{key}. Rotate it before cutover: anyone with repository access, and "
                        "anyone who has ever had it, holds the current value. The generated "
                        "manifests read this key from a Kubernetes Secret and never contain "
                        "the value, so rotating costs you one `kubectl create secret` and "
                        "nothing else."
                    ),
                    proposed=None,
                    confidence="high",
                    severity="blocking",
                    evidence=[f"{location}:{lineno}"],
                )
            )
    return gaps


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def common_path_prefix(paths: list[str]) -> str:
    if not paths:
        return "/"
    segments = [path.strip("/").split("/") for path in paths]
    shared: list[str] = []
    for parts in zip(*segments):
        if len(set(parts)) != 1 or parts[0].startswith("{"):
            break
        shared.append(parts[0])
    return "/" + "/".join(shared) if shared else "/"


def detect_routes(root: Path, services: list[Service]):
    routes: list[Route] = []
    evidence: dict[str, list[Evidence]] = {}
    for service in services:
        entry = root / service.build.context / "server.py"
        if entry.exists():
            module = FastAPIModule(entry.read_text(encoding="utf-8"), rel(root, entry))
            prefix = common_path_prefix([route["path"] for route in module.effective_routes()])
            routes.append(
                Route(
                    host=None,
                    path=prefix,
                    service=service.name,
                    port=service.ports[0] if service.ports else None,
                    tls=False,
                )
            )
            evidence[f"route.{prefix}"] = [
                cite(root, entry, module.mounts[owner][1]) for owner in sorted(module.mounts)
            ]
        elif service.role == "web":
            # #3: port None -> the renderer wires the Ingress to the Service's
            # named port, so nothing here has to know a number it cannot detect.
            routes.append(Route(host=None, path="/", service=service.name, port=None, tls=False))
            evidence["route./"] = [
                cite(root, root / service.build.context / "package.json", 1)
            ]
    routes.sort(key=lambda r: (r.path, r.service))
    gaps = [
        Gap(
            id=gap_id("route", "host"),
            kind="value",
            pointers=[],
            question=(
                "What hostname will the app be served on after the move? Every route this "
                "run found is host-less, so the generated Ingress matches any hostname that "
                "reaches the cluster, and no TLS certificate can be issued until a name "
                "exists. Answer with the DNS name you will point at the new ingress."
            ),
            proposed=None,
            confidence="high",
            severity="blocking",
            evidence=sorted({f"{e.path}:{e.line}" for v in evidence.values() for e in v}),
        )
    ]
    return routes, gaps, evidence


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def pointers_for(identifier: str, appspec) -> list[str]:
    """Recompute JSON pointers for a gap id against this run's AppSpec.

    #8: this returns a list. Some gaps have no pointer -- the credential value must
    never enter the AppSpec, and the unused .env key is deliberately absent from it
    -- and route.host is one question whose answer sets a field on every route.
    RFC-0001's single `pointer` can express neither. Building these is also more
    work than "a per-run addressing convenience" suggests.
    """
    parts = identifier.split(".")
    if parts[0] == "service":
        context = parts[1]
        index = next(
            (i for i, s in enumerate(appspec.services) if s.build.context == context), None
        )
        if index is None:
            return []
        tail = parts[2:]
        if tail[:1] == ["probes"]:
            return [f"/services/{index}/probes"]
        if tail[:1] == ["ports"]:
            return [f"/services/{index}/ports"]
        if tail[:2] == ["build", "start_cmd"]:
            return [f"/services/{index}/build/start_cmd"]
        if tail[:2] == ["build", "lockfile"]:
            return [f"/services/{index}/build/install_cmd"]
        if tail[:2] == ["build", "pinning"]:
            return []                      # an action, not a field
        if tail[0] == "env" and tail[-1] == "value":
            name = tail[1]
            env_index = next(
                (j for j, v in enumerate(appspec.services[index].env) if v.name == name), None
            )
            return [] if env_index is None else [f"/services/{index}/env/{env_index}/value"]
        if tail[0] == "env" and tail[-1] == "unused":
            return []                      # the key is not in the AppSpec at all
        if tail[:2] == ["runtime", "version"]:
            return [f"/services/{index}/runtime/version"]
        if tail[:1] == ["resources"]:
            return [f"/services/{index}/resources"]
        if tail[:1] == ["replicas"]:
            return [f"/services/{index}/replicas"]
        return [f"/services/{index}"]
    if parts[0] == "datastore":
        index = next((i for i, d in enumerate(appspec.datastores) if d.name == parts[1]), None)
        return [] if index is None else [f"/datastores/{index}/{parts[2]}"]
    if parts[0] == "route":
        return [f"/routes/{i}/{parts[1]}" for i in range(len(appspec.routes))]
    if parts[0] == "environment":
        index = next((i for i, e in enumerate(appspec.environments) if e.name == parts[1]), None)
        return [] if index is None else [f"/environments/{index}/{parts[2]}"]
    if parts[0] == "delivery":
        return [f"/delivery/{'.'.join(parts[1:])}"]
    if parts[0] == "app":
        return ["/name"]
    return []                              # secrets: never in the AppSpec, by design


# ---------------------------------------------------------------------------
# Spec-level gaps. Rule #22 applies throughout: if the tool will put a value in
# the output that it did not detect, there is a gap for it here.
#
# This is a function, and it runs *after* answers are merged, so that a gap whose
# proposal is derived from an answered value proposes the right thing and a gap
# that an answer made moot is never asked. See #21.
# ---------------------------------------------------------------------------
def spec_level_gaps(appspec, datastore_evidence) -> list[Gap]:
    out: list[Gap] = []
    name = appspec.name
    services = appspec.services
    datastores = appspec.datastores
    environments = appspec.environments
    if name is None:
        out.append(
            Gap(
                id=gap_id("app", "name"),
                kind="value",
                pointers=[],
                question=(
                    "What is this application called? The name goes in every label, the "
                    "namespace, the Ingress, the ArgoCD application and the Secret the API "
                    "reads its database URL from. Nothing in an Emergent repository states "
                    "one and this checkout has no git remote to take it from. It is not "
                    "guessed from the directory name: that would make the generated files "
                    "depend on what you happened to call your clone. Lowercase letters, "
                    "digits and dashes."
                ),
                proposed=None,
                confidence="high",
                severity="blocking",
                evidence=[],
            )
        )
    for service in services:
        proposed = PROPOSED_PYTHON if service.runtime.language == "python" else PROPOSED_NODE
        out.append(
            Gap(
                id=gap_id("service", service.build.context, "runtime", "version"),
                kind="value",
                pointers=[],
                question=(
                    f"Which {service.runtime.language} version does {service.name} need? "
                    f"Nothing in {service.build.context}/ pins one, so the generated "
                    f"Dockerfile uses {proposed}. A different minor version is usually fine; "
                    "a different major one is where things break quietly."
                ),
                proposed=proposed,
                confidence="low",
                severity="important",
                evidence=[],
            )
        )
        out.append(
            Gap(
                id=gap_id("service", service.build.context, "resources"),
                kind="value",
                pointers=[],
                question=(
                    f"How much CPU and memory does {service.name} need? The generated values "
                    "are placeholders, not measurements. Too low and the pod is OOM-killed "
                    "under load; too high and you pay for idle capacity. If you do not know, "
                    "deploy with these, watch for a week, and set them from what you see."
                ),
                proposed={
                    "requests": service.resources.requests,
                    "limits": service.resources.limits,
                },
                confidence="low",
                severity="important",
                evidence=[],
            )
        )
        out.append(
            Gap(
                id=gap_id("service", service.build.context, "replicas"),
                kind="value",
                pointers=[],
                question=(
                    f"How many copies of {service.name} should run? The generated value is 1, "
                    "which means every deploy and every node drain is a brief outage. Two is "
                    "the usual answer for anything serving traffic; a worker or a cron job is "
                    "usually fine at one."
                ),
                proposed=1,
                confidence="low",
                severity="cosmetic",
                evidence=[],
            )
        )
    for datastore in datastores:
        out.append(
            Gap(
                id=gap_id("datastore", datastore.name, "mode"),
                kind="value",
                pointers=[],
                question=(
                    f"How should {datastore.kind} run after the move? This is a cost, "
                    "operations and risk decision that nothing in the repository can answer, "
                    "so no database manifest was generated for any option.\n"
                    "  in_cluster -- a StatefulSet next to the app. Cheapest; you own "
                    "backups, upgrades and restores.\n"
                    "  managed    -- Atlas or a cloud provider's service. Costs money; "
                    "backups and failover are theirs.\n"
                    "  external   -- a database your team already runs. Free if it exists; "
                    "you supply the connection string.\n"
                    "Whichever you pick, the application manifests do not change: they read "
                    f"the connection string from a Secret named '<app>-{datastore.name}'. "
                    "Until it exists the pod fails with 'secret not found', which is the "
                    "message you want, not a DNS timeout."
                ),
                proposed=None,
                confidence="high",
                severity="blocking",
                evidence=[
                    f"{e.path}:{e.line}"
                    for e in datastore_evidence.get(f"datastore.{datastore.name}", [])
                ],
            )
        )
        out.append(
            Gap(
                id=gap_id("datastore", datastore.name, "version"),
                kind="value",
                pointers=[],
                question=(
                    f"Which {datastore.kind} version? The driver in requirements.txt works "
                    "with 5.0 and later."
                ),
                proposed=None,
                confidence="medium",
                severity="important",
                evidence=[],
                # #21: moot unless mode is answered in_cluster -- a managed or
                # external database already has a version.
                depends_on=[gap_id("datastore", datastore.name, "mode")],
            )
        )
    out.append(
        Gap(
            id=gap_id("delivery", "image_registry"),
            kind="value",
            pointers=[],
            question=(
                "Which container registry will hold the built images? The Kustomize overlay "
                "sets the image for each service and cannot be applied until this is a real "
                "registry path, e.g. ghcr.io/your-org."
            ),
            proposed=None,
            confidence="high",
            severity="blocking",
            evidence=[],
        )
    )
    out.append(
        Gap(
            id=gap_id("delivery", "image_tag"),
            kind="value",
            pointers=[],
            question=(
                "What should the image tag be? It has to be immutable -- a commit SHA is the "
                "usual answer. A moving tag like 'production' or 'latest' means a rollback "
                "has nothing to roll back to, and, because the generated Deployment uses "
                "imagePullPolicy: IfNotPresent, a node that already cached that tag will go "
                "on running the old image with no error anywhere.\n"
                "You do not answer this once. Your build pipeline answers it every time it "
                "pushes an image, by running:\n"
                "  scan  ->  a one-entry plan setting delivery.image_tag  ->  apply "
                "--accepted-by ci  ->  scan  ->  render  ->  commit\n"
                "That keeps the output tree a pure function of the AppSpec, so `verify` "
                "stays green. Editing the tag directly in k8s/overlays/*/kustomization.yaml "
                "does not: verify reports it as a renderer bug, correctly, because it cannot "
                "tell your build apart from something that edited output it should not have. "
                "Set a placeholder here for the first render; the pipeline overwrites it."
            ),
            proposed="unbuilt",
            confidence="medium",
            severity="important",
            evidence=[],
        )
    )
    out.append(
        Gap(
            id=gap_id("delivery", "gitops_repo_url"),
            kind="value",
            pointers=[],
            question=(
                "Which git repository will ArgoCD watch for these manifests? This is the "
                "repository you commit the generated tree to, which may or may not be the "
                "application repository. ArgoCD needs read access to it."
            ),
            proposed=None,
            confidence="high",
            severity="blocking",
            evidence=[],
        )
    )
    out.append(
        Gap(
            id=gap_id("delivery", "ingress_class"),
            kind="value",
            pointers=[],
            question=(
                "Which ingress controller serves this cluster? The generated Ingress names "
                "no IngressClass, so the cluster's default is used -- and if the cluster has "
                "no default, nothing picks the Ingress up and the app is simply unreachable "
                "with no error anywhere. Common answers are 'nginx' and 'traefik'; "
                "`kubectl get ingressclass` tells you what is installed."
            ),
            proposed=None,
            confidence="high",
            severity="important",
            evidence=[],
        )
    )
    out.append(
        Gap(
            id=gap_id("environment", "production", "namespace"),
            kind="value",
            pointers=[],
            question=(
                "Which namespace should production deploy into? The generated value is the "
                "app name with '-production' appended. Change it if your cluster has a "
                "naming convention."
            ),
            proposed=environments[0].namespace,
            confidence="high",
            severity="cosmetic",
            evidence=[],
            depends_on=[gap_id("app", "name")] if name is None else [],
        )
    )

    return out


def is_moot(gap: Gap, appspec) -> bool:
    """#21: a gap another answer has made pointless is not asked.

    RFC-0001's gap list is flat, so it would go on asking which MongoDB version to
    deploy after the user has said the database is managed by someone else.
    """
    parts = gap.id.split(".")
    if parts[0] == "datastore" and parts[-1] == "version":
        datastore = next((d for d in appspec.datastores if d.name == parts[1]), None)
        return datastore is not None and datastore.mode in {"managed", "external"}
    return False


# Never answerable, by invariant rather than by omission. RFC-0001: "AppSpec.source
# in its entirety, and any field used to construct a stable id -- Service.name
# above all -- are never answerable by any detector. A detector that declares one
# is a bug, and the schema check rejects it."
NEVER_ANSWERABLE = ("/source", "/services/*/name")


def answerable_set(gaps: list[Gap], appspec=None, answers=None) -> tuple[dict, list[str]]:
    """The declared address space, and any detector bug found while declaring it.

    RFC-0001 says the answerable set "is declared, not inferred" and never says
    where the declaration lives. It lives here, and scan writes it out, so `apply`
    has something to check a plan's target against.
    """
    declared, problems = {}, []

    # #32: an already-answered question must stay in the address space, or an answer
    # can never be revised. RFC-0001 says a plan may only target ids "a detector or
    # gap declares answerable" -- and an answered gap is no longer emitted, so its id
    # leaves the set and the only way to change your mind is to hand-edit the file
    # the whole mechanism exists to keep hand-editing out of.
    if appspec is not None and answers is not None:
        for answer in answers.answers:
            if subject_exists(answer.target, appspec):
                declared[answer.target] = {
                    "kind": answer.kind,
                    "pointers": [] if answer.kind == "action" else pointers_for(
                        answer.target, appspec
                    ),
                    "severity": "answered",
                    "evidence_scope": evidence_scope_for(answer.target, appspec),
                }

    for gap in sorted(gaps, key=lambda g: g.id):
        for pointer in gap.pointers:
            if pointer.startswith("/source") or re.fullmatch(r"/services/\d+/name", pointer):
                problems.append(
                    f"{gap.id} declares {pointer} answerable, which the invariant forbids"
                )
        declared[gap.id] = {
            "kind": gap.kind,
            "pointers": gap.pointers,
            "severity": gap.severity,
            "evidence_scope": gap.evidence_scope,
        }
    return declared, problems


def _pointer_in_json(data, pointer: str):
    current = data
    for token in pointer.strip("/").split("/"):
        if isinstance(current, list):
            index = int(token)
            if index >= len(current):
                return None
            current = current[index]
        elif isinstance(current, dict):
            if token not in current:
                return None
            current = current[token]
        else:
            return None
    return current


def evidence_scope_for(identifier: str, appspec) -> list[str]:
    """#34: where a citation is load-bearing for this question.

    A question about a service is answered from that service's tree. A question the
    repository cannot speak to -- which registry, which hostname, what to call the
    app -- has no load-bearing evidence anywhere, which is not the same as saying a
    plan may not cite anything: it may, and a reviewer should see it, but a CSS
    refactor must not re-ask what the application is called.
    """
    parts = identifier.split(".")
    if parts[0] == "service":
        return [f"{parts[1]}/"]
    if parts[0] == "secret":
        return [f"{parts[1]}/"]
    if parts[0] == "datastore":
        datastore = next((d for d in appspec.datastores if d.name == parts[1]), None)
        if datastore is None:
            return []
        contexts = {
            s.build.context
            for s in appspec.services
            if s.name in datastore.consumed_by
        }
        return sorted(f"{context}/" for context in contexts)
    return []


def in_scope(path: str, scope: list[str]) -> bool:
    return any(path.startswith(prefix) for prefix in scope)


def subject_exists(identifier: str, appspec) -> bool:
    """Does the thing a stored answer was about still exist in this run?

    #30: RFC-0001 separates a *stale* answer (the detector now disagrees) from an
    *orphaned* one (the target is gone), and they need different handling -- one is
    a question to re-confirm, the other is information about something that
    vanished. Telling them apart cannot key on whether a gap still exists, because
    a gap disappearing is also what *success* looks like: when a detector graduates
    and starts producing a value, its gap goes away. RFC-0001 calls that case out
    by name as something the design makes work. Keying on the gap list turns it
    into an orphan report.

    So the question is asked of the AppSpec, not of the gap list.
    """
    parts = identifier.split(".")
    if parts[0] == "service":
        return any(s.build.context == parts[1] for s in appspec.services)
    if parts[0] == "datastore":
        return any(d.name == parts[1] for d in appspec.datastores)
    if parts[0] == "environment":
        return any(e.name == parts[1] for e in appspec.environments)
    if parts[0] == "secret":
        # secret.<path segments>.<KEY>; the first segment is the service directory.
        return any(s.build.context == parts[1] for s in appspec.services)
    if parts[0] in {"app", "delivery", "route"}:
        return True
    return False


def scan_repo(root: Path, answers=None):
    from answers import merge
    from spec import AppSpec

    root = root.resolve()
    gaps: list[Gap] = []
    evidence: dict[str, list[Evidence]] = {}

    platform, platform_evidence = detect_platform(root)
    if platform_evidence:
        evidence["source.platform"] = platform_evidence
    name, repo_url, commit = detect_identity(root)

    services: list[Service] = []
    skipped: list[tuple[str, str]] = []
    for directory in sorted(path for path in root.iterdir() if path.is_dir()):
        if directory.name.startswith(".") or directory.name == "node_modules":
            continue
        if (directory / "server.py").exists() and (directory / "requirements.txt").exists():
            service, service_gaps, service_evidence = detect_python_service(root, directory)
        elif (directory / "package.json").exists():
            service, service_gaps, service_evidence = detect_node_service(root, directory)
        else:
            # Say what was passed over and why. A scan that quietly finds nothing
            # looks exactly like a scan that worked, which is the worst way for a
            # detector's assumptions to be wrong -- and the assumptions here are
            # narrow: v1 is one scenario, and the entrypoint filename is one string.
            if (directory / "requirements.txt").exists():
                reason = "has requirements.txt but no server.py (the only Python entrypoint looked for)"
            elif any(directory.glob("*.py")):
                reason = "has Python sources but neither server.py nor requirements.txt"
            elif any(directory.glob("*.ts")) or any(directory.glob("*.tsx")):
                reason = "looks like TypeScript but has no package.json at its root"
            else:
                reason = "nothing recognisable at its root"
            skipped.append((rel(root, directory), reason))
            continue
        services.append(service)
        gaps.extend(service_gaps)
        for key, value in service_evidence.items():
            evidence[f"service.{service.name}.{key}"] = value
    services.sort(key=lambda s: s.name)

    datastores, datastore_evidence = detect_datastores(root, services)
    evidence.update(datastore_evidence)
    routes, route_gaps, route_evidence = detect_routes(root, services)
    gaps.extend(route_gaps)
    evidence.update(route_evidence)
    gaps.extend(detect_committed_credentials(root))

    environments = [
        Environment(
            name="production",
            replicas=None,
            resources=None,
            namespace=f"{name}-production" if name else None,
        )
    ]
    appspec = AppSpec(
        name=name,
        source=Source(platform=platform, repo_url=repo_url, commit=commit),
        services=services,
        datastores=datastores,
        routes=routes,
        environments=environments,
        delivery=Delivery(
            image_registry=None,
            image_tag=None,
            gitops_repo_url=None,
            ingress_class=None,
            target_cluster="https://kubernetes.default.svc",
        ),
    )

    # First pass: the merge needs pointers to resolve an answer's target against.
    detector_gaps = list(gaps)
    first_pass = spec_level_gaps(appspec, datastore_evidence)
    for gap in detector_gaps + first_pass:
        gap.pointers = [] if gap.kind == "action" else pointers_for(gap.id, appspec)

    # #33: snapshot the *bare* detector values before anything is merged.
    # `Answer.detected` means "what the detector produced when this was answered",
    # and after a merge the AppSpec holds previously-answered values instead. Filling
    # `detected` from the merged spec records an answer as though a detector had
    # produced it, and every later scan then reports the real detector -- which
    # still produces nothing -- as disagreeing. Revising an answer is enough to
    # trigger it. `current` (compare-and-swap) and `detected` look like the same
    # value and are not: one is what the human reviewed, the other is what the
    # detector said.
    from spec import to_jsonable as _to_jsonable

    bare = _to_jsonable(appspec)
    detected = {}
    for gap in detector_gaps + first_pass:
        if gap.pointers:
            detected[gap.id] = _pointer_in_json(bare, gap.pointers[0])

    merged = merge(
        root, appspec, detector_gaps + first_pass, answers,
        resolve_pointers=pointers_for, subject_exists=subject_exists,
    )
    if merged.applied:
        # #28: merging writes JSON values into a typed tree. Round-trip through the
        # one deserialiser so everything downstream sees one representation.
        from spec import from_jsonable, to_jsonable

        appspec = from_jsonable(to_jsonable(appspec))

    # Second pass, with the answered values in place, so that a gap deriving its
    # proposal from an answered field proposes the right thing.
    # A stale answer is re-emitted as a gap carrying the original question; the
    # original must not also be emitted, or the same question is asked twice in one
    # report.
    superseded = {gap.id for gap in merged.gaps}
    gaps = [
        gap
        for gap in detector_gaps + spec_level_gaps(appspec, datastore_evidence)
        if gap.id not in merged.applied
        and gap.id not in superseded
        and not is_moot(gap, appspec)
    ] + merged.gaps
    for gap in gaps:
        # #20: an action gap addresses no field. Revision 1 gave two of them a
        # nearest-field pointer, which made compare-and-swap reject an
        # acknowledgement against a value nobody proposed to change.
        gap.pointers = [] if gap.kind == "action" else pointers_for(gap.id, appspec)
        gap.evidence_scope = evidence_scope_for(gap.id, appspec)

    # Bare values for every answerable id, including ones an answer has since
    # filled in -- apply needs them and cannot recompute them from a merged scan.
    for gap in gaps:
        if gap.pointers and gap.id not in detected:
            detected[gap.id] = _pointer_in_json(bare, gap.pointers[0])

    severity_rank = {"blocking": 0, "important": 1, "cosmetic": 2}
    gaps.sort(key=lambda g: (severity_rank[g.severity], g.id))
    return (
        appspec,
        gaps,
        {key: sorted(value, key=lambda e: (e.path, e.line))
         for key, value in sorted(evidence.items())},
        dict(sorted(detected.items())),
        sorted(merged.redundant, key=lambda r: r["target"]),
        sorted(skipped),
    )
