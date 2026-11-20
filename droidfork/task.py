"""Scripted GUI policy and an independent provider-based reward for a contact-creation task."""

import time
from .android import contact_rows, judge, step, wait_label

from .scenario import TARGET, ALTERNATE, prefix_actions


def prefix(g, pair):
    for action in prefix_actions(pair):
        step(g, action)
    wait_label(g, "Northline Studio")
    if contact_rows(g):
        raise RuntimeError("prefix unexpectedly persisted a contact")


def suffix(g, pair, index):
    number = TARGET if index % 2 == 0 else ALTERNATE
    name = " ".join(pair)
    step(g, {"type": "tap", "label": "Phone"})
    step(g, {"type": "type", "text": number})
    step(g, {"type": "tap", "label": "Save"})
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline and not contact_rows(g):
        time.sleep(0.05)
    actual = contact_rows(g)
    if actual != [{"name": name, "number": number}]:
        raise AssertionError(f"branch isolation or saved contact failed: {actual}")
    reward = judge(g, name, TARGET)
    if reward != float(index % 2 == 0):
        raise AssertionError("wrong verifier result")
    return {"chosen_number": number, "rows": actual, "reward": reward}
