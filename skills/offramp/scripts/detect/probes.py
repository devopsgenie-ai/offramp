"""Health probes, from the composed routes.

Two things this module refuses to do. It will not invent a probe path -- a probe
pointing at a path that does not exist is the most expensive kind of confidently wrong
output, because Kubernetes will restart a working container on it forever. And it will
not nominate a business endpoint as a health check: `/api/items` may be slow, may hit
the database, and may legitimately 404.
"""

from __future__ import annotations

from spec import Gap, Probe, Route, gap_id

HEALTH_NAMES = ("health", "healthz", "livez", "readyz", "ping", "_health")


def _is_health(path: str) -> bool:
    return path.rstrip("/").rsplit("/", 1)[-1].lower() in HEALTH_NAMES


def detect_probes(
    routes: list[Route], service_name: str, port: "int | None"
) -> tuple[list[Probe], list[Gap]]:
    candidates = sorted(route.path for route in routes if _is_health(route.path))

    if not candidates:
        return [], [Gap(
            id=gap_id("service", service_name, "probes"),
            kind="action",
            pointers=[],
            question=(
                f"`{service_name}` exposes no health endpoint, so no liveness or "
                f"readiness probe can be generated for it. Add one -- a handler that "
                f"returns 200 without touching the database is enough -- and confirm "
                f"here. Without it Kubernetes cannot tell a started container from a "
                f"working one, and a rollout will report success while the service is "
                f"broken. No path is proposed because pointing a probe at a business "
                f"endpoint restarts a healthy container whenever that endpoint is slow."
            ),
            proposed=None,
            confidence="high",
            severity="important",
            evidence=[],
        )]

    if port is None:
        return [], [Gap(
            id=gap_id("service", service_name, "probes", "port"),
            kind="value",
            pointers=[],
            question=(
                f"`{service_name}` has a health endpoint at `{candidates[0]}` but no "
                f"known port, so its probes cannot be written. This resolves itself "
                f"once the service's port is answered."
            ),
            proposed=None,
            confidence="medium",
            severity="important",
            evidence=[],
            depends_on=[gap_id("service", service_name, "ports")],
        )]

    path = candidates[0]
    # Sorted by role so the AppSpec bytes do not depend on dict ordering.
    return [
        Probe(role=role, path=path, port=port, kind="http")
        for role in ("liveness", "readiness")
    ], []
