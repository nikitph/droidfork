"""Summarize complete, validated trials. Ratios use sums of real elapsed seconds."""

import json
from pathlib import Path
from statistics import median

OUT = Path(__file__).resolve().parents[1] / "results"
LABELS = {
    "cold": "Cold boot and replay",
    "warm": "Warm rewind and replay",
    "fork": "On-demand machine fork",
}


def read(file):
    return json.loads((OUT / file).read_text())


def main():
    bench = read("bench.json")
    modes = ("cold", "warm", "fork")
    group = {m: [t for t in bench["trials"] if t["mode"] == m] for m in modes}
    rounds = {m: {t["round"] for t in rows} for m, rows in group.items()}
    if len(set(map(frozenset, rounds.values()))) != 1 or not all(group.values()):
        raise ValueError("incomplete paired comparison")
    text = [
        "# Android branching benchmark",
        "",
        "Two Android 15 Contacts rollouts per batch on an 8-core Apple M1 Pro with 16 GiB RAM. Each starts from the same clean fixture. The scripted user opens Contacts, creates an unsaved name and company, and then chooses a phone number. One branch saves the requested number. The counterfactual saves a different number. The judge reads the Contacts Provider outside the GUI action channel.",
        "",
        "| Method | Paired batches | Median batch, s | Range, s | Rollouts per host-hour | Host-seconds per positive reward |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    stats = {}
    for m in modes:
        rows = group[m]
        times = [r["total_s"] for r in rows]
        branches = sum(r["n"] for r in rows)
        positives = sum(o["reward"] for r in rows for o in r["outcomes"])
        stats[m] = {
            "median_s": median(times),
            "range_s": [min(times), max(times)],
            "batches": len(rows),
            "rollouts": branches,
            "positive_rewards": int(positives),
            "total_s": sum(times),
            "rollouts_per_host_hour": 3600 * branches / sum(times),
            "host_seconds_per_success": sum(times) / positives,
        }
        x = stats[m]
        text.append(
            f"| {LABELS[m]} | {len(rows)} | {median(times):.2f} | {min(times):.2f}–{max(times):.2f} | {x['rollouts_per_host_hour']:.1f} | {x['host_seconds_per_success']:.2f} |"
        )
    actions = [
        a
        for t in bench["trials"]
        for group_actions in [t["shared_actions"], *t["branch_actions"]]
        for a in group_actions
    ]
    idle_timeouts = sum(not a["idle"] for a in actions)
    forks = group["fork"]
    warms = group["warm"]
    text.extend(
        [
            "",
            "[Batch timings for every trial](benchmark.png). Throughput is extrapolated from the summed wall time of the measured batches.",
            "",
            "Every timed policy step, including the first tap in each child, returned a screenshot, accessibility tree and foreground activity over gRPC. Timing includes checking the alternate number. The fork and cold paths include launch and release. The warm path measures a running worker pool. Provisioning and retiring that pair cost an additional median "
            f"{median(r['prewarm_s'] + r['retire_warm_pool_s'] for r in warms):.2f} s per batch if done from scratch. At a supplied host price of P per hour, multiply host-seconds per positive reward by P / 3600. The experiment used local hardware and no paid inference.",
            "",
            f"The final series recorded {len(actions)} GUI actions. {idle_timeouts} returned the documented idle timeout. The policy checked the observed fields and the saved provider state. The raw file records {len(bench.get('failed_attempts', []))} failed batch attempts in this series. Development runs preceded the final series.",
            "",
            "## Where the time went",
            "",
            "| Fork path stage | Median, s |",
            "|---|---:|",
        ]
    )
    for k, title in (
        ("reset_s", "Reset parent"),
        ("shared_prefix_s", "Enter shared GUI prefix"),
        ("snapshot_s", "Capture and seal machine checkpoint"),
        ("fork_rpc_s", "Request through all Android package-manager readiness checks"),
        ("first_action_s", "Tap Phone in every child and receive observations"),
        (
            "fork_through_all_first_actions_s",
            "Request through every first verified GUI action, including observations",
        ),
        ("suffix_and_judge_s", "Enter numbers, save and score"),
        ("release_s", "Release devices"),
    ):
        text.append(f"| {title} | {median(r['stages'][k] for r in forks):.2f} |")
    text.extend(
        [
            "",
            "The request-to-first-action row overlaps the preceding two rows. Independent workers replay their prefixes in parallel.",
            "",
            "## Isolation and fidelity",
            "",
        ]
    )
    function = read("functional.json")
    aw = read("androidworld.json")
    text.append(
        f"The provider returned the requested number in one child and the alternate number in its sibling. The unsaved parent retained {len(function['parent_rows'])} persisted rows. Restoring a snapshot of the alternate child retained its own saved contact. The upstream AndroidWorld initializer and verifier independently returned {aw['child_rewards']} for the children and {aw['parent_after']} for the parent."
    )
    text += [
        "",
        "[Parent draft](evidence/parent-draft.png) · [Correct child](evidence/child-0-saved.png) · [Counterfactual child](evidence/child-1-saved.png) · [Upstream verifier results](androidworld.json)",
        "",
    ]
    for key, title in (
        ("app_tree", "App accessibility signature"),
        ("xml", "Complete accessibility XML"),
        ("pixels", "Exact screenshot pixels"),
    ):
        matches = sum(
            len({v[key] for v in r["post_action_signatures"]}) == 1 for r in forks
        )
        text.append(
            f"- {title} matched across both children immediately after the same tap in {matches}/{len(forks)} timed fork batches."
        )
    app = read("app-benchmark.json")["trials"]
    sizes = sorted({r["db_bytes"] for r in app})
    size_label = f"{sizes[0]:,}" if len(sizes) == 1 else f"{sizes[0]:,}–{sizes[-1]:,}"
    text += [
        "",
        "## App checkpoint measurements",
        "",
        f"Across {len(app)} drafts in one app, {sum(a['form_and_provider_match'] and a['reward'] == 1 for a in app)}/{len(app)} restores retained the pre-existing contact, erased the subsequently saved counterfactual, reconstructed the unsaved fields and focus, and completed the task with the requested number. Median capture was {median(a['snapshot_s'] for a in app):.2f} s. Median warm restore was {median(a['restore_s'] for a in app):.2f} s. Database blobs occupied {size_label} bytes each. The five manifests referenced {len({a['database_sha256'] for a in app})} distinct database blobs and occupied {min(a['manifest_bytes'] for a in app)}–{max(a['manifest_bytes'] for a in app)} bytes. Full-screen pixels matched exactly in {sum(a['before']['pixels'] == a['after']['pixels'] for a in app)}/{len(app)} cases.",
        "",
        "## Fan-out from a saved checkpoint",
        "",
        "These runs keep the source parent offline. Each trial creates all children concurrently, taps Phone, verifies the inherited fields and focus, and records the operating system's process memory charge. The physical footprint covers an entire emulator process, including graphics and runtime components.",
        "",
        "| Live children | Trials | Median ready, s | Median all acted, s | Median process footprint per child, MiB |",
        "|---:|---:|---:|---:|---:|",
    ]
    fan = read("fanout.json")["trials"]
    for n in sorted({r["n"] for r in fan}):
        rows = [r for r in fan if r["n"] == n]
        fp = [
            e["physical_footprint_bytes"] / 2**20
            for r in rows
            for e in r["emulators"]
            if "physical_footprint_bytes" in e
        ]
        text.append(
            f"| {n} | {len(rows)} | {median(r['ready_s'] for r in rows):.2f} | {median(r['all_acted_s'] for r in rows):.2f} | {median(fp):,.0f} |"
        )
    text += [
        "",
        "## Interpretation",
        "",
        f"The stock-emulator fork delivered {stats['fork']['rollouts_per_host_hour'] / stats['cold']['rollouts_per_host_hour']:.2f}× the throughput of cold replay and {stats['fork']['rollouts_per_host_hour'] / stats['warm']['rollouts_per_host_hour']:.2f}× the throughput of warm replay on this workload. These are small-sample, single-machine observations. The spec's sub-second 16-child goal, 32-way ≥99% screen-and-tree match and Linux/KVM host density remain unverified. The strict equality counts above include status-bar and blinking-cursor changes.",
        "",
        "A separate experiment enabled the emulator's QuickbootFileBacked feature and started a child with a hard link to a sealed 2.5 GiB RAM image. The emulator reported loading the snapshot in 112 ms. Android stayed offline through the 150 s readiness deadline, so this experiment did not establish usable shared guest RAM. The timings above use the working native snapshot backend.",
        "",
        "[Raw gRPC trials](bench.json) · [App trials](app-benchmark.json) · [Fan-out and memory](fanout.json) · [Functional test](functional.json) · [Host and image](host.json)",
        "",
    ]
    (OUT / "REPORT.md").write_text("\n".join(text))
    print(json.dumps(stats, indent=2))


if __name__ == "__main__":
    main()
