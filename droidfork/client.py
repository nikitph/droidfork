"""Python trainer SDK. The client can run inside Linux without an Android SDK."""

import grpc
from .rpc import api_pb2 as pb, api_pb2_grpc as rpc


class Pool:
    def __init__(self, address="127.0.0.1:54329"):
        self.channel = grpc.insecure_channel(address)
        self.rpc = rpc.EnvironmentsStub(self.channel)
        self.info = self.rpc.Info(pb.Empty(), timeout=10)

    def reset(self, snapshot=None, *, reuse=None, cold=False):
        r = self.rpc.Reset(
            pb.ResetRequest(
                snapshot=snapshot or self.info.base,
                reuse=reuse.id if reuse else "",
                cold_boot=cold,
            ),
            timeout=240,
        )
        return Device(self, r.id)

    def fork(self, snapshot, n):
        devices = self.rpc.Fork(pb.ForkRequest(snapshot=snapshot, n=n), timeout=240)
        return [Device(self, d.id) for d in devices.children]

    def release(self, devices):
        self.rpc.Release(
            pb.Devices(children=[pb.Device(id=d.id) for d in devices]), timeout=90
        )


class Device:
    def __init__(self, pool, key):
        self.pool = pool
        self.id = key

    def observe(self):
        return self.pool.rpc.Observe(pb.Device(id=self.id), timeout=40)

    def step(self, action):
        typ = action["type"]
        if typ == "tap":
            key, value = (
                ("tap_label", action["label"])
                if "label" in action
                else ("tap", pb.Point(x=action["x"], y=action["y"]))
            )
        elif typ == "swipe":
            key, value = (
                "swipe",
                pb.Swipe(
                    start=pb.Point(x=action["x1"], y=action["y1"]),
                    end=pb.Point(x=action["x2"], y=action["y2"]),
                ),
            )
        elif typ == "wait":
            key, value = "wait", pb.Empty()
        else:
            key, field = {
                "type": ("type_text", "text"),
                "launch": ("launch", "app"),
                "key": ("key", "key"),
            }[typ]
            value = action[field]
        return self.pool.rpc.Step(
            pb.StepRequest(device=self.id, **{key: value}), timeout=60
        )

    def snapshot(self, kind="machine", name="checkpoint"):
        if kind not in ("app", "machine"):
            raise ValueError("unknown snapshot kind")
        return self.pool.rpc.Snapshot(
            pb.SnapshotRequest(device=self.id, name=name, app=kind == "app"),
            timeout=180,
        ).id

    def judge(self, name, number):
        return self.pool.rpc.Judge(
            pb.JudgeRequest(device=self.id, name=name, number=number), timeout=20
        ).value
