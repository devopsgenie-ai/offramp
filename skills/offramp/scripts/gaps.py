"""Gaps that belong to the run rather than to one detector, and the answerable set.

Delivery is the motivating case. The overlay needs a registry, the ApplicationSet needs
the URL of the *deployment* repository and a destination cluster, and the Ingress needs
a class. None is a property of the application and none can be detected -- and
`AppSpec.source.repo_url` is provenance for the *source* repository and declared never
answerable, so it is the wrong field twice over.
"""

from __future__ import annotations

import fnmatch

from spec import AppSpec, Gap, gap_id

#: Pointers no plan may ever target. `source` is provenance; a service's name is its
#: identity and answers key on it, so letting it be answered would orphan every answer
#: in the user's repository at once.
NEVER_ANSWERABLE = ("/source", "/services/*/name")


def _delivery_gap(field: str, question: str, proposed, severity="blocking") -> Gap:
    return Gap(
        id=gap_id("delivery", field),
        kind="value",
        pointers=[f"/delivery/{field}"],
        question=question,
        proposed=proposed,
        confidence="low",
        severity=severity,
        evidence=[],
        # Nothing in the repository speaks to where this deploys, so no citation is
        # load-bearing. That is different from "cite nothing".
        evidence_scope=[],
    )


def spec_level_gaps(appspec: AppSpec) -> list[Gap]:
    gaps: list[Gap] = []

    if appspec.name is None:
        gaps.append(Gap(
            id="app.name",
            kind="value",
            pointers=["/name"],
            question=(
                "What is this application called? No git remote was found, and the name "
                "reaches every label, the namespace, the Ingress host and the Secret the "
                "services read their connection strings from. The checkout directory is "
                "deliberately not used: it would make the generated tree depend on where "
                "the repository happens to sit on disk."
            ),
            proposed=None,
            confidence="low",
            severity="blocking",
            evidence=[],
        ))

    gaps.append(_delivery_gap(
        "image_registry",
        "Which container registry will hold the built images? The overlay writes image "
        "references against it, so nothing can be pulled until it is set. Example: "
        "ghcr.io/your-org.",
        None,
    ))
    gaps.append(_delivery_gap(
        "gitops_repo_url",
        "What is the URL of the *deployment* repository -- the one this generated tree "
        "will be committed to and that Argo CD watches? This is not the application's "
        "source repository, which is recorded separately as provenance.",
        None,
    ))
    gaps.append(_delivery_gap(
        "ingress_class",
        "Which ingress controller serves this cluster? The Ingress needs its class name. "
        "Common answers are nginx, traefik and alb.",
        None,
    ))
    gaps.append(_delivery_gap(
        "image_tag",
        "Which image tag should the overlay reference? CI normally answers this on every "
        "build rather than a person answering it once, and the answers file records that "
        "it was accepted by CI rather than by a human.",
        None,
        severity="important",
    ))

    if appspec.routes:
        gaps.append(Gap(
            id="routes.host",
            kind="value",
            pointers=[f"/routes/{index}/host" for index in range(len(appspec.routes))],
            question=(
                f"Which hostname serves this application? One answer sets the host on "
                f"all {len(appspec.routes)} detected routes. Nothing in the repository "
                f"records the production hostname -- the platform supplied it."
            ),
            proposed=None,
            confidence="low",
            severity="blocking",
            evidence=[],
        ))

    for index, environment in enumerate(appspec.environments):
        if environment.namespace is None:
            gaps.append(Gap(
                id=gap_id("environment", environment.name, "namespace"),
                kind="value",
                pointers=[f"/environments/{index}/namespace"],
                question=(
                    f"Which Kubernetes namespace does the `{environment.name}` overlay "
                    f"deploy into? Every generated object is namespaced by it."
                ),
                proposed=None,
                confidence="medium",
                severity="blocking",
                evidence=[],
            ))

    gaps.sort(key=lambda gap: gap.id)
    return gaps


def is_moot(gap: Gap, appspec: AppSpec) -> bool:
    """True when a gap's precondition has been answered in a way that retires it.

    RFC-0001: "datastore.version only matters if mode is in_cluster; answering managed
    retires it unasked." A flat gap list without this asks questions whose answers cannot
    matter, which is how a reviewer learns to click through.
    """
    for dependency in gap.depends_on:
        parts = dependency.split(".")
        if len(parts) == 3 and parts[0] == "datastore" and parts[2] == "mode":
            store = next((item for item in appspec.datastores if item.name == parts[1]), None)
            if store is not None and store.mode in ("managed", "external"):
                return True
    return False


def answerable_set(gaps: list[Gap], appspec: AppSpec) -> tuple[dict, list[str]]:
    """What a plan is permitted to target, declared by the gaps themselves.

    Returned rather than enforced here: `apply` rejects a plan entry outside this set,
    and that check needs the same declaration this produces.
    """
    declared: dict = {}
    problems: list[str] = []
    for gap in gaps:
        if gap.kind == "action":
            continue          # acknowledged, not valued: there is no target to write
        for pointer in gap.pointers:
            if any(fnmatch.fnmatch(pointer, pattern + "*") or fnmatch.fnmatch(pointer, pattern)
                   for pattern in NEVER_ANSWERABLE):
                problems.append(
                    f"gap {gap.id} declares pointer {pointer} as answerable, but it is in "
                    f"never-answerable space ({', '.join(NEVER_ANSWERABLE)})"
                )
        declared[gap.id] = {
            "pointers": list(gap.pointers),
            "kind": gap.kind,
            "severity": gap.severity,
            "evidence_scope": list(gap.evidence_scope),
        }
    return declared, problems
