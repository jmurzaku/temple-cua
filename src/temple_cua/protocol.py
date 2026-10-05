"""Validated actions and tool schemas shared by the model providers."""

from dataclasses import asdict, dataclass, field
import math
from typing import Any

KINDS = ("type", "key", "click", "move", "scroll", "wait", "done")
_FIELDS_BY_KIND = {
    "type": ("text",), "done": ("text",), "key": ("keys",),
    "click": ("x", "y", "button", "clicks"), "move": ("x", "y"),
    "scroll": ("direction", "amount"), "wait": ("seconds",),
}


@dataclass(frozen=True)
class Action:
    kind: str
    text: str | None = None
    keys: list[str] | None = None
    x: int | None = None
    y: int | None = None
    button: str = "left"
    clicks: int = 1
    seconds: float = 0.5
    direction: str | None = None
    amount: int = 1

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"Unknown action kind: {self.kind!r}")
        if self.kind in {"type", "done"}:
            if not isinstance(self.text, str) or len(self.text) > 4096:
                raise ValueError("type/done require text of at most 4096 characters")
            if self.kind == "type" and (not self.text or any(ord(c) > 126 or (ord(c) < 32 and c not in '\n\t') for c in self.text)):
                raise ValueError("TempleOS typing accepts printable US ASCII, newline and tab")
        if self.kind == "key":
            if not isinstance(self.keys, list) or not 1 <= len(self.keys) <= 5:
                raise ValueError("key requires 1..5 keys in a chord")
            if any(not isinstance(k, str) or not k or len(k) > 24 for k in self.keys):
                raise ValueError("Invalid key name")
        if self.kind in {"click", "move"}:
            if type(self.x) is not int or type(self.y) is not int or not (0 <= self.x < 640 and 0 <= self.y < 480):
                raise ValueError("Mouse coordinates must be integers within 640x480")
        if self.button not in {"left", "middle", "right"} or type(self.clicks) is not int or not 1 <= self.clicks <= 3:
            raise ValueError("Invalid mouse button/click count")
        if not isinstance(self.seconds, (int, float)) or isinstance(self.seconds, bool) or not math.isfinite(self.seconds) or not 0 <= self.seconds <= 10:
            raise ValueError("seconds must be finite and within 0..10")
        if self.kind == "scroll" and self.direction not in {"up", "down"}:
            raise ValueError("scroll direction must be up or down")
        if type(self.amount) is not int or not 1 <= self.amount <= 20:
            raise ValueError("amount must be an integer within 1..20")

    @classmethod
    def from_dict(cls, value: dict) -> "Action":
        if not isinstance(value, dict):
            raise ValueError("Action must be an object")
        unknown = set(value) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"Unknown action fields: {sorted(unknown)}")
        try:
            action = cls(**value)
        except TypeError as exc:
            raise ValueError("Action fields are missing or have invalid types") from exc
        irrelevant = set(value) - set(action.to_dict())
        if irrelevant:
            raise ValueError(f"Fields do not apply to {action.kind}: {sorted(irrelevant)}")
        return action

    def to_dict(self) -> dict:
        value = asdict(self)
        return {key: value[key] for key in ("kind", *_FIELDS_BY_KIND[self.kind])}


@dataclass
class Decision:
    actions: list[Action]
    note: str = ""
    usage: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not 1 <= len(self.actions) <= 4 or any(not isinstance(a, Action) for a in self.actions):
            raise ValueError("A decision requires 1..4 validated actions")
        if any(a.kind == "done" for a in self.actions[:-1]):
            raise ValueError("done must be the last action")
        if not isinstance(self.note, str) or len(self.note) > 8192:
            raise ValueError("Invalid decision note")


def action_json_schema() -> dict:
    # OpenAI strict tools require every object's properties to be required.
    # Separate shapes prevent unrelated null/default fields from leaking into
    # actions while supplying the same schema to both model providers.
    fields = {
        "text": {"type": "string", "maxLength": 4096},
        "keys": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 24}, "minItems": 1, "maxItems": 5},
        "x": {"type": "integer", "minimum": 0, "maximum": 639},
        "y": {"type": "integer", "minimum": 0, "maximum": 479},
        "button": {"type": "string", "enum": ["left", "middle", "right"]},
        "clicks": {"type": "integer", "minimum": 1, "maximum": 3},
        "seconds": {"type": "number", "minimum": 0, "maximum": 10},
        "direction": {"type": "string", "enum": ["up", "down"]},
        "amount": {"type": "integer", "minimum": 1, "maximum": 20},
    }
    variants = []
    for kind in KINDS:
        properties = {"kind": {"type": "string", "enum": [kind]}}
        properties.update({name: fields[name] for name in _FIELDS_BY_KIND[kind]})
        variants.append({
            "type": "object", "additionalProperties": False,
            "properties": properties, "required": list(properties),
        })
    return {
        "anyOf": variants,
        "description": "Use only the fields applicable to this action. Keys are US key names or chords such as ['ctrl','c']. Coordinates are screenshot pixels.",
    }


def decision_json_schema() -> dict:
    return {"type": "object", "additionalProperties": False, "required": ["actions", "note"],
            "properties": {"actions": {"type": "array", "items": action_json_schema(), "minItems": 1, "maxItems": 4},
                           "note": {"type": "string", "maxLength": 8192}}}
