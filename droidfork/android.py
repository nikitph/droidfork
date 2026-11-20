"""Allowlisted GUI actions, observable state and out-of-band Contacts scoring."""

from __future__ import annotations

from http.client import RemoteDisconnected
import hashlib
import io
import json
import re
import time
from xml.etree import ElementTree as ET

from PIL import Image
import uiautomator2 as u2
from adbutils.errors import AdbError

from .emulator import console, shell, run

CONTACTS = "com.google.android.contacts"
PHONE_URI = "content://contacts/phones/"
EDIT = "android.widget.EditText"


def ui(g):
    if g.ui is None:
        g.ui = u2.connect(g.serial)
        g.ui.jsonrpc.setConfigurator(
            {"waitForIdleTimeout": 0, "waitForSelectorTimeout": 100}
        )
    return g.ui


def prepare_snapshot_observer(g):
    """Leave the accessibility controller in Android with no live host shell to inherit."""
    controller = g.ui
    if controller is None or controller._process is None:  # already detached
        return
    g.detach_observer()
    shell(
        g.serial,
        "sh",
        "-c",
        "CLASSPATH=/data/local/tmp/u2.jar setsid app_process / com.wetest.uia2.Main "
        "-p 9008 </dev/null >/data/local/tmp/droidfork-observer.log 2>&1 &",
    )
    deadline = time.monotonic() + 12
    while not controller._check_alive():
        if time.monotonic() >= deadline:
            # Older images can still use the normal host-attached observer on demand.
            return
        time.sleep(0.05)
    # Prime the resident controller before capture. Children open their own transport.
    hierarchy(g)


def contact_rows(g):
    raw = shell(
        g.serial,
        "content",
        "query",
        "--uri",
        PHONE_URI,
        "--projection",
        "display_name:number",
    )
    if raw == "No result found.":
        return []
    rows = [
        dict(name=name, number=re.sub(r"\D", "", number))
        for name, number in re.findall(
            r"^Row: \d+ display_name=(.*), number=(.*)$", raw, re.M
        )
    ]
    if not rows or len(rows) != len(raw.splitlines()):
        raise RuntimeError(
            "unrecognized Android contacts provider result: " + raw[:180]
        )
    return sorted(rows, key=lambda c: (c["name"], c["number"]))


def judge(g, name, number):
    with g.lock:
        return float(
            {"name": name, "number": re.sub(r"\D", "", number)} in contact_rows(g)
        )


def hierarchy(g):
    deadline = time.monotonic() + 12
    while True:
        try:
            return ui(g).dump_hierarchy(max_depth=65)
        except (RemoteDisconnected, ConnectionResetError, AdbError) as e:
            if time.monotonic() >= deadline:
                raise
            if isinstance(e, AdbError) and not any(
                word in str(e) for word in ("offline", "closed", "not found")
            ):
                raise
            g.ui = None
            time.sleep(0.25)


def select(tree, label):
    for node in ET.fromstring(tree).iter("node"):
        if (
            label in (node.get("text"), node.get("content-desc"))
            and node.get("enabled") == "true"
        ):
            nums = list(map(int, re.findall(r"\d+", node.get("bounds", ""))))
            if len(nums) == 4:
                x, y, r, b = nums
                return [(x + r) // 2, (y + b) // 2]
    return None


def wait_label(g, label, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pos = select(hierarchy(g), label)
        if pos:
            return pos
        time.sleep(0.08)
    diagnostic = g.pool.root / "logs" / (g.home.name + "-missing.xml")
    diagnostic.write_text(hierarchy(g))
    raise TimeoutError(f"missing GUI label {label!r}. Device hierarchy in {diagnostic}")


def observe(g, *, tree=None):
    with g.lock:
        if tree is None:
            tree = hierarchy(g)
        # The emulator can encode its rendered frame on the host, saving a guest
        # screencap process and PNG transfer. Each guest has its own locked home.
        frame = g.home / "observation.png"
        frame.unlink(missing_ok=True)
        try:
            console(g.serial, "screenrecord", "screenshot", str(frame))
            png = frame.read_bytes()
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("invalid emulator screenshot")
        except (OSError, RuntimeError, ValueError):
            png = run(["adb", "-s", g.serial, "exec-out", "screencap", "-p"])
        finally:
            frame.unlink(missing_ok=True)
        activities = shell(g.serial, "dumpsys", "activity", "activities")
        current = re.search(r"topResumedActivity=.*? u[0-9]+ (\S+) t[0-9]+", activities)
        return {
            "xml": tree,
            "png": png,
            "activity": current.group(1) if current else "",
        }


def digest(obs):
    # Store exact pixels plus an app-only semantic signature, reported independently.
    root = ET.fromstring(obs["xml"])
    fields = (
        "class",
        "resource-id",
        "text",
        "content-desc",
        "bounds",
        "checked",
        "selected",
        "focused",
    )
    nodes = [
        tuple(n.get(k, "") for k in fields)
        for n in root.iter("node")
        if n.get("package") == CONTACTS
    ]
    pixels = Image.open(io.BytesIO(obs["png"])).convert("RGB")
    return {
        "pixels": hashlib.sha256(pixels.tobytes()).hexdigest(),
        "xml": hashlib.sha256(obs["xml"].encode()).hexdigest(),
        "app_tree": hashlib.sha256(json.dumps(nodes).encode()).hexdigest(),
        "activity": obs["activity"],
    }


def settled_tree(g, timeout=2):
    # Poll for two consecutive identical accessibility hierarchies. Animations are disabled on the lab image.
    start = time.monotonic()
    previous = hierarchy(g)
    while time.monotonic() - start < timeout:
        time.sleep(0.06)
        current = hierarchy(g)
        if current == previous:
            return True, current
        previous = current
    return False, previous


def settle(g, timeout=2):
    idle, _ = settled_tree(g, timeout)
    return idle


def step(g, action, *, return_observation=False):
    with g.lock:
        typ = action["type"]
        if typ == "launch":
            if action["app"] != "contacts":
                raise ValueError("this task pack allows Contacts")
            shell(
                g.serial,
                "am",
                "start",
                "-W",
                "-a",
                "android.intent.action.MAIN",
                "-c",
                "android.intent.category.LAUNCHER",
                "-p",
                CONTACTS,
            )
            wait_label(g, "Search contacts", timeout=35)
        elif typ == "tap":
            coords = (
                wait_label(g, action["label"])
                if "label" in action
                else [action["x"], action["y"]]
            )
            if not all(type(v) is int and 0 <= v <= 32767 for v in coords):
                raise ValueError("invalid tap coordinates")
            shell(g.serial, "input", "tap", *coords)
        elif typ == "type":
            value = action["text"]
            if not re.fullmatch(r"[a-zA-Z0-9 .,()+@_-]{1,120}", value):
                raise ValueError("unsupported text for adb input")
            shell(g.serial, "input", "text", value.replace(" ", "%s"))
        elif typ == "swipe":
            coords = [action[k] for k in ("x1", "y1", "x2", "y2")]
            if not all(type(v) is int and 0 <= v <= 32767 for v in coords):
                raise ValueError("invalid swipe coordinates")
            shell(g.serial, "input", "swipe", *coords, 300)
        elif typ == "key" and action["key"] in ("BACK", "HOME", "ENTER", "TAB"):
            shell(g.serial, "input", "keyevent", "KEYCODE_" + action["key"])
        elif typ != "wait":
            raise ValueError("unsupported action")
        idle, tree = settled_tree(g)
        result = {"idle": idle}
        if return_observation:
            result.update(observe(g, tree=tree))
        return result


def configure(g):
    for scope, key, val in [
        ("global", "window_animation_scale", "0"),
        ("global", "transition_animation_scale", "0"),
        ("global", "animator_duration_scale", "0"),
        ("system", "screen_off_timeout", "2147483647"),
        ("secure", "show_ime_with_hard_keyboard", "0"),
    ]:
        shell(g.serial, "settings", "put", scope, key, val)
    for cmd in (
        ["svc", "wifi", "disable"],
        ["svc", "data", "disable"],
        ["input", "keyevent", "KEYCODE_WAKEUP"],
        ["wm", "dismiss-keyguard"],
    ):
        shell(g.serial, *cmd)
    # This fixture models a hardware keyboard. Gboard can re-enable the on-screen
    # keyboard on first launch and hide form fields on the small display.
    for package in ("com.google.android.inputmethod.latin", "com.google.android.tts"):
        shell(g.serial, "pm", "disable-user", "--user", "0", package)
    # Preflight the app and its runtime permissions once on the clean benchmark image.
    for perm in (
        "READ_CONTACTS",
        "WRITE_CONTACTS",
        "READ_PHONE_STATE",
        "POST_NOTIFICATIONS",
    ):
        try:
            shell(g.serial, "pm", "grant", CONTACTS, "android.permission." + perm)
        except RuntimeError:
            pass
    shell(
        g.serial,
        "am",
        "start",
        "-W",
        "-a",
        "android.intent.action.INSERT",
        "-t",
        "vnd.android.cursor.dir/contact",
        "-p",
        CONTACTS,
    )
    wait_label(g, "First name")
    shell(g.serial, "am", "force-stop", CONTACTS)
    step(g, {"type": "key", "key": "HOME"})
    if contact_rows(g):
        raise RuntimeError("clean benchmark image unexpectedly contains contacts")
