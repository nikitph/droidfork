"""Local trusted-trainer endpoint. All Android administration stays inside the host process."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import functools
import json
import signal
from pathlib import Path
import threading
import traceback

import grpc

from . import android
from .contacts import app_restore, app_snapshot
from .emulator import Pool, identity
from .rpc import api_pb2 as pb, api_pb2_grpc as rpc


class CapacityError(Exception):
    pass


def checked(fn):
    @functools.wraps(fn)
    def wrapped(self, q, ctx):
        try:
            return fn(self, q, ctx)
        except CapacityError as e:
            ctx.abort(grpc.StatusCode.RESOURCE_EXHAUSTED, str(e))
        except KeyError as e:
            ctx.abort(grpc.StatusCode.NOT_FOUND, str(e))
        except (ValueError, FileNotFoundError) as e:
            ctx.abort(grpc.StatusCode.INVALID_ARGUMENT, str(e))
        except Exception as e:
            traceback.print_exc()
            ctx.abort(grpc.StatusCode.FAILED_PRECONDITION, str(e))

    return wrapped


class Service(rpc.EnvironmentsServicer):
    def __init__(self, pool, capacity):
        self.pool = pool
        self.base = json.loads((pool.root / "base.json").read_text())
        self.capacity = capacity
        self.slots = threading.BoundedSemaphore(capacity)
        self.devices = {}
        self.lock = threading.RLock()

    def allocate(self, n, fn, context):
        if n < 1 or n > self.capacity:
            raise CapacityError("requested child count exceeds host capacity")
        held = 0
        with self.lock:
            for _ in range(n):
                if not self.slots.acquire(blocking=False):
                    for _ in range(held):
                        self.slots.release()
                    raise CapacityError("insufficient free device slots")
                held += 1
        try:
            guests = fn()
            if not context.is_active():
                self.pool.release(guests)
                raise RuntimeError("client disconnected during launch")
            with self.lock:
                result = []
                for g in guests:
                    key = identity()
                    self.devices[key] = g
                    result.append(pb.Device(id=key))
                return result
        except BaseException:
            for _ in range(n):
                self.slots.release()
            raise

    @contextmanager
    def guest(self, key, *, for_reset=False):
        with self.lock:
            g = self.devices[key]
        with g.lock:
            if g.lease is None:
                raise KeyError(key)
            if g.restore_incomplete and not for_reset:
                raise RuntimeError(
                    "device restore was interrupted; reset or release this device"
                )
            yield g

    def Info(self, q, ctx):
        return pb.InfoReply(
            base=self.base["node"],
            fingerprint=self.base["fingerprint"],
            capacity=self.capacity,
        )

    @checked
    def Reset(self, q, ctx):
        node = q.snapshot or self.base["node"]
        is_app = node.startswith("app-")
        if q.cold_boot and (is_app or q.reuse):
            raise ValueError("cold boot requires a fresh machine from disk")
        if q.reuse:
            with self.guest(q.reuse, for_reset=True) as g:
                if is_app:
                    app_restore(g, node[4:])
                else:
                    self.pool.rewind(g, node)
            return pb.Device(id=q.reuse)

        def launch():
            g = self.pool.reset(self.base["node"] if is_app else node, cold=q.cold_boot)
            try:
                if is_app:
                    app_restore(g, node[4:])
                return [g]
            except BaseException:
                g.close()
                raise

        return self.allocate(1, launch, ctx)[0]

    @checked
    def Snapshot(self, q, ctx):
        with self.guest(q.device) as g:
            node = "app-" + app_snapshot(g) if q.app else self.pool.snapshot(g, q.name)
        return pb.SnapshotId(id=node)

    @checked
    def Fork(self, q, ctx):
        return pb.Devices(
            children=self.allocate(q.n, lambda: self.pool.fork(q.snapshot, q.n), ctx)
        )

    @checked
    def Observe(self, q, ctx):
        with self.guest(q.id) as g:
            return pb.Observation(**android.observe(g))

    @checked
    def Step(self, q, ctx):
        kind = q.WhichOneof("action")
        if kind == "tap_label":
            action = {"type": "tap", "label": q.tap_label}
        elif kind == "tap":
            action = {"type": "tap", "x": q.tap.x, "y": q.tap.y}
        elif kind == "swipe":
            action = {
                "type": "swipe",
                "x1": q.swipe.start.x,
                "y1": q.swipe.start.y,
                "x2": q.swipe.end.x,
                "y2": q.swipe.end.y,
            }
        elif kind in ("type_text", "launch", "key"):
            typ, field = {
                "type_text": ("type", "text"),
                "launch": ("launch", "app"),
                "key": ("key", "key"),
            }[kind]
            action = {"type": typ, field: getattr(q, kind)}
        elif kind == "wait":
            action = {"type": "wait"}
        else:
            raise ValueError("action missing")
        with self.guest(q.device) as g:
            return pb.Observation(**android.step(g, action, return_observation=True))

    @checked
    def Judge(self, q, ctx):
        with self.guest(q.device) as g:
            return pb.Reward(value=android.judge(g, q.name, q.number))

    @checked
    def Release(self, q, ctx):
        guests = []
        with self.lock:
            for dev in q.children:
                g = self.devices.pop(dev.id, None)
                if g:
                    guests.append(g)
        try:
            self.pool.release(guests)
        finally:
            for _ in guests:
                self.slots.release()
        return pb.Empty()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=54329)
    parser.add_argument("--capacity", type=int, default=4)
    parser.add_argument("--ready-file", type=Path)
    args = parser.parse_args()
    svc = Service(Pool(), args.capacity)
    server = grpc.server(ThreadPoolExecutor(max_workers=48))
    rpc.add_EnvironmentsServicer_to_server(svc, server)
    port = server.add_insecure_port("127.0.0.1:" + str(args.port))
    server.start()
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    if args.ready_file:
        args.ready_file.write_text(str(port))
    print("DroidFork listening on localhost", port, flush=True)
    try:
        done.wait()
    finally:
        server.stop(10).wait()
        svc.pool.release(list(svc.devices.values()))


if __name__ == "__main__":
    main()
