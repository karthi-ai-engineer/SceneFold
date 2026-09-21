"""Reading two accounts of one moment: does it catch real contradictions without inventing them?"""

import json

import pytest

from scenefold.judge import KINDS, OllamaJudge, Reading, _reading
from scenefold.knowledge import MODEL_ERROR, OCCLUDED, READ_DIFFERENTLY

# Pairs a person would have no trouble with: the first three fit, the last three cannot.
PAIRS = [
    (True, "A wide shot of a stadium crowd holding up lights at night.",
     "A large concert crowd waving phone lights in a dark arena."),
    (True, "The screens show a close-up of the singer.",
     "The crowd jumps with their hands in the air."),
    (True, "A bright stage with blue lighting and a large crowd.",
     "The audience holds up lights while the stage glows blue behind them."),
    (False, "Confetti falls over the crowd as the song ends.",
     "The stage is empty and the lights are off."),
    (False, "A performer stands alone at a piano.",
     "Four band members play together at the front of the stage."),
    (False, "The stadium is empty apart from a few staff.",
     "A huge crowd fills every seat, singing along."),
]  # fmt: skip


def test_an_answer_that_fits_is_not_a_conflict():
    said = _reading(json.dumps({"fit": True, "kind": "same", "why": "two views of one thing"}))

    assert said.fit and said.kind is None


def test_each_kind_of_difference_becomes_its_own_conflict():
    for word, kind in (
        ("detail", READ_DIFFERENTLY),
        ("hidden", OCCLUDED),
        ("mistake", MODEL_ERROR),
    ):
        said = _reading(json.dumps({"fit": False, "kind": word, "why": "they differ"}))

        assert said.kind == kind
        assert not said.fit


def test_a_muddled_answer_is_read_as_agreement():
    """Calling a disagreement puts a moment in front of a person, so an unclear answer must not."""
    for answer in ("not json at all", "[]", '{"fit": false}', '{"fit": false, "kind": "banana"}'):
        said = _reading(answer)

        assert said.fit, answer
        assert said.kind is None


def test_saying_it_does_not_fit_without_a_reason_still_names_one():
    said = _reading(json.dumps({"fit": False, "kind": "detail", "why": ""}))

    assert said.kind == READ_DIFFERENTLY
    assert said.why


def test_every_word_the_model_may_answer_has_a_meaning():
    assert set(KINDS) == {"same", "detail", "hidden", "elsewhere", "mistake"}
    assert KINDS["same"] is None
    assert all(kind for word, kind in KINDS.items() if word != "same")


def test_a_reading_knows_what_it_is():
    said = Reading(fit=False, kind=OCCLUDED, why="a pillar")

    assert (said.fit, said.kind, said.why) == (False, OCCLUDED, "a pillar")


def test_the_real_reader_tells_agreement_from_contradiction():
    """The model itself, on pairs whose answers are not in doubt. Skipped when it isn't here."""
    judge = OllamaJudge()
    try:
        judge.read("a test", "another test")
    except Exception:  # noqa: BLE001 - no model on this machine, which is fine
        pytest.skip("no model is running to read with")

    got = [judge.read(first, second).fit for _, first, second in PAIRS]

    wanted = [fit for fit, _, _ in PAIRS]
    assert got == wanted
