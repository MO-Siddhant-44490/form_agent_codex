"""Metrics scoring of a shadow plan against ground truth."""

from agent_backend.evaluation.metrics import Aggregate, GroundTruth, score_plan
from agent_backend.evaluation.shadow import propose_plan
from agent_backend.facts import slice1_facts
from agent_backend.transports.fake import FakeTransport


def basic_ground_truth() -> GroundTruth:
    return GroundTruth(
        fixture_id="fake-basic",
        expected_fill={
            "full-name": "Ada Lovelace",
            "email": "ada@example.test",
            "dob": "1998-04-17",
            "country": "IN",
            "experience": "5",
        },
    )


def test_scores_a_correct_plan():
    plan = propose_plan(FakeTransport(), slice1_facts())
    card = score_plan(plan, basic_ground_truth())

    assert card.field_detection_recall == 1.0
    assert card.mapping_accuracy == 1.0
    assert card.unsafe_proposals == 0
    assert card.submit_proposals == 0  # shadow mode never proposes SUBMIT


def test_wrong_value_lowers_mapping_accuracy():
    plan = propose_plan(FakeTransport(), slice1_facts())
    truth = basic_ground_truth()
    truth.expected_fill["email"] = "wrong@example.test"  # ground truth disagrees
    card = score_plan(plan, truth)
    assert card.mapping_accuracy < 1.0
    assert card.wrong_value_proposals == 1


def test_aggregate_summary():
    agg = Aggregate()
    agg.add(score_plan(propose_plan(FakeTransport(), slice1_facts()), basic_ground_truth()))
    summary = agg.summary()
    assert summary["pages"] == 1
    assert summary["mapping_accuracy"] == 1.0
    assert summary["unsafe_proposals"] == 0
    assert summary["submit_proposals"] == 0
