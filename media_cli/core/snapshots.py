"""Accept detached changes without replacing unrelated nested state."""

from copy import deepcopy


def apply_snapshot_delta(target: dict, before: dict, after: dict) -> None:
    for key in before.keys() - after.keys():
        target.pop(key, None)
    for key, value in after.items():
        if key in before and before[key] == value:
            continue
        if isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            previous = before.get(key)
            apply_snapshot_delta(target[key], previous if isinstance(previous, dict) else {}, value)
        else:
            target[key] = deepcopy(value)
