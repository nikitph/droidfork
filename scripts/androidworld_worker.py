"""Pinned AndroidWorld environment's task initializer and scorer, using AndroidEnv's real ADB parser."""

import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from android_env.components import adb_call_parser, adb_controller, config_classes
from android_world.task_evals.single.contacts import ContactsAddContact

ROOT = Path(__file__).resolve().parents[1] / ".lab" / "pool" / "runs"


def environment(serial):
    path = subprocess.check_output(
        ["adb", "-s", serial, "emu", "avd", "path"], text=True
    ).splitlines()[0]
    if not Path(path).resolve().is_relative_to(ROOT):
        raise ValueError(
            "AndroidWorld operations require an owned disposable DroidFork guest"
        )
    config = config_classes.AdbControllerConfig(
        adb_path=shutil.which("adb"), device_name=serial
    )
    parser = adb_call_parser.AdbCallParser(adb_controller.AdbController(config))
    return SimpleNamespace(
        controller=SimpleNamespace(execute_adb_call=parser.parse), interaction_cache=""
    )


def main():
    task = None
    for line in sys.stdin:
        msg = json.loads(line)
        env = environment(msg["serial"])
        if msg["op"] == "init":
            subprocess.run(
                ["adb", "-s", msg["serial"], "root"], check=True, stdout=sys.stderr
            )
            subprocess.run(
                ["adb", "-s", msg["serial"], "wait-for-device"], check=True, timeout=30
            )
            task = ContactsAddContact({"name": msg["name"], "number": msg["number"]})
            task.initialize_task(env)
            result = task.goal
        else:
            result = task.is_successful(env)
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
