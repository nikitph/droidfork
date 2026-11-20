"""Local stock Android Emulator backend. Mutable guests and sealed checkpoints have separate AVD homes."""

from __future__ import annotations

import atexit
import concurrent.futures
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import uuid

NAME = "DroidFork35"
IMAGE = "system-images;android-35;google_apis;arm64-v8a"
DEFAULT_ROOT = Path(__file__).resolve().parents[1] / ".lab" / "pool"


def run(args, *, timeout=90, env=None):
    deadline = time.monotonic() + timeout
    while True:
        p = subprocess.run(
            args,
            capture_output=True,
            timeout=max(0.1, deadline - time.monotonic()),
            env=env,
        )
        # The ADB server can briefly forget a serial while adbd reconnects after restoration.
        # These transport-selection errors happen before command dispatch, so replay is safe.
        unavailable = b"device offline" in p.stderr or re.search(
            rb"adb: device 'emulator-[0-9]+' not found", p.stderr
        )
        if args[0] != "adb" or not unavailable or time.monotonic() + 0.2 >= deadline:
            break
        time.sleep(0.2)
    if p.returncode:
        raise RuntimeError(
            f"{args[0]} exited {p.returncode}: {(p.stderr or p.stdout).decode(errors='replace')[-1500:]}"
        )
    return p.stdout


def shell(serial, *args, timeout=30):
    return (
        run(["adb", "-s", serial, "shell", shlex.join(map(str, args))], timeout=timeout)
        .decode()
        .strip()
    )


def console(serial, *args, timeout=90):
    out = run(["adb", "-s", serial, "emu", *args], timeout=timeout).decode().strip()
    if not out.endswith("OK") or "KO:" in out:
        raise RuntimeError(out)
    return out.splitlines()[:-1]


def identity():
    return uuid.uuid4().hex


def record(path, payload):
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def clone_file(source, dest):
    # Refuse a silent deep copy. The benchmark is explicitly for a CoW filesystem.
    if platform.system() == "Darwin":
        import ctypes

        libc = ctypes.CDLL(None, use_errno=True)
        if libc.clonefile(os.fsencode(source), os.fsencode(dest), 0) < 0:
            e = ctypes.get_errno()
            raise OSError(e, os.strerror(e), str(source))
    else:
        run(["cp", "--reflink=always", str(source), str(dest)])
    return str(dest)


def avd_ini(home):
    (home / (NAME + ".ini")).write_text(
        f"avd.ini.encoding=UTF-8\npath={home}/{NAME}.avd\ntarget=android-35\n"
    )


def clone_avd(source, dest):
    def ignore(_directory, entries):
        return {
            n
            for n in entries
            if n.endswith(".lock")
            or n in {"emu-launch-params.txt", "read-snapshot.txt"}
        }

    shutil.copytree(
        source / (NAME + ".avd"),
        dest / (NAME + ".avd"),
        copy_function=clone_file,
        ignore=ignore,
    )
    avd_ini(dest)


def parallel(items, fn):
    if not items:
        return []
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(items)) as executor:
        return list(executor.map(fn, items))


class Guest:
    def __init__(self, pool, home, port, lease, proc, log):
        self.pool, self.home, self.port = pool, home, port
        self.lease, self.proc, self.log = lease, proc, log
        self.serial = f"emulator-{port}"
        self.lock = threading.RLock()
        self.ui = None
        self.restored_from = None
        self.restore_incomplete = False

    def until_ready(self, meta=None):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"emulator exited. See {self.log}")
            try:
                if (
                    shell(self.serial, "getprop", "sys.boot_completed", timeout=3)
                    == "1"
                ):
                    if meta:
                        loaded = console(self.serial, "avd", "snapshot", "get")
                        marker = shell(self.serial, "getprop", "debug.droidfork.node")
                        if meta["tag"] not in loaded or marker != meta["id"]:
                            raise RuntimeError(
                                f"snapshot identity mismatch on {self.serial}: {loaded}, {marker}"
                            )
                    if "package:" in shell(
                        self.serial,
                        "pm",
                        "path",
                        "com.google.android.contacts",
                        timeout=4,
                    ):
                        self.restored_from = meta["id"] if meta else None
                        return
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                if "identity mismatch" in str(e):
                    raise
            time.sleep(0.15)
        raise TimeoutError(f"Android failed readiness on {self.serial}. See {self.log}")

    def detach_observer(self):
        # A live adb shell is a host connection and cannot be inherited across a VM snapshot.
        observer, self.ui = self.ui, None
        if observer is not None:
            observer.stop_uiautomator(wait=observer._process is not None)
            atexit.unregister(observer.stop_uiautomator)

    def close(self):
        with self.lock:
            if self.lease is None:
                return
            try:
                if self.proc.poll() is None:
                    if self.ui is not None:
                        atexit.unregister(self.ui.stop_uiautomator)
                    try:
                        console(self.serial, "kill", timeout=3)
                        self.proc.wait(timeout=8)
                    except (RuntimeError, subprocess.TimeoutExpired):
                        self.proc.terminate()
                        try:
                            self.proc.wait(timeout=8)
                        except subprocess.TimeoutExpired:
                            self.proc.kill()
                            self.proc.wait()
                shutil.rmtree(self.home)
            finally:
                self.lease.close()
                self.lease = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class Pool:
    def __init__(self, root=DEFAULT_ROOT, *, experimental_feature=None):
        if experimental_feature not in (None, "OnDemandSnapshotLoad", "SnapshotAdb"):
            raise ValueError("unknown experimental emulator feature")
        self.experimental_feature = experimental_feature
        self.root = Path(root).resolve()
        for name in ("nodes", "runs", "logs", "objects"):
            (self.root / name).mkdir(parents=True, exist_ok=True)

    def meta(self, node):
        if not re.fullmatch(r"[a-f0-9]{32}", node):
            raise ValueError("invalid machine snapshot id")
        return json.loads((self.root / "nodes" / node / "meta.json").read_text())

    def _lease(self):
        for port in range(5554, 5634, 2):
            lease_path = (
                Path(tempfile.gettempdir()) / f"droidfork-{os.getuid()}-{port}.lock"
            )
            lease = lease_path.open("a")
            try:
                fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for test in (port, port + 1):
                    with socket.socket() as s:
                        s.bind(("127.0.0.1", test))
                return port, lease
            except OSError:
                lease.close()
        raise RuntimeError("no free emulator console port")

    def _start(self, home, meta=None):
        port, lease = self._lease()
        log = self.root / "logs" / (home.name + ".log")
        args = [
            "emulator",
            "-avd",
            NAME,
            "-port",
            str(port),
            "-no-window",
            "-no-audio",
            "-no-boot-anim",
            "-no-snapshot-save",
            "-gpu",
            "swiftshader_indirect",
        ]
        if self.experimental_feature:
            args.extend(["-feature", self.experimental_feature])
        args += ["-snapshot", meta["tag"]] if meta else ["-no-snapshot-load"]
        try:
            with log.open("wb") as out:
                proc = subprocess.Popen(
                    args,
                    env={**os.environ, "ANDROID_AVD_HOME": str(home)},
                    stdin=subprocess.DEVNULL,
                    stdout=out,
                    stderr=out,
                    start_new_session=True,
                )
        except BaseException:
            lease.close()
            raise
        g = Guest(self, home, port, lease, proc, log)
        try:
            g.until_ready(meta)
            return g
        except BaseException:
            g.close()
            raise

    def fresh(self):
        home = self.root / "runs" / identity()
        home.mkdir()
        try:
            run(
                [
                    "avdmanager",
                    "create",
                    "avd",
                    "-n",
                    NAME,
                    "-p",
                    str(home / (NAME + ".avd")),
                    "-k",
                    IMAGE,
                    "-d",
                    "pixel_2",
                ],
                env={**os.environ, "ANDROID_AVD_HOME": str(home)},
            )
            p = home / (NAME + ".avd") / "config.ini"
            props = dict(
                tuple(s.strip() for s in line.split("=", 1))
                for line in p.read_text().splitlines()
                if "=" in line
            )
            props.update(
                {
                    "hw.ramSize": "1536",
                    "hw.cpu.ncore": "2",
                    "hw.lcd.width": "480",
                    "hw.lcd.height": "854",
                    "hw.lcd.density": "200",
                    "hw.keyboard": "yes",
                    "hw.camera.back": "none",
                    "hw.camera.front": "none",
                    "hw.sdCard": "no",
                    "showDeviceFrame": "no",
                    "skin.dynamic": "yes",
                    "skin.name": "480x854",
                    "skin.path": "_no_skin",
                }
            )
            p.write_text("".join(f"{k}={v}\n" for k, v in props.items()))
            return self._start(home)
        except BaseException:
            shutil.rmtree(home, ignore_errors=True)
            raise

    def snapshot(self, g, name="checkpoint"):
        with g.lock:
            node = identity()
            tag = "df_" + node[:16]
            node_dir = self.root / "nodes" / node
            stage = self.root / "nodes" / ("." + node)
            stage.mkdir()
            try:
                from .android import prepare_snapshot_observer

                prepare_snapshot_observer(g)
                shell(g.serial, "setprop", "debug.droidfork.node", node)
                console(g.serial, "avd", "snapshot", "save", tag)
                console(g.serial, "avd", "stop")
                try:
                    clone_avd(g.home, stage)
                finally:
                    console(g.serial, "avd", "start")
                meta = dict(
                    id=node, name=name, tag=tag, parent=g.restored_from, kind="machine"
                )
                record(stage / "meta.json", meta)
                stage.rename(node_dir)
                g.restored_from = node
                return node
            finally:
                shutil.rmtree(stage, ignore_errors=True)

    def reset(self, node, *, cold=False):
        meta = self.meta(node)
        home = self.root / "runs" / identity()
        home.mkdir()
        try:
            clone_avd(self.root / "nodes" / node, home)
            guest = self._start(home, None if cold else meta)
            if cold:
                try:
                    # Reapply runtime-only device settings lost during a stock cold boot.
                    for command in (
                        ("input", "keyevent", "KEYCODE_WAKEUP"),
                        ("wm", "dismiss-keyguard"),
                        ("svc", "wifi", "disable"),
                        ("svc", "data", "disable"),
                    ):
                        shell(guest.serial, *command)
                    for package in (
                        "com.google.android.inputmethod.latin",
                        "com.google.android.tts",
                    ):
                        shell(
                            guest.serial, "pm", "disable-user", "--user", "0", package
                        )
                except BaseException:
                    guest.close()
                    raise
            return guest
        except BaseException:
            shutil.rmtree(home, ignore_errors=True)
            raise

    def fork(self, node, n):
        if not 1 <= n <= 40:
            raise ValueError("fork count must be 1..40")
        made = []
        lock = threading.Lock()

        def one(_):
            guest = self.reset(node)
            with lock:
                made.append(guest)
            return guest

        try:
            return parallel(range(n), one)
        except BaseException:
            self.release(made)
            raise

    def rewind(self, guest, node):
        """Stock emulator baseline: reload the named snapshot already on this guest's disks."""
        with guest.lock:
            meta = self.meta(node)
            if not (
                guest.home / (NAME + ".avd") / "snapshots" / meta["tag"] / "snapshot.pb"
            ).is_file():
                raise ValueError(
                    "stock rewind requires a snapshot present on this device"
                )
            guest.detach_observer()
            guest.restore_incomplete = True
            console(guest.serial, "avd", "snapshot", "load", meta["tag"])
            guest.ui = None
            guest.until_ready(meta)
            guest.restore_incomplete = False

    @staticmethod
    def release(guests):
        parallel(guests, lambda g: g.close())
