"""Compare the old and optimized paths in alternating order on the same checkpoint."""

import argparse
import fcntl
import json
import shutil
from pathlib import Path
from xml.etree import ElementTree

import benchmark as bench

from droidfork.emulator import Pool


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("fixture", "control", "optimized", "kernel", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--rounds", type=int, default=5)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    evidence = args.output / "evidence"
    fixture = json.loads(args.fixture.read_text())
    pool = Pool()
    rows = []
    best = {}
    phases = [
        ("control", "direct-map", args.control, None),
        ("optimized", "parallel-direct-map", args.optimized, args.kernel),
    ]
    lock = Path(fixture["path"]).parent / "benchmark.lock"

    with lock.open("a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for i in range(args.rounds):
            for name, mode, engine, kernel in phases[:: 1 if i % 2 == 0 else -1]:
                row = bench.trial(
                    pool, fixture, str(engine), mode, evidence, i == 0, kernel
                )
                row.update(configuration=name, round=i + 1)
                rows.append(row)
                print(json.dumps(row), flush=True)
                if "error" not in row and row["verified_s"] < best.get(
                    name, float("inf")
                ):
                    best[name] = row["verified_s"]
                    for suffix in (".png", ".xml", "-before.png"):
                        src = evidence / (mode + suffix)
                        if src.exists():
                            shutil.copyfile(src, evidence / (name + "-best" + suffix))
                (args.output / "paired.json").write_text(
                    json.dumps(rows, indent=2) + "\n"
                )
        with bench.launch(
            pool, fixture["node"], fixture["path"], shutil.which("emulator"), {}
        ) as parent:
            parent.until_ready(pool.meta(fixture["node"]))
            if bench.contact_rows(parent):
                raise ValueError("saved parent contains child writes")

            tree = ElementTree.fromstring(bench.hierarchy(parent))
            if not any(
                n.get("focused") == "true" and n.get("text") == "Northline Studio"
                for n in tree.iter("node")
            ):
                raise ValueError("parent focus changed")
            (args.output / "isolation.json").write_text(
                json.dumps({"parent_empty": True, "parent_company_focused": True})
                + "\n"
            )


if __name__ == "__main__":
    main()
