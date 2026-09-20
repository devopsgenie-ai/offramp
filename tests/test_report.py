from report import render_gap_report
from spec import AppSpec, Delivery, Gap, Source


def spec(name="widgets"):
    return AppSpec(
        name=name,
        source=Source(platform="emergent", repo_url=None, commit=None),
        services=[], datastores=[], routes=[], environments=[],
        delivery=Delivery(image_registry=None, image_tag=None, gitops_repo_url=None,
                          ingress_class=None, target_cluster="in-cluster"),
    )


def gap(**kw):
    base = dict(id="app.name", kind="value", pointers=["/name"], question="What name?",
                proposed="widgets", confidence="high", severity="blocking", evidence=[])
    base.update(kw)
    return Gap(**base)


def test_report_is_byte_stable():
    gaps = [gap(), gap(id="delivery.ingress_class", severity="important")]
    assert render_gap_report(spec(), gaps) == render_gap_report(spec(), gaps)


def test_blocking_gaps_come_first():
    body = render_gap_report(spec(), [
        gap(id="b", severity="cosmetic"), gap(id="a", severity="blocking"),
    ])
    assert body.index("## blocking") < body.index("## cosmetic")


def test_counts_by_severity_appear_in_the_header():
    body = render_gap_report(spec(), [gap(), gap(id="x", severity="cosmetic")])
    assert "blocking: 1" in body and "cosmetic: 1" in body


def test_an_action_gap_says_nothing_goes_in_the_answers_file():
    body = render_gap_report(spec(), [gap(id="credential.x", kind="action", proposed=None)])
    assert "nothing goes in the answers file" in body


def test_a_dependent_gap_tells_the_reader_what_to_answer_first():
    body = render_gap_report(spec(), [gap(id="datastore.mongodb.version",
                                          depends_on=["datastore.mongodb.mode"])])
    assert "datastore.mongodb.mode" in body
    assert "first" in body


def test_a_missing_name_is_named_in_the_title_rather_than_guessed():
    assert "unnamed" in render_gap_report(spec(name=None), [gap()])


def test_the_report_ends_with_exactly_one_newline():
    body = render_gap_report(spec(), [gap()])
    assert body.endswith("\n") and not body.endswith("\n\n")
