"""End-to-end paired Android task benchmark. Every policy action uses the gRPC API."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import platform
import re
import time
from xml.etree import ElementTree as ET
from PIL import Image

from droidfork.client import Pool
from droidfork.scenario import ALTERNATE, NAMES, TARGET, prefix_actions


def now():
    return time.perf_counter()


def together(items, fn):
    with ThreadPoolExecutor(max_workers=len(items)) as e:
        return list(e.map(fn, items))


def terms(obs):
    return [
        n
        for n in ET.fromstring(obs.xml).iter("node")
        if n.get("package") == "com.google.android.contacts"
    ]


def verify_draft(obs, pair):
    texts = {n.get("text") for n in terms(obs)}
    if not {"Northline Studio", *pair, "Save", "Phone"}.issubset(texts):
        raise AssertionError("expected unsaved contact fields in view: " + repr(texts))


def gui(dev, action, timings):
    t = now()
    obs = dev.step(action)
    timings.append({"action": action, "seconds": now() - t, "idle": obs.idle})
    return obs


def prefix(dev, pair, log):
    for action in prefix_actions(pair):
        obs = gui(dev, action, log)
    verify_draft(obs, pair)


def first_action(dev, pair, log):
    obs = gui(dev, {"type": "tap", "label": "Phone"}, log)
    verify_draft(obs, pair)
    if not any(
        n.get("text") == "Phone" and n.get("focused") == "true" for n in terms(obs)
    ):
        raise AssertionError("child did not accept its first action")
    fields = (
        "class",
        "resource-id",
        "text",
        "content-desc",
        "bounds",
        "focused",
        "checked",
        "selected",
    )
    semantic = [tuple(n.get(k, "") for k in fields) for n in terms(obs)]
    return {
        "pixels": hashlib.sha256(
            Image.open(io.BytesIO(obs.png)).convert("RGB").tobytes()
        ).hexdigest(),
        "xml": hashlib.sha256(obs.xml.encode()).hexdigest(),
        "app_tree": hashlib.sha256(json.dumps(semantic).encode()).hexdigest(),
    }


def suffix(dev, pair, i, log):
    number = TARGET if i % 2 == 0 else ALTERNATE
    typed = gui(dev, {"type": "type", "text": number}, log)
    if not any(
        re.sub(r"\D", "", node.get("text", "")) == number for node in terms(typed)
    ):
        raise AssertionError(f"branch {i} phone entry did not reach the GUI")
    saved = gui(dev, {"type": "tap", "label": "Save"}, log)
    name = " ".join(pair)
    t = now()
    # Saving is asynchronous. Only repeat the read-only judge, never replay a Save action.
    while True:
        expected, other = dev.judge(name, TARGET), dev.judge(name, ALTERNATE)
        if expected or other or now() - t >= 8:
            break
        time.sleep(0.1)
    expected_pair = (1, 0) if i % 2 == 0 else (0, 1)
    if (expected, other) != expected_pair:
        current = dev.observe()
        raise AssertionError(
            f"wrong contact reward in branch {i}: {(expected, other)}; "
            f"save activity {saved.activity}; current visible texts {[n.get('text') for n in terms(current) if n.get('text')]}"
        )
    return {
        "chosen_number": number,
        "reward": expected,
        "alternate_reward": other,
        "judge_s": now() - t,
    }


def trial(pool, mode, index, n, workers):
    pair = NAMES[index % len(NAMES)]
    logs = [[] for _ in range(n)]
    stages = {}
    own = []
    parent = None
    shared = []
    start = now()
    try:
        t = now()
        if mode == "fork":
            parent = pool.reset()
            own.append(parent)
            stages["reset_s"] = now() - t
            t = now()
            prefix(parent, pair, shared)
            stages["shared_prefix_s"] = now() - t
            t = now()
            node = parent.snapshot(name="before-phone")
            stages["snapshot_s"] = now() - t
            t = now()
            devs = pool.fork(node, n)
            own.extend(devs)
            stages["fork_rpc_s"] = now() - t
            fork_requested = t
        elif mode == "warm":
            devs = together(workers, lambda d: pool.reset(reuse=d))
            stages["reset_s"] = now() - t
        elif mode == "cold":
            # Independent stock emulators boot the prepared system/user data but ignore the RAM snapshot.
            def launch(_):
                d = pool.reset(cold=True)
                own.append(d)
                return d

            devs = together(range(n), launch)
            stages["reset_s"] = now() - t
        else:
            raise ValueError(mode)
        if mode != "fork":
            t = now()
            together(list(enumerate(devs)), lambda x: prefix(x[1], pair, logs[x[0]]))
            stages["parallel_prefix_s"] = now() - t
        t = now()
        signatures = together(
            list(enumerate(devs)), lambda x: first_action(x[1], pair, logs[x[0]])
        )
        stages["first_action_s"] = now() - t
        if mode == "fork":
            stages["fork_through_all_first_actions_s"] = now() - fork_requested
        t = now()
        outcomes = together(
            list(enumerate(devs)), lambda x: suffix(x[1], pair, x[0], logs[x[0]])
        )
        stages["suffix_and_judge_s"] = now() - t
        if parent and (
            parent.judge(" ".join(pair), TARGET)
            or parent.judge(" ".join(pair), ALTERNATE)
        ):
            raise AssertionError("parent state changed after child save")
        scored = now() - start
    finally:
        t = now()
        if own:
            pool.release(own)
        stages["release_s"] = now() - t
    return {
        "mode": mode,
        "round": index,
        "n": n,
        "name": " ".join(pair),
        "scored_s": scored,
        "total_s": now() - start,
        "stages": stages,
        "outcomes": outcomes,
        "post_action_signatures": signatures,
        "shared_actions": shared,
        "branch_actions": logs,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--address", default="127.0.0.1:54329")
    parser.add_argument("--out", type=Path, default=Path("results/bench.json"))
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--n", type=int, default=2)
    parser.add_argument("--modes", default="cold,warm,fork")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="keep completed trials in the same output file",
    )
    args = parser.parse_args()
    pool = Pool(args.address)
    modes = args.modes.split(",")
    data = {
        "client": platform.platform(),
        "base": pool.info.base,
        "image": pool.info.fingerprint,
        "n": args.n,
        "trials": [],
    }

    if (
        args.rounds < 1
        or args.n < 1
        or set(modes) - {"cold", "warm", "fork"}
        or len(set(modes)) != len(modes)
    ):
        parser.error("invalid rounds, branches or modes")
    if args.resume and args.out.exists():
        previous = json.loads(args.out.read_text())
        if any(previous.get(k) != data[k] for k in ("base", "image", "client", "n")):
            parser.error(
                "resume requires the same base snapshot, client and branch count"
            )
        data = previous
    complete = {(t["round"], t["mode"]) for t in data["trials"]}

    def save():
        tmp = args.out.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        tmp.replace(args.out)

    for r in range(args.rounds):
        for mode in modes[r % len(modes) :] + modes[: r % len(modes)]:
            if (r, mode) in complete:
                continue
            print(f"round {r + 1} {mode}", flush=True)
            workers = []
            try:
                if mode == "warm":
                    t = now()

                    def prewarm(_):
                        d = pool.reset()
                        workers.append(d)

                    together(range(args.n), prewarm)
                    setup = now() - t
                trial_data = trial(pool, mode, r, args.n, workers)
            except Exception as exc:
                data.setdefault("failed_attempts", []).append(
                    {"round": r, "mode": mode, "error": str(exc)}
                )
                save()
                raise
            finally:
                t = now()
                if workers:
                    pool.release(workers)
                retire = now() - t
            if mode == "warm":
                trial_data["prewarm_s"] = setup
                trial_data["retire_warm_pool_s"] = retire
            data["trials"].append(trial_data)
            save()
            print(
                f"finished {trial_data['total_s']:.2f}s, rewards {[o['reward'] for o in trial_data['outcomes']]}",
                flush=True,
            )
    print("Complete", args.out, flush=True)


if __name__ == "__main__":
    main()
