from droidfork.emulator import Pool, record, shell
from droidfork.android import configure
import time


def main():
    p = Pool()
    t = time.monotonic()
    with p.fresh() as g:
        print("Android booted", round(time.monotonic() - t, 2), flush=True)
        configure(g)
        node = p.snapshot(g, "contacts-clean-home")
        record(
            p.root / "base.json",
            {
                "node": node,
                "fingerprint": shell(g.serial, "getprop", "ro.build.fingerprint"),
            },
        )
        print("Prepared", node, flush=True)


if __name__ == "__main__":
    main()
