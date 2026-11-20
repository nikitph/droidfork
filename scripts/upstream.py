"""Exercise the upstream ContactsAddContact implementation on an actual parent and two forks."""

import json
from pathlib import Path
import subprocess
from droidfork.emulator import Pool, parallel, record, run
from droidfork.task import prefix, suffix
from droidfork.scenario import NAMES, TARGET


def main():
    p = Pool()
    lab = p.root.parent
    rev = (
        run(["git", "-C", str(lab / "android_world"), "rev-parse", "HEAD"])
        .decode()
        .strip()
    )
    base = json.loads((p.root / "base.json").read_text())["node"]
    pair = NAMES[-1]
    with subprocess.Popen(
        [str(lab / "awenv/bin/python"), "scripts/androidworld_worker.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as worker:

        def call(g, op):
            message = {
                "op": op,
                "serial": g.serial,
                "name": " ".join(pair),
                "number": TARGET,
            }
            worker.stdin.write(json.dumps(message) + "\n")
            worker.stdin.flush()
            reply = worker.stdout.readline()
            if not reply:
                raise RuntimeError("AndroidWorld worker stopped")
            return json.loads(reply)

        try:
            with p.reset(base) as parent:
                goal = call(parent, "init")
                prefix(parent, pair)
                initial = call(parent, "score")
                node = p.snapshot(parent, "upstream-task")
                kids = p.fork(node, 2)
                try:
                    parallel(list(enumerate(kids)), lambda x: suffix(x[1], pair, x[0]))
                    scores = [call(g, "score") for g in kids]
                    parent_after = call(parent, "score")
                    if [initial, *scores, parent_after] != [0, 1, 0, 0]:
                        raise AssertionError("unexpected upstream scores")
                    result = {
                        "upstream_commit": rev,
                        "task": "ContactsAddContact",
                        "instruction": goal,
                        "parent_before": initial,
                        "child_rewards": scores,
                        "parent_after": parent_after,
                        "transport": "AndroidEnv AdbCallParser against each local device",
                    }
                    record(Path("results/androidworld.json"), result)
                    print(result)
                finally:
                    p.release(kids)
        finally:
            worker.stdin.close()


if __name__ == "__main__":
    main()
