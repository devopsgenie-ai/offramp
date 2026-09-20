import json
import pytest
from spec import (
    AppSpec, Delivery, Evidence, Gap, Source,
    canonical_json, check_enums, evidence_text, gap_id,
)


def empty_spec(name="widgets"):
    return AppSpec(
        name=name,
        source=Source(platform="emergent", repo_url=None, commit=None),
        services=[], datastores=[], routes=[], environments=[],
        delivery=Delivery(image_registry=None, image_tag=None,
                          gitops_repo_url=None, ingress_class=None,
                          target_cluster="in-cluster"),
    )


def a_gap(**kw):
    base = dict(id="app.name", kind="value", pointers=["/name"],
                question="What is the application called?", proposed="widgets",
                confidence="high", severity="blocking", evidence=[])
    base.update(kw)
    return Gap(**base)


def test_canonical_json_is_byte_stable():
    spec = empty_spec()
    assert canonical_json(spec) == canonical_json(spec)


def test_canonical_json_sorts_keys_and_ends_with_newline():
    out = canonical_json({"b": 1, "a": 2})
    assert out == '{\n  "a": 2,\n  "b": 1\n}\n'


def test_canonical_json_sorts_nested_dict_keys_regardless_of_insertion_order():
    first = canonical_json({"outer": {"z": 1, "a": 2}})
    second = canonical_json({"outer": {"a": 2, "z": 1}})
    assert first == second


def test_canonical_json_preserves_list_order():
    assert json.loads(canonical_json({"x": [3, 1, 2]}))["x"] == [3, 1, 2]


def test_gap_id_is_dot_joined_and_name_based():
    assert gap_id("service", "api", "health", "path") == "service.api.health.path"


def test_evidence_text_renders_path_and_line():
    assert evidence_text([Evidence(path="backend/server.py", line=12)]) == [
        "backend/server.py:12"
    ]


def test_check_enums_accepts_a_valid_spec():
    assert check_enums(empty_spec(), [a_gap()]) == []


def test_check_enums_rejects_an_unknown_severity():
    problems = check_enums(empty_spec(), [a_gap(severity="urgent")])
    assert len(problems) == 1
    assert "severity" in problems[0] and "urgent" in problems[0]


def test_check_enums_rejects_an_unknown_origin():
    problems = check_enums(empty_spec(), [a_gap(origin="vibes")])
    assert any("origin" in problem for problem in problems)


def test_gap_defaults_origin_to_detector_and_empty_dependencies():
    gap = a_gap()
    assert gap.origin == "detector"
    assert gap.depends_on == []
    assert gap.evidence_scope == []
