import pytest
from gaps import NEVER_ANSWERABLE, answerable_set, is_moot, spec_level_gaps
from spec import AppSpec, Datastore, Delivery, Environment, Gap, Route, Source


def spec(name="widgets", routes=(), environments=(), delivery=None):
    return AppSpec(
        name=name,
        source=Source(platform="emergent", repo_url="https://x.invalid/a.git", commit=None),
        services=[], datastores=[], routes=list(routes), environments=list(environments),
        delivery=delivery or Delivery(image_registry=None, image_tag=None,
                                      gitops_repo_url=None, ingress_class=None,
                                      target_cluster="in-cluster"),
    )


def ids(gaps):
    return sorted(gap.id for gap in gaps)


def test_a_missing_app_name_is_blocking():
    gap = next(g for g in spec_level_gaps(spec(name=None)) if g.id == "app.name")
    assert gap.severity == "blocking"
    assert gap.proposed is None


def test_a_detected_app_name_raises_no_gap():
    assert "app.name" not in ids(spec_level_gaps(spec()))


def test_every_undetectable_delivery_field_is_a_gap():
    assert {"delivery.image_registry", "delivery.gitops_repo_url",
            "delivery.ingress_class", "delivery.image_tag"} <= set(ids(spec_level_gaps(spec())))


def test_the_image_tag_gap_says_ci_normally_answers_it():
    gap = next(g for g in spec_level_gaps(spec()) if g.id == "delivery.image_tag")
    assert "CI" in gap.question


def test_one_host_question_points_at_every_route():
    routes = [Route(host=None, path=p, service="backend", port=None, tls=False)
              for p in ("/api/health", "/api/items")]
    gap = next(g for g in spec_level_gaps(spec(routes=routes)) if g.id == "routes.host")
    assert gap.pointers == ["/routes/0/host", "/routes/1/host"]
    assert gap.severity == "blocking"


def test_no_routes_means_no_host_question():
    assert "routes.host" not in ids(spec_level_gaps(spec()))


def test_a_missing_namespace_is_a_gap_per_environment():
    environments = [Environment(name="production", namespace=None)]
    gaps = spec_level_gaps(spec(environments=environments))
    assert "environment.production.namespace" in ids(gaps)


def test_is_moot_retires_a_version_question_once_mode_is_external():
    appspec = spec()
    appspec.datastores = [Datastore(name="mongodb", kind="mongodb", version=None,
                                    mode="external", consumed_by=["backend"],
                                    env_keys=["MONGO_URL"])]
    gap = Gap(id="datastore.mongodb.version", kind="value", pointers=[], question="?",
              proposed=None, confidence="low", severity="important", evidence=[],
              depends_on=["datastore.mongodb.mode"])
    assert is_moot(gap, appspec) is True


def test_is_moot_keeps_the_version_question_when_the_mode_is_in_cluster():
    appspec = spec()
    appspec.datastores = [Datastore(name="mongodb", kind="mongodb", version=None,
                                    mode="in_cluster", consumed_by=["backend"],
                                    env_keys=["MONGO_URL"])]
    gap = Gap(id="datastore.mongodb.version", kind="value", pointers=[], question="?",
              proposed=None, confidence="low", severity="important", evidence=[],
              depends_on=["datastore.mongodb.mode"])
    assert is_moot(gap, appspec) is False


def test_answerable_set_declares_each_value_gap():
    gaps = spec_level_gaps(spec(name=None))
    declared, problems = answerable_set(gaps, spec(name=None))
    assert problems == []
    assert "app.name" in declared
    assert declared["app.name"]["pointers"] == ["/name"]


def test_answerable_set_excludes_action_gaps():
    action = Gap(id="credential.backend.env.X", kind="action", pointers=[], question="?",
                 proposed=None, confidence="high", severity="blocking", evidence=[])
    declared, problems = answerable_set([action], spec())
    assert declared == {}
    assert problems == []


def test_answerable_set_rejects_a_pointer_into_never_answerable_space():
    bad = Gap(id="services.0.name", kind="value", pointers=["/services/0/name"],
              question="?", proposed=None, confidence="high", severity="cosmetic",
              evidence=[])
    _, problems = answerable_set([bad], spec())
    assert len(problems) == 1
    assert "/services/0/name" in problems[0]


def test_never_answerable_covers_source_and_service_names():
    assert NEVER_ANSWERABLE == ("/source", "/services/*/name")
