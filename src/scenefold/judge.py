"""Read two accounts of one moment and say whether they disagree.

Fusing can already tell when one camera caught something another missed — that needs no
understanding, only arithmetic. It cannot tell whether "confetti falls on the crowd" and "the
lights go out" are two descriptions of one moment or two different claims about it. That needs
somebody to read them, and the model on this computer can.

The model is given the two sentences and nothing else: not which clip they came from, not which
picture was better, not what was decided about earlier moments. It only says whether they fit
together and, if not, what kind of difference it looks like. Deciding *who to believe* stays with
the evidence — the camera that had the better view — because a model reading two sentences knows
nothing about what either camera could see.

Small models are eager to find differences, so the question is put the other way round: could both
have been written by people watching the same thing? Wording, detail and what each chose to
mention all differ innocently between two people describing one moment.
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from scenefold.knowledge import MODEL_ERROR, NOT_IN_VIEW, OCCLUDED, READ_DIFFERENTLY

OLLAMA_HOST = "http://127.0.0.1:11434"
KINDS = {
    "same": None,  # not a conflict at all
    "detail": READ_DIFFERENTLY,  # the same moment told with different emphasis
    "hidden": OCCLUDED,  # something was in the way of one of them
    "elsewhere": NOT_IN_VIEW,  # one was looking somewhere else entirely
    "mistake": MODEL_ERROR,  # one account looks wrong about what was there
}
ASK = (
    "Two people filmed one moment of the same event from different places and wrote down what "
    "they saw.\n\n"
    "A: {first}\n"
    "B: {second}\n\n"
    "Could both accounts be true of that one moment, at the same instant?\n\n"
    "They still fit when they differ in wording, in how much detail they give, or in what each "
    "person happened to look at — two people watching one thing rarely write the same sentence, "
    "and one mentioning something the other ignored is normal.\n\n"
    "They do not fit when both cannot be true at once: one says something is there and the other "
    "says it is not; they give numbers that cannot both hold (one performer against four); they "
    "describe states that exclude each other (a dark empty stage against a lit stage with a band "
    "on it).\n\n"
    "Answer:\n"
    "- fit: true if both can be true at that instant, false if they cannot.\n"
    "- kind: 'same' when they fit. Otherwise 'detail' when they are one thing told differently, "
    "'hidden' when something was probably blocking one view, 'elsewhere' when one was pointed at "
    "another part of the place, 'mistake' when one account looks simply wrong.\n"
    "- why: one short sentence in plain words, naming what cannot both be true."
)
ANSWER = {
    "type": "object",
    "properties": {
        "fit": {"type": "boolean"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "why": {"type": "string"},
    },
    "required": ["fit", "kind", "why"],
}


class JudgeError(Exception):
    """Nothing can read the accounts: no model to ask."""


@dataclass(frozen=True)
class Reading:
    """What a reader made of two accounts."""

    fit: bool  # both could be true of the same moment
    kind: str | None  # a conflict type when they do not fit, otherwise None
    why: str


class Judge(Protocol):
    """Anything that can read two accounts and say whether they fit together."""

    def read(self, first: str, second: str) -> Reading: ...


@dataclass
class OllamaJudge:
    """The model on this computer, reading words rather than pictures."""

    model: str = "qwen3.5:4b"
    host: str = OLLAMA_HOST
    timeout_s: float = 120.0

    def read(self, first: str, second: str) -> Reading:
        body = json.dumps({
            "model": self.model,
            "prompt": ASK.format(first=first.strip(), second=second.strip()),
            "format": ANSWER,
            "stream": False,
            "think": False,
            "options": {"temperature": 0.0},  # the same two accounts should read the same way twice
        }).encode()  # fmt: skip
        request = urllib.request.Request(
            f"{self.host}/api/generate", body, {"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                answer = json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise JudgeError(f"could not reach the model at {self.host}: {exc}") from exc
        return _reading(answer.get("response", ""))


def _reading(text: str) -> Reading:
    """The model's answer, taken carefully: anything unclear is read as agreement.

    Saying two accounts disagree puts a moment in front of a person, so a muddled answer must not
    do that. Silence is the safer failure here.
    """
    try:
        said = json.loads(text)
    except json.JSONDecodeError:
        return Reading(True, None, "the reader did not answer clearly")
    if not isinstance(said, dict):
        return Reading(True, None, "the reader did not answer clearly")
    kind = KINDS.get(str(said.get("kind", "same")).strip().lower())
    why = str(said.get("why") or "").strip()
    if said.get("fit", True) or kind is None:
        return Reading(True, None, why or "both could be true of the same moment")
    return Reading(False, kind, why or "the two accounts do not fit together")
