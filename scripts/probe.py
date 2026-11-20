import json
import re
import time
from xml.etree import ElementTree as ET
from droidfork.emulator import Pool, parallel, record
from droidfork.android import observe, digest, contact_rows, step
from droidfork.contacts import app_snapshot, app_restore, editor
from droidfork.task import prefix, suffix
from droidfork.scenario import NAMES
from pathlib import Path


def main():
    p = Pool()
    base = json.loads((p.root / "base.json").read_text())["node"]
    with p.reset(base) as parent:
        prefix(parent, NAMES[0])
        ref = observe(parent)
        path = Path("results/evidence")
        path.mkdir(parents=True, exist_ok=True)
        (path / "parent-draft.png").write_bytes(ref["png"])
        (path / "parent-draft.xml").write_text(ref["xml"])
        t = time.monotonic()
        node = p.snapshot(parent, "Mira before phone")
        snap = time.monotonic() - t
        key = app_snapshot(parent)
        t = time.monotonic()
        children = p.fork(node, 2)
        fork = time.monotonic() - t
        try:
            obs = parallel(children, observe)
            for i, o in enumerate(obs):
                (path / f"child-{i}-draft.png").write_bytes(o["png"])
                (path / f"child-{i}-draft.xml").write_text(o["xml"])
            rows = parallel(
                list(enumerate(children)),
                lambda pair: suffix(pair[1], NAMES[0], pair[0]),
            )
            for index, child in enumerate(children):
                number = rows[index]["chosen_number"]

                def shows_number(obs):
                    return any(
                        number
                        in re.sub(
                            r"\D",
                            "",
                            " ".join((n.get("text", ""), n.get("content-desc", ""))),
                        )
                        for n in ET.fromstring(obs["xml"]).iter("node")
                    )

                detail = observe(child)
                if any(
                    n.get("content-desc") == "Close Popup Window"
                    for n in ET.fromstring(detail["xml"]).iter("node")
                ):
                    step(child, {"type": "tap", "label": "Close Popup Window"})
                    detail = observe(child)
                if not shows_number(detail):
                    step(child, {"type": "tap", "label": "Mira Shah"})
                    detail = observe(child)
                if not shows_number(detail):
                    raise AssertionError(
                        "saved number missing from Contacts detail screen"
                    )
                (path / f"child-{index}-saved.png").write_bytes(detail["png"])
                (path / f"child-{index}-saved.xml").write_text(detail["xml"])
            if contact_rows(parent):
                raise AssertionError("parent mutated")
            after = app_snapshot(parent)
            t = time.monotonic()
            app_restore(children[0], key)
            app_time = time.monotonic() - t
            if contact_rows(children[0]):
                raise AssertionError("app reset retained child contact")
            if contact_rows(children[1]) != rows[1]["rows"]:
                raise AssertionError("sibling mutated")
            if editor(children[0]) != editor(parent):
                raise AssertionError("draft changed")
            appobs = observe(children[0])
            (path / "app-restored.png").write_bytes(appobs["png"])
            (path / "app-restored.xml").write_text(appobs["xml"])
            # A fork of an already forked guest must preserve its committed contact.
            second = p.snapshot(children[1], "saved alternate contact")
            with p.reset(second) as nested:
                if contact_rows(nested) != rows[1]["rows"]:
                    raise AssertionError("nested fork lost committed contact")
            report = dict(
                base=base,
                node=node,
                snapshot_s=snap,
                fork_2_s=fork,
                app_restore_s=app_time,
                parent=digest(ref),
                children=[digest(x) for x in obs],
                app_restored=digest(appobs),
                outcomes=rows,
                parent_rows=contact_rows(parent),
                nested_rows=rows[1]["rows"],
                app_key=key,
                manifest_reused=key == after,
                restored_fields=editor(children[0]),
            )
            record(Path("results/functional.json"), report)
            print(json.dumps(report, indent=2), flush=True)
        finally:
            p.release(children)


if __name__ == "__main__":
    main()
