"""Verify the published touchscreen summary and the fastest child's two frames."""

import json
from pathlib import Path
from statistics import median

from scripts.latency import verified_focus, verified_underlines


def main():
    if not __debug__:
        raise RuntimeError("Run with ordinary Python assertion checking enabled.")
    root = Path(__file__).resolve().parents[1] / "results" / "fresh-profile"
    records = json.loads((root / "host-fixed-adb.json").read_text())
    runs = [r for r in records if r.get("mode") == "touch-direct-map"]
    if len(runs) != 6 or any("error" in r for r in runs):
        raise ValueError("Expected six passing native touchscreen trials.")
    fastest = min(runs, key=lambda r: r["verified_s"])
    evidence = root / "evidence" / "host-fixed-adb"
    stem = "touch-direct-map-best"
    xml = (evidence / (stem + ".xml")).read_text()
    before = (evidence / (stem + "-before.png")).read_bytes()
    after = (evidence / (stem + ".png")).read_bytes()
    if verified_underlines(xml, before, "Northline Studio") != fastest["initial_pixels_sha256"]:
        raise ValueError("Initial frame does not match the fastest trace.")
    if verified_focus(xml, after, "Phone") != fastest["pixels_sha256"]:
        raise ValueError("Final frame does not match the fastest trace.")
    print(f"{len(runs)}/{len(runs)} passed; best {fastest['verified_s']:.3f} s; "
          f"median {median(r['verified_s'] for r in runs):.3f} s. "
          "Both frames match the fastest trace.")


if __name__ == "__main__":
    main()
