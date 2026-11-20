# Fresh Android fork benchmark on Apple Silicon

The benchmark starts a fresh headless emulator from a saved Contacts draft, sends a touchscreen gesture to the Phone field and checks the resulting Android accessibility tree and screenshot. It requires an APFS-backed DroidFork pool. The SDK installed in Android Studio can continue to run independently of the development build.

For the published four-vCPU result, start with the SDK prerequisites in the [project README](../../README.md) and run `make prepare` once to create the pool base. Build the observer and relay using the commands below. Create the four-vCPU draft from that base using the command under **Profile a fresh child and its first input**. You can verify the published frames and timing summary without compiling QEMU by running `uv run --python 3.12 python -m scripts.verify_public_result`.

## Build the engine

The tested SDK is Android Emulator 37.2.12, build 16428233. The QEMU source is commit `bbaa8072362a251645b339b97a737a725c776126` on AOSP's `emu-main-dev` branch. [AOSP QEMU revision](https://android.googlesource.com/platform/external/qemu/+/bbaa8072362a251645b339b97a737a725c776126/)

Use an emulator source checkout with the layout of the AOSP emu-main-dev manifest. The adjacent [revision list](source-revisions.json) records the actual repositories and commits used. For a fresh checkout, apply both patches to `external/qemu`. The Apple toolchain patch selects the local Command Line Tools clang. The runtime patch changes QEMU's snapshot RAM loader, Hypervisor.framework memory registration and local network-daemon discovery.

The following commands run from the DroidFork project root. Set `AOSP` to the source root, `OBJ` to a separate build directory and `DEV_SDK` to a new directory. This build used CMake 3.31.6, Ninja and Apple's ARM64 command-line tools.

```sh
export EXP="$PWD/experiments/fresh-restore"
git -C "$AOSP/external/qemu" apply "$EXP/apple-clang.patch" "$EXP/native-restore.patch"
cmake -S "$AOSP/external/qemu" -B "$OBJ" -G Ninja \
  -DCMAKE_TOOLCHAIN_FILE="$AOSP/external/qemu/android/build/cmake/toolchain-darwin-aarch64.cmake" \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_OSX_DEPLOYMENT_TARGET=13.0 \
  -DOPTION_MINBUILD=ON -DWEBRTC=OFF -DQTWEBENGINE=OFF -DOPTION_TCMALLOC=OFF \
  -DOPTION_CRASHUPLOAD=NONE -DOPTION_SDK_TOOLS_REVISION=37.2.12 \
  -DOPTION_SDK_TOOLS_BUILD_NUMBER=16428233
cmake --build "$OBJ" --target qemu-system-aarch64-headless gfxstream_backend -j 7
sh "$EXP/stage-engine.sh" "$OBJ" "$DEV_SDK"
```

The staging script uses the installed SDK's renderer resources together with locally compiled dynamic libraries. On macOS 27 the packaged SwiftShader could allocate its first color buffer with the shipping emulator's macOS 13.3 SDK compatibility setting. The engine uses ad-hoc signing with the Hypervisor.framework entitlement.

## Prepare a checkpoint

Set `BASE` to the existing unsaved Mira Shah draft node with the Company field focused. Use the API 35 ARM64 Google APIs image, a 480 × 854 display and SwiftShader. The guest service requires Android API 30 or newer. This fixture uses an Android userdebug build. Its setup uses ADB and the guest's root shell to check that the observer is listening. The benchmark sends its input and receives observations over the emulator's Unix pipe.

```sh
export ASSETS="$PWD/.lab/native-build"
export FIXTURE="$ASSETS/fixture-v3.json"
export PYTHONPATH="$PWD"
sh "$EXP/build-observer.sh" "$ASSETS/app"
"$ANDROID_HOME/ndk/27.1.12297006/toolchains/llvm/prebuilt/darwin-x86_64/bin/aarch64-linux-android29-clang" \
  -O2 -Wall "$EXP/tap_agent.c" -o "$ASSETS/tap_agent"
.venv/bin/python "$EXP/benchmark.py" --prepare "$BASE" --assets "$ASSETS" --fixture "$FIXTURE"
NODE=$(jq -r .node "$FIXTURE")
TAG=$(jq -r .tag ".lab/pool/nodes/$NODE/meta.json")
.venv/bin/python "$EXP/prepare_ram.py" ".lab/pool/nodes/$NODE/DroidFork35.avd/snapshots/$TAG"
```

The exporter supports this fixture's uncompressed v2 layout with 16 KiB pages. It writes a read-only, page-ordered file from the checkpoint. Its header binds the layout to the saved index using SHA-256. The file is a trusted local build artifact. For mapped trials QEMU opens that sealed file and creates a private mapping for the new child. Ordinary and batched restores read the original snapshot. The experiment keeps private child disks in every mode.

The observer APK and relay live in the saved guest. The coordinates are recorded as fixture metadata on the host. In the current protocol the relay receives both the command and coordinates only after the child connects. The Android-dispatched gesture checks the original Company focus inside each fresh child. The native touchscreen variant records the initial child framebuffer and verifies the Company underline. No timed child exists during checkpoint preparation.

## Run

```sh
DF_PROFILE=1 .venv/bin/python "$EXP/benchmark.py" --fixture "$FIXTURE" \
  --emulator "$DEV_SDK/emulator" --modes direct-map direct-batch stock --rounds 3 \
  --isolation --results results/fresh-tap.json --evidence results/evidence/fresh-tap

DF_PROFILE=1 .venv/bin/python "$EXP/benchmark.py" --fixture "$FIXTURE" \
  --emulator "$DEV_SDK/emulator" --modes direct-map --rounds 3 --resident-source \
  --isolation --results results/fresh-tap-resident.json --evidence results/evidence/fresh-tap-resident
```

Run with ordinary Python assertion checking enabled. Each timed interval begins before binding the control listener, creating the private AVD and launching its QEMU process. The host checks the guest's volatile checkpoint ID, the draft values, the focused node and the blue underline on Phone alone in a newly captured full-screen PNG. The stopwatch includes PNG decoding and pixel verification. Source preparation, optional source pinning, later contact saves and shutdown are outside the interval. The host stores a representative PNG and XML for each mode along with the RGB hash and stage times for every successful request.

The optional resident-source run wires the 2.5 GiB read-only checkpoint mapping on the host before timing and releases it when the run finishes. Direct modes launch the headless binary with the SDK library paths, request faster local netsim discovery, and disable metrics and the optional crash handler. The baseline uses the stock SDK launcher. All modes use the same Contacts app, rendering configuration, gesture and verifier within a series.

## Profile a fresh child and its first input

Apply the profiling patches before configuring the build above. Keep a separate SDK copy built with just the two original patches if you want the matched control. `memory-profile.patch` is optional and applies on top of `native-restore.patch`.

```sh
git -C "$AOSP/external/qemu" apply "$EXP/profile-startup.patch" "$EXP/fixed-adb.patch"
git -C "$AOSP/hardware/google/gfxstream" apply "$EXP/renderer-link.patch"
# For anonymous backing, page tracing, or vCPU QoS experiments:
git -C "$AOSP/external/qemu" apply "$EXP/memory-profile.patch"
```

The fast launch accepts the original SDK kernel decompressed once per image. Both forms contain identical guest instructions. The ADB option selects one specific executable and still checks its protocol version inside the new emulator process. The default observer checks initial focus, dispatches a gesture and queries the resulting focus. To try the separate single-tree observer variant, set `DF_SINGLE_TREE=1` when running `build-observer.sh` and prepare a new source checkpoint.

```sh
gzip -dc "$ANDROID_HOME/system-images/android-35/google_apis/arm64-v8a/kernel-ranchu" > "$ASSETS/kernel-ranchu.raw"

# Build and save a source draft if changing CPU count or GPU backend.
.venv/bin/python "$EXP/make_profile_draft.py" --cores 4 --gpu host --output "$ASSETS/gpu-base"
.venv/bin/python "$EXP/benchmark.py" --prepare "$(cat "$ASSETS/gpu-base")" --gpu host \
  --assets "$ASSETS" --fixture "$FIXTURE"
NODE=$(jq -r .node "$FIXTURE")
TAG=$(jq -r .tag ".lab/pool/nodes/$NODE/meta.json")
.venv/bin/python "$EXP/prepare_ram.py" ".lab/pool/nodes/$NODE/DroidFork35.avd/snapshots/$TAG"

DF_PROFILE=1 .venv/bin/python "$EXP/benchmark.py" --fixture "$FIXTURE" \
  --emulator "$DEV_SDK/emulator" --kernel "$ASSETS/kernel-ranchu.raw" --gpu host \
  --fixed-adb "$(command -v adb)" --modes frame-direct-map touch-direct-map \
  gesture-race-direct-map race-direct-map --rounds 5 --isolation \
  --results results/fork-profile.json --evidence results/fork-profile-evidence
```

`frame-direct-map` uses Android accessibility to deliver the tap. `touch-direct-map` sends a down/up pair through the authenticated emulator console after capturing the initial child frame. Both read the full post-tap accessibility tree from Android and a new full-screen PNG from the renderer. `gesture-race-direct-map` and `race-direct-map` retain the first renderer frame with the changed underline while Android returns the tree. The host rechecks the saved frame against the live tree before stopping the clock.

For the alternating two-vCPU software-rendered control, use `profile_pairs.py --fixture "$FIXTURE" --control "$CONTROL_SDK/emulator" --optimized "$DEV_SDK/emulator" --kernel "$ASSETS/kernel-ranchu.raw" --output results/matched` with a software-rendered fixture. Each raw row records host-clock milestone seconds, a new PID, the checkpoint identity and guest-side stage durations. The test preserves failed requests and first launches with cold host caches. [Measured results and timing breakdown](../../results/FRESH-PROFILE.md)
