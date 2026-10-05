import pytest

from temple_cua.protocol import Action, Decision


@pytest.mark.parametrize("value", [
    {"kind": "click", "x": -1, "y": 0},
    {"kind": "move", "x": 640, "y": 0},
    {"kind": "click", "x": True, "y": 0},
    {"kind": "wait", "seconds": float("nan")},
    {"kind": "wait", "seconds": float("inf")},
    {"kind": "type", "text": "Unicode: Ω"},
    {"kind": "type", "text": "\x1b"},
    {"kind": "key", "keys": []},
    {"kind": "scroll", "direction": "left"},
    {"kind": "host_shell", "text": "ls"},
    {"kind": "wait", "shell": "ls"},
    {"kind": "wait", "text": "does not apply"},
    {"seconds": 1},
])
def test_invalid_actions_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        Action.from_dict(value)


def test_done_must_end_sequence():
    with pytest.raises(ValueError, match="last"):
        Decision([Action("done", text="done"), Action("wait")])


def test_action_roundtrip_preserves_only_applicable_values():
    action = Action("click", x=123, y=456, button="right", clicks=2)
    assert Action.from_dict(action.to_dict()) == action
