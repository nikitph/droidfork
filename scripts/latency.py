"""Time real trainer RPCs through a verified focus change in a live Contacts draft."""

import argparse
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from xml.etree import ElementTree as ET

import grpc
from PIL import Image

from droidfork.client import Pool as Client
from droidfork.emulator import Pool, console, record
from droidfork.rpc import api_pb2_grpc
from droidfork.service import Service
from scripts.benchmark import prefix, suffix, together


def verified_tap(device, label):
    start = time.perf_counter()
    obs = device.step({"type": "tap", "label": label})
    assert "ContactEditorActivity" in obs.activity
    pixels = verified_focus(obs.xml, obs.png, label)
    return dict(
        seconds=time.perf_counter() - start, label=label, pixels=pixels, idle=obs.idle
    )


def verified_focus(xml, png, label):
    nodes = list(ET.fromstring(xml).iter("node"))
    texts = {n.get("text") for n in nodes}
    assert {"Mira", "Shah", "Northline Studio", "Phone", "Save"} <= texts
    assert any(n.get("text") == label and n.get("focused") == "true" for n in nodes)
    return verified_underlines(xml, png, label)


def verified_underlines(xml, png, label):
    nodes = list(ET.fromstring(xml).iter("node"))
    with Image.open(BytesIO(png)) as image:
        assert image.format == "PNG" and image.size == (480, 854)
        rgb = image.convert("RGB")
        # On this pinned Contacts theme the active field has a blue underline.
        # Check its presence on the target and its absence on the other field.
        fields = {
            n.get("hint"): n
            for n in nodes
            if n.get("class") == "android.widget.EditText"
        }
        for field in ("Company", "Phone"):
            node = fields[field]
            x0, _, x1, bottom = map(int, re.findall(r"-?\d+", node.get("bounds")))
            colors = [
                rgb.getpixel(((x0 + x1) // 2, y)) for y in range(bottom - 6, bottom)
            ]
            blue = any(b - r > 48 and b - g > 32 for r, g, b in colors)
            if blue != (node.get("text") == label):
                raise AssertionError(
                    f"returned frame has stale focus for {label}: {colors}"
                )
        pixels = hashlib.sha256(rgb.tobytes()).hexdigest()
    return pixels


def verify_company_focused(device):
    obs = device.observe()
    assert any(
        n.get("text") == "Northline Studio" and n.get("focused") == "true"
        for n in ET.fromstring(obs.xml).iter("node")
    )


def cohort(client, service, node, n, parked):
    start = time.perf_counter()
    kids = client.fork(node, n)
    timing = {"ready_s": time.perf_counter() - start}
    try:
        if parked:

            def freeze(device):
                verify_company_focused(device)
                with service.guest(device.id) as guest:
                    console(guest.serial, "avd", "stop")
                    assert "stopped" in " ".join(console(guest.serial, "avd", "status"))

            together(kids, freeze)
            timing = {"preparation_s": time.perf_counter() - start}
            start = time.perf_counter()

        def act(device):
            if parked:
                with service.guest(device.id) as guest:
                    console(guest.serial, "avd", "start")
            return verified_tap(device, "Phone")

        taps = together(kids, act)
        elapsed = time.perf_counter() - start
        outcomes = together(
            list(enumerate(kids)),
            lambda item: suffix(item[1], ("Mira", "Shah"), item[0], []),
        )
        return dict(n=n, **timing, total_s=elapsed, taps=taps, outcomes=outcomes)
    finally:
        client.release(kids)


def run(args):
    backend = Pool(experimental_feature=args.feature)
    service = Service(backend, 4)
    server = grpc.server(ThreadPoolExecutor(max_workers=8))
    api_pb2_grpc.add_EnvironmentsServicer_to_server(service, server)
    port = server.add_insecure_port("127.0.0.1:0")
    server.start()
    client = Client(f"127.0.0.1:{port}")
    results = {"warm_taps": [], "forks": [], "rewinds": [], "parked": []}
    try:
        if args.checkpoint:
            node = args.checkpoint
        else:
            # Checkpoints encode emulator features. Build a matched fixture from disk.
            parent = client.reset(cold=bool(args.feature))
            try:
                prefix(parent, ("Mira", "Shah"), [])
                start = time.perf_counter()
                node = parent.snapshot(name="verified tap benchmark")
                results["capture_s"] = time.perf_counter() - start
            finally:
                client.release([parent])
        results["checkpoint"] = node
        dev = client.fork(node, 1)[0]
        try:
            verify_company_focused(dev)
            for idx in range(args.taps):
                label = "Phone" if idx % 2 == 0 else "Northline Studio"
                item = verified_tap(dev, label)
                if results["warm_taps"]:
                    assert item["pixels"] != results["warm_taps"][-1]["pixels"]
                results["warm_taps"].append(item)
            for _ in range(args.rewinds):
                start = time.perf_counter()
                client.reset(node, reuse=dev)
                restored = time.perf_counter()
                tap = verified_tap(dev, "Phone")
                results["rewinds"].append(
                    dict(
                        ready_s=restored - start,
                        total_s=time.perf_counter() - start,
                        tap=tap,
                    )
                )
        finally:
            client.release([dev])
        for kind, rounds in (("forks", args.rounds), ("parked", args.parked)):
            for _ in range(rounds):
                for n in args.counts:
                    batch = cohort(client, service, node, n, parked=kind == "parked")
                    results[kind].append(batch)
                    print(kind, n, round(batch["total_s"], 3), flush=True)
                    record(args.out, results)
    finally:
        server.stop(0).wait()
        client.channel.close()
        record(args.out, results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--checkpoint", help="Existing machine checkpoint of the Mira Shah draft"
    )
    parser.add_argument(
        "--feature",
        choices=["OnDemandSnapshotLoad", "SnapshotAdb"],
        help="Experiment with a matched emulator feature at both capture and restore",
    )
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--taps", type=int, default=30)
    parser.add_argument("--rewinds", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument(
        "--parked",
        type=int,
        default=0,
        help="Extra trials with children restored and frozen before the timer",
    )
    parser.add_argument("--counts", type=int, nargs="+", default=[1, 2])
    run(parser.parse_args())
