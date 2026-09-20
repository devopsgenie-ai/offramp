"""The Python service: what it is built from and how it starts.

Routes, probes and environment variables are separate detectors over the same tree.
This one answers only "what is this service, and what does building it require".

Rule this module obeys without exception: if the tool will put a value in the output
that it did not detect, it emits a gap. There is no allowance for an "obvious" default
-- the line between an obvious default and a guess was left to each contributor's
judgement in the spike, and that makes the headline gap-count metric drift for reasons
unrelated to detection.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from spec import Build, Evidence, Gap, Resources, Runtime, Service, gap_id
from walk import dns_label, rel, walk_files

PROPOSED_PYTHON = "3.11"
ENTRY_MODULES = ("server.py", "main.py", "app.py", "asgi.py", "wsgi.py")
EXPOSE_RE = re.compile(r"^EXPOSE\s+(\d+)", re.MULTILINE)
PINNED = re.compile(r"^[A-Za-z0-9._-]+(\[[^\]]+\])?==")
DEFAULT_RESOURCES = Resources(
    requests={"cpu": "100m", "memory": "256Mi"},
    limits={"cpu": "500m", "memory": "512Mi"},
)


def find_python_services(root: Path) -> list[Path]:
    """Directories holding a requirements.txt. Sorted, so the order is the tree's."""
    return sorted({path.parent for path in walk_files(root, suffixes=(".txt",))
                   if path.name == "requirements.txt"})


def _requirement_names(text: str) -> tuple[list[str], list[str]]:
    """Return (all names, unpinned names) in file order."""
    names: list[str] = []
    unpinned: list[str] = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped or stripped.startswith("-"):
            continue
        name = re.split(r"[=<>!~\[]", stripped, maxsplit=1)[0].strip()
        if not name:
            continue
        names.append(name)
        if not PINNED.match(stripped):
            unpinned.append(name)
    return names, unpinned


def _declared_version(directory: Path) -> tuple[str | None, str | None]:
    """(version, filename) from the usual places, in a fixed order."""
    for filename in (".python-version", "runtime.txt"):
        path = directory / filename
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            cleaned = text.removeprefix("python-").strip()
            if cleaned:
                return cleaned, filename
    pyproject = directory / "pyproject.toml"
    if pyproject.is_file():
        match = re.search(
            r'requires-python\s*=\s*"[^0-9]*([0-9]+\.[0-9]+)',
            pyproject.read_text(encoding="utf-8"),
        )
        if match:
            return match.group(1), "pyproject.toml"
    return None, None


def _kwarg_value(call: ast.Call, name: str):
    for keyword in call.keywords:
        if keyword.arg == name and isinstance(keyword.value, ast.Constant):
            return keyword.value.value
    return None


def _listen_port(directory: Path) -> tuple[int | None, str | None]:
    """(port, filename) from a literal uvicorn.run(port=...) or a Dockerfile EXPOSE.

    Only literals count. `port=int(os.environ["PORT"])` is not a port this repository
    states, and reporting one would be the invention rule's exact failure.
    """
    for filename in ENTRY_MODULES:
        path = directory / filename
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", None) != "run":
                continue
            value = _kwarg_value(node, "port")
            if isinstance(value, int):
                return value, filename
    dockerfile = directory / "Dockerfile"
    if dockerfile.is_file():
        match = EXPOSE_RE.search(dockerfile.read_text(encoding="utf-8"))
        if match:
            return int(match.group(1)), "Dockerfile"
    return None, None


def _entrypoint(directory: Path) -> tuple[str | None, str | None]:
    """(module stem, ASGI app variable) from the first entry module that defines one."""
    for filename in ENTRY_MODULES:
        path = directory / filename
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            func = node.value.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in ("FastAPI", "Flask") and isinstance(node.targets[0], ast.Name):
                return path.stem, node.targets[0].id
    return None, None


def detect_python_service(
    root: Path, directory: Path
) -> tuple[Service, list[Gap], list[Evidence]]:
    name = dns_label(directory.name)
    context = rel(root, directory)
    gaps: list[Gap] = []
    evidence: list[Evidence] = []

    requirements = directory / "requirements.txt"
    requirements_rel = rel(root, requirements)
    names, unpinned = _requirement_names(requirements.read_text(encoding="utf-8"))
    evidence.append(Evidence(path=requirements_rel, line=1))

    version, version_file = _declared_version(directory)
    if version_file:
        evidence.append(Evidence(path=rel(root, directory / version_file), line=1))
    else:
        gaps.append(Gap(
            id=gap_id("service", name, "runtime", "version"),
            kind="value",
            pointers=[f"/services/{name}/runtime/version"],
            question=(
                f"Which Python version does `{context}` run on? Nothing in the service "
                f"declares one -- no .python-version, runtime.txt or requires-python -- "
                f"so the image tag would otherwise be a guess."
            ),
            proposed=PROPOSED_PYTHON,
            confidence="medium",
            severity="important",
            evidence=[requirements_rel],
            evidence_scope=[context],
        ))

    port, port_source = _listen_port(directory)
    if port_source:
        evidence.append(Evidence(path=rel(root, directory / port_source), line=1))

    module, app_var = _entrypoint(directory)
    start_cmd = None
    if module and app_var:
        start_cmd = f"uvicorn {module}:{app_var} --host 0.0.0.0"
        if port is not None:
            start_cmd += f" --port {port}"
    else:
        gaps.append(Gap(
            id=gap_id("service", name, "build", "start_cmd"),
            kind="value",
            pointers=[f"/services/{name}/build/start_cmd"],
            question=(
                f"How is `{context}` started? No module among "
                f"{', '.join(ENTRY_MODULES)} assigns a FastAPI or Flask application."
            ),
            proposed=None,
            confidence="low",
            severity="blocking",
            evidence=[requirements_rel],
            evidence_scope=[context],
        ))

    if port is None:
        gaps.append(Gap(
            id=gap_id("service", name, "ports"),
            kind="value",
            pointers=[f"/services/{name}/ports"],
            question=(
                f"Which port does `{context}` listen on? Nothing in the repository binds "
                f"one -- no literal `uvicorn.run(port=...)` and no Dockerfile EXPOSE -- so "
                f"the generated Service, the probes and the Ingress backend would all be "
                f"guessing together, and they would have to guess the same thing."
            ),
            proposed=[8000],
            confidence="medium",
            severity="blocking",
            evidence=[requirements_rel],
            evidence_scope=[context],
        ))

    if unpinned:
        gaps.append(Gap(
            id=gap_id("service", name, "dependencies", "pinned"),
            kind="action",
            pointers=[],
            question=(
                f"Pin the transitive dependency set for `{context}` and commit the "
                f"result. {len(unpinned)} of {len(names)} requirements are unpinned "
                f"({', '.join(unpinned)}), so two builds of the same commit can install "
                f"different code. Generate a lock file (pip-compile, uv pip compile or "
                f"`pip freeze`) and commit it, then confirm here."
            ),
            proposed=None,
            confidence="high",
            severity="important",
            evidence=[requirements_rel],
            evidence_scope=[context],
        ))

    dockerfile = directory / "Dockerfile"
    service = Service(
        name=name,
        role="api",
        runtime=Runtime(language="python", version=version),
        build=Build(
            context=context,
            dockerfile=rel(root, dockerfile) if dockerfile.is_file() else None,
            install_cmd="pip install -r requirements.txt",
            build_cmd=None,
            start_cmd=start_cmd,
            output_dir=None,
        ),
        ports=[port] if port is not None else [],
        probes=[],
        env=[],
        resources=DEFAULT_RESOURCES,
        replicas=1,
    )
    evidence.sort(key=lambda item: (item.path, item.line))
    return service, gaps, evidence
