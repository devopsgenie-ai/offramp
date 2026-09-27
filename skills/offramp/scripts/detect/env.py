"""Environment variables: which ones the application reads, and how they are delivered.

Two axes, deliberately separate. `binding` says when the value is substituted --
`runtime` into the container's environment, `build_arg` into a bundle at image build
time. `sensitive` says whether the value is a credential. They are independent, and
collapsing them loses the case that matters: MONGO_URL is both a datastore fact and a
credential.

A build arg is compiled into a file served to every browser, so it is public by
construction and is never marked sensitive. When one *is* a real secret the gap says
"this is already public", not "rotate before cutover" -- rotating a value that is baked
into a shipped bundle does not make the old bundle safe.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from spec import Evidence, EnvVar, Gap, gap_id
from walk import rel, walk_files

BUILD_ARG_PREFIXES = ("REACT_APP_", "VITE_", "NEXT_PUBLIC_")
DEV_ONLY_ENV = ("WDS_", "FAST_REFRESH", "CHOKIDAR_", "BROWSER", "GENERATE_SOURCEMAP")
SECRET_NAME_RE = re.compile(r"(PASSWORD|PASSWD|SECRET|TOKEN|API_?KEY|CREDENTIAL|PRIVATE)")
CREDENTIAL_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@")
# `process.env.X` for CRA and Node; `import.meta.env.X` for Vite, which is the modal
# Lovable frontend (RFC-0002).
_JS_ENV_RE = re.compile(r"(?:process|import\.meta)\.env\.([A-Z_][A-Z0-9_]*)")
_JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs")


def parse_env_file(path: Path) -> list[tuple[str, str, int]]:
    """(key, value, 1-based line) for each assignment. Comments and blanks skipped."""
    found: list[tuple[str, str, int]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip().removeprefix("export ").strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        found.append((key.strip(), value.strip().strip("'\""), number))
    return found


def is_sensitive(key: str, value: str) -> bool:
    """A credential by name, or a URL carrying a password."""
    return bool(SECRET_NAME_RE.search(key.upper())) or bool(CREDENTIAL_URL_RE.match(value))


def _is_dev_only(key: str) -> bool:
    return any(key.startswith(prefix) or key == prefix for prefix in DEV_ONLY_ENV)


def _binding(key: str) -> str:
    return "build_arg" if key.startswith(BUILD_ARG_PREFIXES) else "runtime"


def _python_reads(text: str) -> list[tuple[str, "str | None", int]]:
    """(key, literal default or None, line) for os.environ / os.getenv reads."""
    found: list[tuple[str, "str | None", int]] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        key = default = None
        if isinstance(node, ast.Subscript):
            value, index = node.value, node.slice
            if (isinstance(value, ast.Attribute) and value.attr == "environ"
                    and isinstance(index, ast.Constant) and isinstance(index.value, str)):
                key = index.value
        elif isinstance(node, ast.Call):
            func = node.func
            attr = getattr(func, "attr", None)
            is_environ_get = attr == "get" and getattr(
                getattr(func, "value", None), "attr", None) == "environ"
            if (attr == "getenv" or is_environ_get) and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    key = first.value
                    if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                        default = node.args[1].value
        if key:
            found.append((key, None if default is None else str(default), node.lineno))
    return found


def detect_env(
    root: Path, directory: Path, service_name: str
) -> tuple[list[EnvVar], list[Gap], list[Evidence]]:
    reads: dict[str, tuple["str | None", str, int]] = {}
    committed: dict[str, tuple[str, str, int]] = {}

    for path in walk_files(directory):
        relative = rel(root, path)
        if path.suffix == ".py":
            for key, default, line in _python_reads(path.read_text(encoding="utf-8")):
                if key not in reads or (reads[key][0] is None and default is not None):
                    reads[key] = (default, relative, line)
        elif path.suffix in _JS_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="ignore")
            for number, line_text in enumerate(text.splitlines(), start=1):
                for key in _JS_ENV_RE.findall(line_text):
                    reads.setdefault(key, (None, relative, number))
        elif path.name == ".env" or path.name.startswith(".env."):
            for key, value, line in parse_env_file(path):
                committed.setdefault(key, (value, relative, line))

    env: list[EnvVar] = []
    gaps: list[Gap] = []
    evidence: list[Evidence] = []

    for key in sorted(reads):
        if _is_dev_only(key):
            continue
        default, read_path, read_line = reads[key]
        committed_value, committed_path, committed_line = committed.get(
            key, (None, None, None))
        binding = _binding(key)
        raw_value = committed_value if committed_value is not None else default
        sensitive = raw_value is not None and is_sensitive(key, raw_value)

        if binding == "build_arg" and sensitive:
            # Public by construction. Never flag it sensitive; say what is true instead.
            gaps.append(Gap(
                id=gap_id("service", service_name, "env", key, "public"),
                kind="action",
                pointers=[],
                question=(
                    f"`{key}` is substituted into the JavaScript bundle at build time, so "
                    f"its value is already public -- it is readable in the shipped assets "
                    f"by anyone who loads the page, and has been for as long as the "
                    f"application has been deployed. It is declared in "
                    f"`{committed_path or read_path}`. If this is a real credential, "
                    f"treat it as disclosed: revoke it and move the call behind the "
                    f"backend. Replacing it and rebuilding does not help, because the new "
                    f"value is baked into the new bundle as well. Confirm here once you "
                    f"have decided."
                ),
                proposed=None,
                confidence="high",
                severity="important",
                evidence=[f"{committed_path or read_path}:{committed_line or read_line}"],
                evidence_scope=[rel(root, directory)],
            ))
            sensitive = False

        env.append(EnvVar(
            name=key,
            source="literal" if raw_value is not None else "platform",
            binding=binding,
            sensitive=sensitive,
            # AGENTS.md §4: never copy a secret value into generated output.
            value=None if sensitive else raw_value,
        ))
        evidence.append(Evidence(path=read_path, line=read_line))

    for key in sorted(committed):
        if key in reads or _is_dev_only(key):
            continue
        value, path_, line_ = committed[key]
        gaps.append(Gap(
            id=gap_id("service", service_name, "env", key),
            kind="value",
            # Zero pointers: no module reads this, so it has no AppSpec field. Gap to
            # field is zero-to-many and this is the zero end.
            pointers=[],
            question=(
                f"`{key}` is set in `{path_}` but no module in `{rel(root, directory)}` "
                f"reads it. It is probably dead configuration left by the platform. "
                f"Answer with a value to carry it into the deployment anyway, or say so "
                f"and it will be dropped."
            ),
            proposed=None,
            confidence="medium",
            severity="cosmetic",
            evidence=[f"{path_}:{line_}"],
            evidence_scope=[rel(root, directory)],
        ))

    evidence.sort(key=lambda item: (item.path, item.line))
    return env, gaps, evidence
