"""Draw a paired batch timing chart from the emitted benchmark data."""

import json
from pathlib import Path
from statistics import median

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT = Path(__file__).resolve().parents[1] / "results"


def main():
    rows = json.loads((OUT / "bench.json").read_text())["trials"]
    fig, ax = plt.subplots(figsize=(8.2, 3.8), constrained_layout=True)
    modes = ("warm", "fork", "cold")
    labels = ("Warm rewind + replay", "Machine snapshot + fork", "Cold boot + replay")
    ax.set_title(
        "Two Android contact rollouts from a common start",
        loc="left",
        fontsize=15,
        pad=18,
    )
    all_seconds = [r["total_s"] for r in rows]
    for y, (mode, label) in enumerate(zip(modes, labels)):
        elapsed = [r["total_s"] for r in rows if r["mode"] == mode]
        color = "#23615e" if mode == "fork" else "#637387"
        ax.plot([min(elapsed), max(elapsed)], [y, y], color=color, linewidth=2)
        for i, value in enumerate(elapsed):
            ax.scatter(
                value,
                y + (i - (len(elapsed) - 1) / 2) * 0.055,
                s=28,
                color=color,
                zorder=3,
            )
        ax.text(
            1.015,
            y,
            f"{median(elapsed):.1f} s",
            va="center",
            ha="left",
            transform=ax.get_yaxis_transform(),
            fontsize=10,
            color="#243442",
        )
    ax.set_yticks(range(len(labels)), labels)
    ax.set_ylim(2.5, -0.6)
    ax.set_xlim(0, max(all_seconds) * 1.10)
    ax.set_xlabel("Elapsed seconds per two-branch batch")
    ax.set_axisbelow(True)
    ax.grid(axis="x", alpha=0.2)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0, pad=8)
    fig.text(
        0.01,
        -0.065,
        "Each dot is one measured batch. Values at right are medians. Warm workers are already running.",
        fontsize=9,
        color="#444",
    )
    fig.savefig(OUT / "benchmark.png", dpi=170, bbox_inches="tight", facecolor="white")
    plt.close(fig)


if __name__ == "__main__":
    main()
