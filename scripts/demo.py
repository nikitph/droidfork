"""Run one complete live branch experiment with automatic local service lifecycle."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from . import prepare
from .benchmark import trial
from droidfork.client import Pool
from droidfork.emulator import DEFAULT_ROOT


def main():
    if not (DEFAULT_ROOT / "base.json").is_file():
        prepare.main()
    with tempfile.TemporaryDirectory() as tmp:
        ready = Path(tmp) / "port"
        log_path = DEFAULT_ROOT / "logs" / ("demo-" + Path(tmp).name + ".log")
        with log_path.open("w") as log:
            service = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "droidfork.service",
                    "--port",
                    "0",
                    "--ready-file",
                    str(ready),
                ],
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        try:
            for _ in range(200):
                if ready.exists() and ready.read_text():
                    break
                if service.poll() is not None:
                    raise RuntimeError(f"service exited; see {log_path}")
                time.sleep(0.05)
            p = Pool("127.0.0.1:" + ready.read_text())
            result = trial(p, "fork", 0, 2, [])
            print(
                json.dumps(
                    {
                        "task": "Save Mira Shah with telephone 2025550137",
                        "total_s": result["total_s"],
                        "timings": result["stages"],
                        "children": result["outcomes"],
                    },
                    indent=2,
                )
            )
        finally:
            service.terminate()
            service.wait(timeout=90)


if __name__ == "__main__":
    main()
