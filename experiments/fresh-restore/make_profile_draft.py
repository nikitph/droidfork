"""Cold-boot a private AVD with the requested CPU and GPU and save the Contacts draft."""

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from droidfork.android import configure
from droidfork.emulator import Guest, Pool, clone_avd, identity, shell
from droidfork.task import prefix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cores", required=True, type=int, choices=range(1, 9))
    parser.add_argument(
        "--gpu", required=True, choices=["host", "swiftshader_indirect"]
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    pool = Pool()
    base = json.loads((pool.root / "base.json").read_text())["node"]
    home = pool.root / "runs" / identity()
    home.mkdir()
    try:
        clone_avd(pool.root / "nodes" / base, home)
        config = home / "DroidFork35.avd/config.ini"
        changed, count = re.subn(
            r"(?m)^hw.cpu.ncore\s*=.*$",
            f"hw.cpu.ncore={args.cores}",
            config.read_text(),
        )
        if count != 1:
            raise ValueError("expected one existing CPU count in the AVD")
        config.write_text(changed)
        port, lease = pool._lease()
        log = pool.root / "logs" / (home.name + ".log")
        with lease, log.open("wb") as out:
            proc = subprocess.Popen(
                [
                    "emulator",
                    "-avd",
                    "DroidFork35",
                    "-port",
                    str(port),
                    "-no-window",
                    "-no-audio",
                    "-no-boot-anim",
                    "-no-snapshot-save",
                    "-no-snapshot-load",
                    "-gpu",
                    args.gpu,
                ],
                env={**os.environ, "ANDROID_AVD_HOME": str(home)},
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=out,
                start_new_session=True,
            )
            with Guest(pool, home, port, lease, proc, log) as guest:
                guest.until_ready()
                if shell(guest.serial, "getconf", "_NPROCESSORS_ONLN") != str(
                    args.cores
                ):
                    raise ValueError("Android CPU count does not match requested AVD")
                configure(guest)
                prefix(guest, ("Mira", "Shah"))
                node = pool.snapshot(
                    guest, f"{args.cores}-vCPU {args.gpu} Contacts draft"
                )
                args.output.write_text(node + "\n")
    finally:
        shutil.rmtree(home, ignore_errors=True)


if __name__ == "__main__":
    main()
