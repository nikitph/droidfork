"""App checkpoints over a persistent contact and an unsaved second contact, with a database-only control."""

import io
import json
from pathlib import Path
import time
from PIL import Image, ImageChops

from droidfork.emulator import Pool, record, shell
from droidfork.android import CONTACTS, contact_rows, digest, observe, step
from droidfork.contacts import app_snapshot, app_restore, editor, get
from droidfork.scenario import NAMES, TARGET, ALTERNATE, prefix_actions


def run_actions(g, actions):
    for a in actions:
        step(g, a)


def commit_phone(g, phone):
    run_actions(
        g,
        [
            {"type": "tap", "label": "Phone"},
            {"type": "type", "text": phone},
            {"type": "tap", "label": "Save"},
        ],
    )


def pixel_difference(a, b):
    a, b = [Image.open(io.BytesIO(x)).convert("RGB") for x in (a, b)]
    diff = ImageChops.difference(a, b)
    return {
        "bbox": diff.getbbox(),
        "pixel_count": a.width * a.height,
        "different_pixels": sum(any(p) for p in diff.get_flattened_data()),
    }


def main():
    pool = Pool()
    base = json.loads((pool.root / "base.json").read_text())["node"]
    results = []
    with pool.reset(base) as g:
        run_actions(g, prefix_actions(("Isha", "Roy")))
        commit_phone(g, "2025550162")
        original = [{"name": "Isha Roy", "number": "2025550162"}]
        if contact_rows(g) != original:
            raise AssertionError("seed contact missing")
        for pair in NAMES[:5]:
            # Return to a known history. The agent opens the list before creating the draft.

            shell(g.serial, "am", "force-stop", CONTACTS)
            run_actions(g, prefix_actions(pair))
            before = observe(g)
            fields = editor(g)
            t = time.perf_counter()
            key = app_snapshot(g)
            snap_s = time.perf_counter() - t
            manifest = json.loads(get(pool, key))
            commit_phone(g, ALTERNATE)
            if len(contact_rows(g)) != 2:
                raise AssertionError("contaminating contact missing")
            t = time.perf_counter()
            app_restore(g, key)
            reset_s = time.perf_counter() - t
            after = observe(g)
            rows = contact_rows(g)
            if rows != original or editor(g) != fields:
                raise AssertionError("app restore fidelity failed")
            commit_phone(g, TARGET)
            expected = sorted(
                original + [{"name": " ".join(pair), "number": TARGET}],
                key=lambda x: x["name"],
            )
            if contact_rows(g) != expected:
                raise AssertionError("restored draft did not complete correctly")
            app_restore(g, key)
            results.append(
                {
                    "name": " ".join(pair),
                    "snapshot_s": snap_s,
                    "restore_s": reset_s,
                    "manifest_bytes": len(get(pool, key)),
                    "db_bytes": len(get(pool, manifest["db"])),
                    "database_sha256": manifest["db"],
                    "form_and_provider_match": True,
                    "reward": 1,
                    "before": digest(before),
                    "after": digest(after),
                    "pixel_difference": pixel_difference(before["png"], after["png"]),
                }
            )
            record(
                Path("results/app-benchmark.json"),
                {"task": "contacts-editor", "trials": results},
            )
            print(results[-1]["name"], round(reset_s, 2), flush=True)


if __name__ == "__main__":
    main()
