# DroidFork

**A fresh Android checkpoint fork through a verified tap in 1.145 s on an M1 Pro.** Six of six native touchscreen requests passed with a median of **1.233 s**. The clock includes creating private disks and a process, restoring the saved Android guest, tapping Phone in Contacts, and verifying the live accessibility tree and rendered pixels. The host reused ordinary file caches.

[Project page](https://nikitph.github.io/droidfork/) · [19-second film](https://nikitph.github.io/droidfork/droidfork.mp4) · [Paper (PDF)](docs/droidfork.pdf) · [LaTeX source](paper/droidfork.tex) · [Raw touchscreen trials](results/fresh-profile/host-fixed-adb.json) · [Build the modified emulator](experiments/fresh-restore/README.md)

The headline result uses the **experimental modified emulator**. The trainer demo below uses the stock SDK and has its own timing results.

DroidFork branches a running Android session into independently actionable devices. The local prototype runs Android 15 on the stock Android Emulator with hardware acceleration on an Apple Silicon Mac. A Python trainer talks to the host over gRPC. Snapshots capture an unsaved draft in the actual Contacts app, and rewards come from the Android Contacts Provider.

[Verified tap and fork latency](results/LATENCY.md) · [Original end-to-end benchmark](results/REPORT.md) · [Raw end-to-end trials](results/bench.json) · [gRPC API](droidfork/rpc/api.proto)

Check the published summary and both fastest-run image hashes locally with `uv run --python 3.12 python -m scripts.verify_public_result`. Raw trial files preserve timing values and failure rows. Published traces use relative paths in place of local home directories.

## Run the live demo

Install `uv` and the Android SDK tools `adb`, `emulator`, `avdmanager` and `sdkmanager`. Put them on PATH and set `ANDROID_HOME` to the SDK directory. Use an APFS workspace on macOS and the API 35 Google APIs ARM image.

```sh
sdkmanager 'system-images;android-35;google_apis;arm64-v8a'
make demo
```

The first run creates a dedicated AVD and a clean task-start checkpoint under `.lab/pool`. The demo starts a loopback service, enters a name and company on screen, forks the unsaved form, and saves two different phone numbers. It prints both rewards and elapsed times, then shuts down its devices and server. The fixture contains synthetic contacts and uses a hardware-keyboard layout, software rendering and offline app interaction. Budget several GB of disk for each retained machine checkpoint. Android SDK updates can invalidate checkpoints. Run `make prepare` to build a new base after an update.

## Use the trainer API

Start `make serve` after preparing the base. Run the following in the project Python environment. Each step returns a PNG, accessibility XML, foreground activity and an idle indicator. A label tap resolves an accessible control on the current device. Coordinate taps, swipes, text, keys, launching Contacts and waiting are also supported.

```python
from droidfork.client import Pool
from droidfork.scenario import prefix_actions

pool = Pool()
parent = pool.reset()
children = []
try:
    for action in prefix_actions(("Mira", "Shah")):
        observation = parent.step(action)
    draft = parent.snapshot(name="Mira before phone")
    children = pool.fork(draft, 2)
    for child, number in zip(children, ["2025550137", "2025550184"]):
        child.step({"type": "tap", "label": "Phone"})
        child.step({"type": "type", "text": number})
        child.step({"type": "tap", "label": "Save"})
    print([c.judge("Mira Shah", "2025550137") for c in children])
finally:
    pool.release([parent, *children])
```

The checked-in benchmark dispatches child actions concurrently. A checkpoint ID can be passed to `pool.reset(id)` or `pool.fork(id, n)` after the parent exits. Snapshots of children retain their parent reference. After an interrupted in-place restore, the server requires a successful reset before accepting further actions on that device. `pool.release` is idempotent. The server defaults to four live devices and localhost-only transport for a trusted trainer.

## How state is captured

**Machine checkpoints.** The host runs the accessibility observer inside Android with its host shell detached, then asks the emulator to save its CPU, device, RAM and disk state, briefly stops the VM, and clones the AVD files into a sealed checkpoint. It resumes the parent even if copying fails. Each child gets a private AVD directory via filesystem reflinks, unique console ports, and its own emulator process. The host checks both the loaded checkpoint tag and a volatile Android property before reporting readiness. Those checks catch a silent fallback to cold boot. Children reconnect to the resident observer through their own ADB transport. A step reuses the last accessibility tree from its idle check and captures the emulator display on the host, with guest screencap as a fallback. The native emulator controls guest time. Exact screenshot equality is measured separately from matching application fields and reward.

**Contacts app checkpoints.** `device.snapshot(kind="app")` snapshots the visible four-field editor and its focus, takes an online SQLite backup of the Contacts Provider, and stores the manifest and database by SHA-256. `pool.reset(app_id, reuse=dev)` restores into a warm device. The host checks the Android image and Contacts version, restores the database and reconstructs the list-to-editor flow. This adapter covers the tested name, company and phone form. Other screens, extra fields and generic Android activity saved-state bundles require additional adapters.

**Storage and memory.** A machine checkpoint uses the emulator's native snapshot format. APFS shares unchanged file blocks across copies. The measured stock backend restores guest RAM into separate emulator processes. This distinction matters for interpreting memory density and for extrapolating to Cuttlefish. The recorded evidence includes task parameters, image fingerprint, observations and provider outcomes.

## Reproduce the measurements

```sh
make prepare
make serve                       # leave running in terminal 1
make benchmark                   # run in terminal 2
make verify                      # isolated machine and app-level fidelity checks
make upstream                    # downloads the pinned AndroidWorld checkout
uv run --python 3.12 python -m scripts.fanout
```

The benchmark alternates the order of three implementations across five contact names. Each batch creates one correct contact and one counterfactual with the wrong number. Both trajectories go through the same gRPC GUI actions and state verifier.

- **Cold replay** boots two independent stock emulators from the prepared disks, replays the entire prefix on both, completes both forms and scores them.
- **Warm replay** rewinds two already running stock emulators to the same clean machine snapshot. It replays the prefix on both. Startup and retirement are reported separately for this steady-state baseline.
- **On-demand fork** resets a parent, runs the shared prefix once, takes a fresh machine checkpoint and launches the children concurrently. Timing includes independent actions and rewards. The report also gives the interval from the fork request through both children's first verified tap.

To resume an interrupted measurement series against the same base, add `--resume` to the benchmark command. Raw observations and action durations are recorded for successful batches. Validation errors are recorded separately.

For a Linux trainer in OrbStack, keep the Android host service on the Mac and run the client container using the host gateway.

```sh
docker build -f Dockerfile.bench -t droidfork-bench:local .
docker run --rm -v "$PWD/results:/lab/results" droidfork-bench:local \
  --address host.docker.internal:54329 --rounds 1 --modes fork --out results/orbstack.json
```

## Scope of the evidence

The workload follows AndroidWorld's `ContactsAddContact` task on the installed API 35 image. The independent upstream check invokes the pinned task's initializer and verifier against the real devices through AndroidEnv's ADB transport. The multi-trial benchmark uses the same name-and-normalized-phone criterion and verifies the alternate number separately. It runs a scripted policy so model inference time and API spending are excluded.

The reference deployment in the proposal uses x86-64 Cuttlefish and KVM. This Mac has an M1 Pro and its OrbStack runtime has no KVM device. Cuttlefish fork latency, `userfaultfd` lazy restore, private guest page sharing, virtual-clock scheduling and 100-device server density need separate hardware evidence. This repository is a single-host prototype. Snapshot sharing between hosts and hosted multitenancy are outside its implemented surface.

[Android Emulator snapshots](https://developer.android.com/studio/run/emulator-snapshots) · [Cuttlefish snapshot requirements](https://source.android.com/docs/devices/cuttlefish/snapshot-restore) · [OrbStack Linux virtualization](https://docs.orbstack.dev/machines/) · [Pinned AndroidWorld task](https://github.com/google-research/android_world/blob/55ddaf5edc6f6c25673aed343907d5b9dca66f84/android_world/task_evals/single/contacts.py)
