"""Measure on-demand fan-out from a sealed machine checkpoint and verify a common GUI action."""

import argparse
import ctypes
import json
from pathlib import Path
import platform
import struct
import time

from droidfork.android import digest, observe, step
from droidfork.contacts import editor
from droidfork.emulator import Pool, parallel, record, run
from droidfork.scenario import NAMES
from droidfork.task import prefix


def memory(g):
    pid = g.proc.pid
    rss = int(run(["ps", "-p", str(pid), "-o", "rss="]).strip()) * 1024
    sample = {"pid": pid, "rss_bytes": rss}
    if platform.system() == "Darwin":
        # rusage_info_v0 has a sixteen-byte uuid followed by eight uint64 counters.
        # The eighth counter is ri_phys_footprint; this is macOS's per-process charge.
        buf = ctypes.create_string_buffer(1024)
        if (
            ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True).proc_pid_rusage(
                pid, 0, buf
            )
            == 0
        ):
            sample["physical_footprint_bytes"] = struct.unpack_from(
                "=Q", buf, 16 + 7 * 8
            )[0]
    return sample


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--counts", default="1,2,3")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--out", type=Path, default=Path("results/fanout.json"))
    args = parser.parse_args()
    p = Pool()
    base = json.loads((p.root / "base.json").read_text())["node"]
    with p.reset(base) as parent:
        prefix(parent, NAMES[0])
        node = p.snapshot(parent, "fanout-benchmark")
    counts = list(map(int, args.counts.split(",")))
    trials = []
    for r in range(args.repeats):
        for n in counts[r % len(counts) :] + counts[: r % len(counts)]:
            t = time.perf_counter()
            kids = p.fork(node, n)
            ready = time.perf_counter() - t
            try:

                def act(g):
                    step(g, {"type": "tap", "label": "Phone"})
                    obs = observe(g)
                    state = editor(g)
                    if not state[-1]["focused"] or [f["value"] for f in state] != [
                        "Mira",
                        "Shah",
                        "Northline Studio",
                        "",
                    ]:
                        raise AssertionError("restored device failed the action")
                    return digest(obs)

                hashes = parallel(kids, act)
                first = time.perf_counter() - t
                samples = [memory(g) for g in kids]
                trials.append(
                    dict(
                        round=r,
                        n=n,
                        ready_s=ready,
                        all_acted_s=first,
                        observations=hashes,
                        emulators=samples,
                    )
                )
                record(
                    args.out,
                    {
                        "checkpoint": node,
                        "source_parent_running": False,
                        "trials": trials,
                    },
                )
                print("fanout", n, round(ready, 2), round(first, 2), flush=True)
            finally:
                p.release(kids)


if __name__ == "__main__":
    main()
