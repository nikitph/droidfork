"""Measure a freshly launched Android emulator through a host-verified tap."""

import argparse
import contextlib
import ctypes
import fcntl
import hashlib
import json
import mmap
import os
import re
import shutil
import socket
import struct
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from droidfork.android import contact_rows, hierarchy, select
from droidfork.emulator import Guest, Pool, clone_avd, identity, shell
from scripts.latency import verified_focus, verified_underlines


@contextlib.contextmanager
def launch(pool, node, path, binary, flags):
    home = pool.root / "runs" / identity()
    home.mkdir()
    log = pool.root / "logs" / (home.name + ".log")
    lease = None
    try:
        clone_avd(pool.root / "nodes" / node, home)
        port, lease = pool._lease()
        args = [
            binary,
            "-avd",
            "DroidFork35",
            "-port",
            str(port),
            "-no-window",
            "-no-audio",
            "-no-boot-anim",
            "-no-snapshot-save",
            "-debug",
            "time",
            "-gpu",
            flags.get("DROIDFORK_GPU", "swiftshader_indirect"),
            "-snapshot",
            pool.meta(node)["tag"],
            "-unix-pipe",
            str(path),
        ]
        env = {k: v for k, v in os.environ.items() if not k.startswith("DROIDFORK_")}
        if flags.get("DROIDFORK_DIRECT"):
            sdk = Path(binary).resolve().parent
            args[0] = str(sdk / "qemu/darwin-aarch64/qemu-system-aarch64-headless")
            env.update(
                ANDROID_EMULATOR_LAUNCHER_DIR=str(sdk),
                ANDROID_EMU_DISABLE_CRASHHANDLER="1",
                DYLD_LIBRARY_PATH=":".join(
                    str(sdk / "lib64" / d) for d in ("vulkan", "gles_angle", ".")
                ),
            )
            args.append("-no-metrics")
            if "DROIDFORK_BOOT_KERNEL" in flags:
                args.extend(["-kernel", flags["DROIDFORK_BOOT_KERNEL"]])
        with log.open("wb") as out:
            profile = os.environ.get("DF_PROFILE")
            proc = subprocess.Popen(
                args,
                env={**env, **flags, "ANDROID_AVD_HOME": str(home)},
                stdout=subprocess.PIPE if profile else out,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            if profile:
                baseline = time.perf_counter()

                def drain():
                    with log.open("wb") as traced:
                        for line in proc.stdout:
                            traced.write(
                                f"{time.perf_counter() - baseline:.6f} ".encode() + line
                            )
                            traced.flush()

                threading.Thread(target=drain, daemon=True).start()
        with Guest(pool, home, port, lease, proc, log) as guest:
            yield guest
    finally:
        if lease is not None:
            lease.close()
        shutil.rmtree(home, ignore_errors=True)


@contextlib.contextmanager
def resident_source(pool, fixture, enabled):
    """Optionally pin only the immutable saved RAM file, with no child or hypervisor."""
    if not enabled:
        yield
        return
    node = fixture["node"]
    path = (
        pool.root
        / "nodes"
        / node
        / "DroidFork35.avd"
        / "snapshots"
        / pool.meta(node)["tag"]
        / "droidfork-main.raw"
    )
    libc = ctypes.CDLL(None, use_errno=True)
    libc.mmap.argtypes = [
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_longlong,
    ]
    libc.mmap.restype = ctypes.c_void_p
    for fn in (libc.mlock, libc.munlock, libc.munmap):
        fn.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    with path.open("rb") as backing:
        size = os.fstat(backing.fileno()).st_size
        address = libc.mmap(
            None, size, mmap.PROT_READ, mmap.MAP_SHARED, backing.fileno(), 0
        )
        if address == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_errno(), "mmap sealed RAM")
        try:
            if libc.mlock(address, size):
                raise OSError(ctypes.get_errno(), "mlock sealed RAM")
            try:
                yield
            finally:
                libc.munlock(address, size)
        finally:
            libc.munmap(address, size)


@contextlib.contextmanager
def anonymous_source(pool, fixture, enabled):
    if not enabled:
        yield None
        return
    node = fixture["node"]
    raw = (
        pool.root
        / "nodes"
        / node
        / "DroidFork35.avd"
        / "snapshots"
        / pool.meta(node)["tag"]
        / "droidfork-main.raw"
    )
    libc = ctypes.CDLL(None, use_errno=True)
    # ARM64 Darwin passes the variadic mode on the stack.
    libc.shm_open.argtypes = [ctypes.c_char_p, ctypes.c_int]
    name = ("/df-" + identity()[:18]).encode()
    fd = libc.shm_open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o400)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "shm_open checkpoint")
    try:
        with raw.open("rb") as src, os.fdopen(fd, "wb") as dest:
            size = os.fstat(src.fileno()).st_size
            os.ftruncate(dest.fileno(), size)
            with mmap.mmap(dest.fileno(), size, access=mmap.ACCESS_WRITE) as mapping:
                view = memoryview(mapping)
                try:
                    if src.readinto(view) != size:
                        raise EOFError("short checkpoint")
                    original = hashlib.sha256(view).digest()
                finally:
                    view.release()
        yield name.decode()
        ro = libc.shm_open(name, os.O_RDONLY, 0)
        if ro < 0:
            raise OSError(ctypes.get_errno(), "reopen sealed checkpoint")
        try:
            with mmap.mmap(ro, size, access=mmap.ACCESS_READ) as mapping:
                if hashlib.sha256(mapping).digest() != original:
                    raise RuntimeError("shared source changed")
        finally:
            os.close(ro)
    finally:
        libc.shm_unlink(name)


def prepare(pool, args):
    base = args.prepare
    directory = Path("/tmp") / ("df-tap-" + identity()[:10])
    directory.mkdir(mode=0o700)
    path = directory / "control"
    with launch(pool, base, path, args.stock, {"DROIDFORK_GPU": args.gpu}) as guest:
        guest.until_ready(pool.meta(base))
        layout = hierarchy(guest)
        point = select(layout, "Phone")
        if not point:
            raise ValueError("expected the unsaved Contacts draft")
        guest.detach_observer()
        for line in shell(guest.serial, "ps", "-A", "-o", "PID,ARGS").splitlines():
            fields = line.split()
            if "com.wetest.uia2.Main" in fields:
                shell(guest.serial, "kill", fields[0])
        adb = ["adb", "-s", guest.serial]
        for source, dest in [
            (args.assets / "tap_agent", "df-agent"),
            (args.assets / "app/res/raw/observer_key", "df-observer-key"),
        ]:
            subprocess.run(
                [*adb, "push", source, "/data/local/tmp/" + dest], check=True
            )
        shell(guest.serial, "chmod", "600", "/data/local/tmp/df-observer-key")
        shell(guest.serial, "chmod", "755", "/data/local/tmp/df-agent")
        subprocess.run(
            [
                *adb,
                "install",
                "--no-incremental",
                "-r",
                args.assets / "app/observer.apk",
            ],
            check=True,
        )
        service = "org.droidfork.observer/.Observer"
        settings = ["settings", "--user", "0"]
        previous = shell(
            guest.serial, *settings, "get", "secure", "enabled_accessibility_services"
        )
        services = [s for s in previous.split(":") if s not in ("", "null", service)]
        for _ in range(40):
            shell(
                guest.serial,
                *settings,
                "put",
                "secure",
                "enabled_accessibility_services",
                ":".join([*services, service]),
            )
            shell(
                guest.serial, *settings, "put", "secure", "accessibility_enabled", "1"
            )
            if any(
                ":7FF9 " in shell(guest.serial, "su", "0", "cat", "/proc/net/" + table)
                for table in ("tcp6", "tcp")
            ):
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Android observer failed to bind")
        shell(
            guest.serial,
            "sh",
            "-c",
            f"setsid /data/local/tmp/df-agent {path} </dev/null >/data/local/tmp/df-agent.log 2>&1 &",
        )
        shell(
            guest.serial,
            "cmd",
            "package",
            "compile",
            "-m",
            "speed",
            "-f",
            "org.droidfork.observer",
        )
        if args.prime_observer:
            result = shell(
                guest.serial,
                "sh",
                "-c",
                "(cat /data/local/tmp/df-observer-key; printf '/////wAAAAA=' | base64 -d; sleep 1) | nc -w 6 127.0.0.1 32761",
            )
            if result != "PRIMED":
                raise RuntimeError("source observation failed")
        # Do not capture package installation and accessibility enablement mid-flight.
        time.sleep(8)
        node = pool.snapshot(guest, "Fresh Contacts tap observer")
    args.fixture.write_text(
        json.dumps(
            {
                "node": node,
                "path": str(path),
                "point": point,
                "profile_guest": True,
                "geometry": field_geometry(layout),
            }
        )
        + "\n"
    )


def field(reader):
    prefix = reader.read(4)
    if len(prefix) != 4:
        raise EOFError("guest closed the result stream")
    (size,) = struct.unpack("!I", prefix)
    if not 0 < size <= 4 << 20:
        raise ValueError(f"invalid frame length {size}")
    body = reader.read(size)
    if len(body) != size:
        raise EOFError("partial guest result")
    return body


@contextlib.contextmanager
def renderer_console(guest):
    """Connect to this child's authenticated display/input console."""
    with (
        tempfile.TemporaryDirectory(prefix="df-frame-") as directory,
        socket.create_connection(
            ("127.0.0.1", int(guest.serial.split("-")[1])), timeout=4
        ) as control,
        control.makefile("rb") as reader,
    ):

        def command(request=None):
            if request is not None:
                control.sendall(request + b"\n")
            while line := reader.readline():
                if line.rstrip() == b"OK":
                    return
                if line.startswith(b"KO"):
                    raise RuntimeError(line.decode().rstrip())
            raise EOFError("emulator console disconnected")

        command()
        token = (Path.home() / ".emulator_console_auth_token").read_bytes().strip()
        command(b"auth " + token)

        def capture():
            path = Path(directory) / "screen.png"
            command(f"screenrecord screenshot {path}".encode())
            png = path.read_bytes()
            path.unlink()
            return png

        yield command, capture


def capture_renderer(guest, xml):
    with renderer_console(guest) as (_, capture):
        start = time.perf_counter()
        attempts = 0
        while True:
            attempts += 1
            png = capture()
            try:
                return png, verified_focus(xml, png, "Phone"), attempts
            except AssertionError:
                if time.perf_counter() - start >= 3:
                    raise
                time.sleep(0.012)


def field_geometry(xml):
    from xml.etree import ElementTree as ET

    result = ET.Element("hierarchy")
    for n in ET.fromstring(xml).iter("node"):
        if n.get("class") == "android.widget.EditText" and n.get("hint") in (
            "Phone",
            "Company",
        ):
            ET.SubElement(
                result,
                "node",
                {k: n.get(k) for k in ("class", "hint", "text", "bounds")},
            )
    if len(result) != 2:
        raise ValueError("source field geometry unavailable")
    return ET.tostring(result, encoding="unicode")


def wait_phone_frame(guest, point, layout, start):
    with renderer_console(guest) as (command, capture):
        before = capture()
        verified_underlines(layout, before, "Northline Studio")
        initial = time.perf_counter() - start
        x, y = point
        command(f"event mouse {x} {y} 0 1".encode())
        time.sleep(0.012)
        command(f"event mouse {x} {y} 0 0".encode())
        sent = time.perf_counter() - start
        for attempt in range(1, 60):
            png = capture()
            try:
                verified_underlines(layout, png, "Phone")
            except AssertionError:
                time.sleep(0.024)
                continue
            ready = time.perf_counter() - start
            return (
                before,
                png,
                {
                    "initial_frame_s": initial,
                    "input_sent_s": sent,
                    "first_phone_frame_s": ready,
                    "png_s": ready,
                    "frame_attempts": attempt,
                },
            )
        raise TimeoutError("phone focus never became visible")


def parallel_tap(guest, point, reader, start):
    """Observe the renderer and guest tree concurrently after a real console touch."""

    def read_tree():
        return field(reader).decode(), time.perf_counter() - start

    with (
        renderer_console(guest) as (command, capture),
        ThreadPoolExecutor(max_workers=1) as worker,
    ):
        tree = worker.submit(read_tree)
        before = capture()
        initial = time.perf_counter() - start
        x, y = point
        command(f"event mouse {x} {y} 0 1".encode())
        time.sleep(0.012)
        command(f"event mouse {x} {y} 0 0".encode())
        sent = time.perf_counter() - start
        frames = []
        while time.perf_counter() - start < sent + 7:
            image = capture()
            frames.append((time.perf_counter() - start, image))
            if tree.done():
                xml, xml_s = tree.result()
                try:
                    digest = verified_focus(xml, image, "Phone")
                except AssertionError:
                    pass
                else:
                    before_hash = verified_underlines(xml, before, "Northline Studio")
                    end = time.perf_counter() - start
                    # This scan describes the already captured frames after the timed check.
                    first_pixel = None
                    for arrived, earlier in frames:
                        try:
                            verified_underlines(xml, earlier, "Phone")
                        except AssertionError:
                            continue
                        first_pixel = arrived
                        break
                    times = {
                        "initial_frame_s": initial,
                        "input_sent_s": sent,
                        "xml_s": xml_s,
                        "verified_s": end,
                        "first_phone_frame_s": first_pixel,
                        "pixels_sha256": digest,
                        "initial_pixels_sha256": before_hash,
                        "frame_attempts": len(frames),
                        "png_s": frames[-1][0],
                    }
                    return xml, image, before, times
            time.sleep(0.022)
        raise TimeoutError("no matching post-tap frame and hierarchy")


def first_changed_frame(guest, fixture, start, host_input):
    """Hold the first optically correct live frame while Android builds its tree."""
    with renderer_console(guest) as (command, capture):
        initial = None
        times = {}
        if host_input:
            initial = capture()
            verified_underlines(fixture["geometry"], initial, "Northline Studio")
            times["initial_frame_s"] = time.perf_counter() - start
            x, y = fixture["point"]
            for button in (1, 0):
                command(f"event mouse {x} {y} 0 {button}".encode())
            times["input_sent_s"] = time.perf_counter() - start
        time.sleep(0.1)
        for attempt in range(1, 35):
            png = capture()
            try:
                verified_underlines(fixture["geometry"], png, "Phone")
            except AssertionError:
                time.sleep(0.06)
                continue
            times.update(
                first_phone_frame_s=time.perf_counter() - start, frame_attempts=attempt
            )
            return png, initial, times
        raise TimeoutError("no live Phone-focused renderer frame")


def trial(pool, fixture, binary, mode, output, save, kernel=None, tuning=None):
    node, path = fixture["node"], Path(fixture["path"])
    flags = {"DROIDFORK_TRACE": "1"} if os.getenv("DF_PROFILE") else {}
    if mode.startswith("tuned-"):
        flags["DROIDFORK_SOFTWARE_RESTORE"] = "1"
    independent = mode.startswith(("race-", "gesture-race-"))
    pixels_first = mode.startswith("pixels-")
    parallel_frame = mode.startswith("parallel-")
    event_driven = mode.startswith("event-")
    host_input = (
        pixels_first
        or parallel_frame
        or event_driven
        or mode.startswith(("touch-", "race-"))
    )
    host_frame = host_input or independent or mode.startswith("frame-")
    if kernel:
        flags["DROIDFORK_BOOT_KERNEL"] = str(kernel)
    if host_frame:
        flags["DROIDFORK_SOFTWARE_RESTORE"] = "1"
    restore_mode = (
        mode.removeprefix("gesture-race-")
        .removeprefix("race-")
        .removeprefix("tuned-")
        .removeprefix("frame-")
        .removeprefix("touch-")
        .removeprefix("event-")
        .removeprefix("parallel-")
        .removeprefix("pixels-")
    )
    if restore_mode not in ("ordinary", "stock"):
        flags["DROIDFORK_BATCH_RAM"] = "1"
    if restore_mode in (
        "fast-batch",
        "fast-map",
        "prefault",
        "direct-map",
        "direct-batch",
    ):
        flags["DROIDFORK_FAST_NETSIM"] = "1"
    if restore_mode in ("direct-map", "direct-batch"):
        flags["DROIDFORK_DIRECT"] = "1"
    if restore_mode == "prefault":
        flags["DROIDFORK_PREFAULT_RAM"] = "1"
    if restore_mode in ("mapped", "fast-map", "prefault", "direct-map"):
        flags["DROIDFORK_MAP_RAM"] = "1"
        flags["DROIDFORK_RAM_IMAGE"] = str(
            pool.root
            / "nodes"
            / node
            / "DroidFork35.avd"
            / "snapshots"
            / pool.meta(node)["tag"]
            / "droidfork-main.raw"
        )
    trace_prefix = None
    if tuning:
        flags["DROIDFORK_GPU"] = tuning.gpu
        if tuning.gpu != "swiftshader_indirect":
            flags.pop("DROIDFORK_SOFTWARE_RESTORE", None)
        if tuning.shm_name:
            flags["DROIDFORK_SHM"] = tuning.shm_name
        if tuning.fixed_adb:
            flags["DROIDFORK_FIXED_ADB"] = str(tuning.fixed_adb.resolve())
        if tuning.interactive:
            flags["DROIDFORK_INTERACTIVE"] = "1"
        if tuning.hints:
            flags["DROIDFORK_HINTS"] = str(tuning.hints.resolve())
        if tuning.page_trace:
            trace_prefix = (tuning.page_trace / identity()).resolve()
            trace_prefix.parent.mkdir(parents=True, exist_ok=True)
            flags["DROIDFORK_RECORD_PAGES"] = str(trace_prefix)
    result = {"mode": mode, "node": node}
    t = time.perf_counter()

    def stamp(key):
        result[key] = time.perf_counter() - t

    try:
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX) as server:
            server.bind(str(path))
            server.listen(1)
            server.settimeout(0.12)
            with launch(pool, node, path, binary, flags) as guest:
                result.update(pid=guest.proc.pid, log=str(guest.log))
                stamp("spawn_s")
                while True:
                    try:
                        conn, _ = server.accept()
                        break
                    except TimeoutError:
                        if (
                            guest.proc.poll() is not None
                            or time.perf_counter() - t > 20
                        ):
                            raise RuntimeError("engine failed before guest handshake")
                with conn, conn.makefile("rb") as reader:
                    conn.settimeout(10)
                    stamp("connected_s")
                    initial_png = None
                    cmd = (
                        b"E"
                        if event_driven
                        else b"O"
                        if host_input
                        else (b"F" if host_frame else b"T")
                    )
                    if "point" in fixture:
                        cmd += struct.pack("!II", *fixture["point"])
                    if pixels_first:
                        initial_png, png, stages = wait_phone_frame(
                            guest, fixture["point"], fixture["geometry"], t
                        )
                        result.update(stages)
                    conn.sendall(cmd)
                    if field(reader).decode() != node:
                        raise ValueError("checkpoint identity mismatch")
                    stamp("marker_s")
                    if independent:
                        with ThreadPoolExecutor(max_workers=1) as worker:
                            image = worker.submit(
                                first_changed_frame, guest, fixture, t, host_input
                            )
                            xml = field(reader).decode()
                            stamp("xml_s")
                            png, initial_png, stages = image.result()
                            result.update(stages)
                            result["pixels_sha256"] = verified_focus(xml, png, "Phone")
                            if initial_png is not None:
                                result["initial_pixels_sha256"] = verified_underlines(
                                    xml, initial_png, "Northline Studio"
                                )
                            stamp("verified_s")
                    elif parallel_frame:
                        xml, png, initial_png, stages = parallel_tap(
                            guest, fixture["point"], reader, t
                        )
                        result.update(stages)
                    else:
                        if host_input and not pixels_first:
                            with renderer_console(guest) as (command, capture):
                                initial_png = capture()
                                stamp("initial_frame_s")
                                x, y = fixture["point"]
                                command(f"event mouse {x} {y} 0 1".encode())
                                command(f"event mouse {x} {y} 0 0".encode())
                                stamp("input_sent_s")
                        try:
                            xml = field(reader).decode()
                        except EOFError:
                            result["observer_log"] = shell(
                                guest.serial,
                                "logcat",
                                "-d",
                                "-s",
                                "DroidForkObserver:E",
                            )[-2000:]
                            raise
                        stamp("xml_s")
                        if pixels_first:
                            result["pixels_sha256"] = verified_focus(xml, png, "Phone")
                        elif host_frame:
                            png, pixels, attempts = capture_renderer(guest, xml)
                            result.update(pixels_sha256=pixels, frame_attempts=attempts)
                        else:
                            png = field(reader)
                            stamp("png_s")
                            result["pixels_sha256"] = verified_focus(xml, png, "Phone")
                        if initial_png is not None:
                            result["initial_pixels_sha256"] = verified_underlines(
                                xml, initial_png, "Northline Studio"
                            )
                        stamp("verified_s")
                    if fixture.get("profile_guest"):
                        result["observer_us"] = json.loads(field(reader))
                if trace_prefix is not None:
                    trace_prefix.with_suffix(".trigger").touch()
                    pages = trace_prefix.with_suffix(".pages")
                    for _ in range(100):
                        if pages.exists():
                            break
                        time.sleep(0.025)
                    if not pages.exists():
                        raise RuntimeError("missing Mach RAM disposition trace")
                    result["page_dispositions"] = str(pages)
                log = guest.log.read_text(errors="replace")
                if "DROIDFORK_MAP_RAM" in flags and "DroidFork HVF: mapped" not in log:
                    raise RuntimeError(
                        "mapped restore was requested but never executed"
                    )
                result["native_log"] = [
                    s
                    for s in log.splitlines()
                    if re.search(
                        "DFstage|DroidFork (HVF|RAM|hints|Mach):|Successfully loaded snapshot",
                        s,
                    )
                ]
                if flags.get("DROIDFORK_SHM") and "DroidFork Mach:" not in log:
                    raise RuntimeError("private Mach view missing")
                output.mkdir(parents=True, exist_ok=True)
                (output / (mode + ".png")).write_bytes(png)
                (output / (mode + ".xml")).write_text(xml)
                if initial_png is not None:
                    (output / (mode + "-before.png")).write_bytes(initial_png)
                if save:
                    if contact_rows(guest):
                        raise ValueError("child already had a saved contact")
                    shell(guest.serial, "input", "text", "2025550137")
                    shell(guest.serial, "input", "tap", *select(xml, "Save"))
                    for _ in range(20):
                        if contact_rows(guest) == [
                            {"name": "Mira Shah", "number": "2025550137"}
                        ]:
                            break
                        time.sleep(0.15)
                    else:
                        raise RuntimeError("child contact save failed")
                    result["child_save_verified"] = True
    except (
        OSError,
        RuntimeError,
        ValueError,
        EOFError,
        AssertionError,
        subprocess.SubprocessError,
    ) as exc:
        result["error"] = repr(exc)
    finally:
        path.unlink(missing_ok=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--prime-observer",
        action="store_true",
        help="read and discard the initial parent hierarchy before checkpoint capture",
    )
    parser.add_argument("--prepare", metavar="BASE_DRAFT_NODE")
    parser.add_argument("--assets", type=Path)
    parser.add_argument("--stock", default=shutil.which("emulator"))
    parser.add_argument("--emulator", help="launcher in the modified SDK copy")
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=[
            "stock",
            "ordinary",
            "batch",
            "mapped",
            "fast-batch",
            "fast-map",
            "prefault",
            "direct-map",
            "direct-batch",
            "tuned-direct-map",
            "tuned-direct-batch",
            "frame-direct-map",
            "frame-direct-batch",
            "touch-direct-map",
            "touch-direct-batch",
            "event-direct-map",
            "parallel-direct-map",
            "pixels-direct-map",
            "race-direct-map",
            "gesture-race-direct-map",
        ],
        default=["mapped"],
    )
    parser.add_argument(
        "--resident-source",
        action="store_true",
        help="wire the sealed RAM file before any child exists",
    )
    parser.add_argument(
        "--anonymous-source",
        action="store_true",
        help="publish sealed checkpoint bytes in a read-only memory object before requests",
    )
    parser.add_argument(
        "--page-trace", type=Path, help="collect page dispositions after verification"
    )
    parser.add_argument(
        "--hints", type=Path, help="apply pre-touch hints during the new request"
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="use interactive macOS vCPU scheduling",
    )
    parser.add_argument(
        "--gpu",
        default="swiftshader_indirect",
        choices=["swiftshader_indirect", "host"],
    )
    parser.add_argument(
        "--kernel", type=Path, help="byte-identical decompressed SDK kernel"
    )
    parser.add_argument(
        "--fixed-adb", type=Path, help="select one SDK adb for headless startup"
    )
    parser.add_argument("--rounds", type=int, default=1)
    parser.add_argument("--results", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument(
        "--isolation",
        action="store_true",
        help="save on child and inspect the sealed source after timing",
    )
    args = parser.parse_args()
    pool = Pool()
    if args.prepare:
        if not args.assets:
            parser.error("--prepare requires --assets")
        prepare(pool, args)
        return
    if (
        not args.results
        or not args.evidence
        or (set(args.modes) - {"stock"} and not args.emulator)
    ):
        parser.error(
            "timing requires --results, --evidence, and a modified --emulator for native modes"
        )
    fixture = json.loads(args.fixture.read_text())
    path = Path(fixture["path"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    results = []
    best = {}
    with (
        (path.parent / "benchmark.lock").open("a") as lock,
        resident_source(pool, fixture, args.resident_source),
        anonymous_source(pool, fixture, args.anonymous_source) as shm_name,
    ):
        args.shm_name = shm_name
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for i in range(args.rounds):
            for mode in (
                args.modes[i % len(args.modes) :] + args.modes[: i % len(args.modes)]
            ):
                result = trial(
                    pool,
                    fixture,
                    args.stock if mode == "stock" else args.emulator,
                    mode,
                    args.evidence,
                    args.isolation and i == 0,
                    args.kernel,
                    args,
                )
                result["resident_source"] = args.resident_source
                result["anonymous_source"] = args.anonymous_source
                print(json.dumps(result), flush=True)
                results.append(result)
                if "error" not in result and result["verified_s"] < best.get(
                    mode, float("inf")
                ):
                    best[mode] = result["verified_s"]
                    for suffix in (".xml", ".png", "-before.png"):
                        evidence = args.evidence / (mode + suffix)
                        if evidence.exists():
                            shutil.copyfile(
                                evidence, args.evidence / (mode + "-best" + suffix)
                            )
                args.results.write_text(json.dumps(results, indent=2) + "\n")
        if args.isolation:
            with launch(
                pool, fixture["node"], path, args.stock, {"DROIDFORK_GPU": args.gpu}
            ) as g:
                g.until_ready(pool.meta(fixture["node"]))
                if contact_rows(g):
                    raise RuntimeError("sealed checkpoint has been modified")
                xml = hierarchy(g)
                from xml.etree import ElementTree

                if not any(
                    n.get("focused") == "true" and n.get("text") == "Northline Studio"
                    for n in ElementTree.fromstring(xml).iter("node")
                ):
                    raise RuntimeError("draft focus changed")
                results.append(
                    {"sealed_parent_empty": True, "original_focus": "Northline Studio"}
                )
                args.results.write_text(json.dumps(results, indent=2) + "\n")
    if any("error" in r for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
