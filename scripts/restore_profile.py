"""Trace the stock emulator restore boundary with the same backend used by the trainer."""

import argparse
import functools
from pathlib import Path
import time
from unittest.mock import patch

from droidfork import emulator
from droidfork.android import observe


def measure(pool, node, mode):
    events = []

    def trace(label, fn):
        @functools.wraps(fn)
        def timed(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                name = label(args)
                if name:
                    events.append(
                        dict(
                            operation=name,
                            start_s=start - begin,
                            end_s=time.perf_counter() - begin,
                        )
                    )

        return timed

    guest = pool.reset(node) if mode == "rewind" else None
    try:
        if guest:
            observe(guest)
        begin = time.perf_counter()
        with (
            patch.object(
                emulator,
                "clone_avd",
                trace(lambda _: "clone private disks", emulator.clone_avd),
            ),
            patch.object(
                emulator,
                "run",
                trace(
                    lambda a: " ".join(a[0][3:]) if a[0][0] == "adb" else a[0][0],
                    emulator.run,
                ),
            ),
            patch.object(
                emulator.subprocess,
                "Popen",
                trace(
                    lambda a: "spawn emulator" if a[0][0] == "emulator" else None,
                    emulator.subprocess.Popen,
                ),
            ),
        ):
            if mode == "launch":
                guest = pool.reset(node)
            else:
                pool.rewind(guest, node)
        return dict(mode=mode, total_s=time.perf_counter() - begin, events=events)
    finally:
        if guest:
            guest.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("checkpoint")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument(
        "--out", type=Path, default=Path("results/restore-profile.json")
    )
    args = parser.parse_args()
    pool = emulator.Pool()
    samples = []
    for _ in range(args.rounds):
        for mode in ("launch", "rewind"):
            samples.append(measure(pool, args.checkpoint, mode))
            emulator.record(args.out, samples)
