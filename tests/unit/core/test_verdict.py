from __future__ import annotations

from openbox.core.types import Verdict


def test_constrain_value():
    assert Verdict.CONSTRAIN.value == "constrain"


def test_constrain_priority_between_allow_and_require_approval():
    assert Verdict.ALLOW.priority == 1
    assert Verdict.CONSTRAIN.priority == 2
    assert Verdict.REQUIRE_APPROVAL.priority == 3


def test_constrain_does_not_stop_or_require_approval():
    assert Verdict.CONSTRAIN.should_stop() is False
    assert Verdict.CONSTRAIN.requires_approval() is False


def test_from_string_constrain_resolves_to_constrain():
    assert Verdict.from_string("constrain") is Verdict.CONSTRAIN
    assert Verdict.from_string("CONSTRAIN") is Verdict.CONSTRAIN


def test_highest_priority_picks_constrain_over_allow():
    assert Verdict.highest_priority(Verdict.ALLOW, Verdict.CONSTRAIN) is Verdict.CONSTRAIN
    assert Verdict.highest_priority(Verdict.CONSTRAIN, Verdict.BLOCK) is Verdict.BLOCK
