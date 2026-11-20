"""Task-specific app checkpoints for Contacts. SQLite backup captures committed rows including WAL."""

import hashlib
import json
import os
import re
import tempfile
from xml.etree import ElementTree as ET

from .android import CONTACTS, EDIT, hierarchy, settle, wait_label, ui
from .emulator import identity, run, shell

DB = "/data/user/0/com.android.providers.contacts/databases/contacts2.db"
LABELS = ("First name", "Last name", "Company", "Phone")


def put(pool, data):
    key = hashlib.sha256(data).hexdigest()
    out = pool.root / "objects" / key
    with tempfile.NamedTemporaryFile(dir=out.parent) as tmp:
        tmp.write(data)
        tmp.flush()
        try:
            os.link(tmp.name, out)
        except FileExistsError:
            pass
    return key


def get(pool, key):
    if not re.fullmatch(r"[a-f0-9]{64}", key):
        raise ValueError("invalid object id")
    data = (pool.root / "objects" / key).read_bytes()
    if hashlib.sha256(data).hexdigest() != key:
        raise IOError("object checksum mismatch")
    return data


def image_id(g):
    version = shell(g.serial, "dumpsys", "package", CONTACTS)
    return [
        shell(g.serial, "getprop", "ro.build.fingerprint"),
        re.search(r"versionCode=(\d+)", version).group(1),
    ]


def editor(g):
    tree = hierarchy(g)
    nodes = list(ET.fromstring(tree).iter("node"))
    fields = [
        n for n in nodes if n.get("class") == EDIT and n.get("package") == CONTACTS
    ]
    by_hint = {n.get("hint"): n for n in fields}
    if (
        not any(n.get("text") == "Create contact" for n in nodes)
        or any(label not in by_hint for label in LABELS)
        or len(by_hint) != len(fields)
        or any(
            n.get("text") not in ("", n.get("hint"))
            for n in fields
            if n.get("hint") not in LABELS
        )
    ):
        raise ValueError(
            "Contacts app snapshot requires the four-field new-contact editor"
        )
    return [
        {
            "label": label,
            "value": "" if n.get("text") == label else n.get("text", ""),
            "focused": n.get("focused") == "true",
        }
        for label, n in ((label, by_hint[label]) for label in LABELS)
    ]


def app_snapshot(g):
    with g.lock:
        fields = editor(g)
        tmp = "/data/local/tmp/droidfork-" + identity() + ".db"
        try:
            shell(g.serial, "su", "0", "sqlite3", DB, ".backup " + tmp)
            db = run(["adb", "-s", g.serial, "exec-out", "su", "0", "cat", tmp])
        finally:
            shell(g.serial, "su", "0", "rm", "-f", tmp)
        if not db.startswith(b"SQLite format 3\0"):
            raise RuntimeError("failed to read online backup")
        manifest = dict(
            version=1,
            kind="contacts-editor",
            image=image_id(g),
            db=put(g.pool, db),
            fields=fields,
        )
        return put(g.pool, json.dumps(manifest, sort_keys=True).encode())


def app_restore(g, key):
    with g.lock:
        manifest = json.loads(get(g.pool, key))
        if (
            manifest.get("version") != 1
            or manifest.get("kind") != "contacts-editor"
            or manifest["image"] != image_id(g)
        ):
            raise ValueError("Contacts snapshot incompatible with device")
        data = get(g.pool, manifest["db"])
        tmp = "/data/local/tmp/droidfork-" + identity() + ".db"
        with tempfile.NamedTemporaryFile() as f:
            f.write(data)
            f.flush()
            try:
                run(["adb", "-s", g.serial, "push", f.name, tmp])
                g.restore_incomplete = True
                shell(g.serial, "am", "force-stop", CONTACTS)
                shell(g.serial, "am", "force-stop", "com.android.providers.contacts")
                shell(g.serial, "su", "0", "sqlite3", DB, ".restore " + tmp)
            finally:
                shell(g.serial, "rm", "-f", tmp)
        # Reconstruct the list -> editor back stack used by this task.
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
        automation = ui(g)
        automation.click(*wait_label(g, "Create contact"))
        wait_label(g, "First name")
        for field in manifest["fields"]:
            if field["value"]:
                automation(className=EDIT, text=field["label"]).set_text(field["value"])
        current = editor(g)
        for field, actual in zip(manifest["fields"], current):
            if field["focused"] and not actual["focused"]:
                automation.click(*wait_label(g, field["value"] or field["label"]))
                # The hardware-keyboard form used by this adapter ends field entry at the caret.
                # Moving there also dismisses the touch insertion handle raised by a tap.
                shell(g.serial, "input", "keyevent", "KEYCODE_MOVE_END")
        settle(g)
        if editor(g) != manifest["fields"]:
            raise RuntimeError("restored form differs from app snapshot")
        g.restore_incomplete = False
