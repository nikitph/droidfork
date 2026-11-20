# Mac fork and action latency

A tap on a running Android 15 Contacts draft reached **556 ms median and 908 ms p95**. Fresh machine restoration remained above one second. This run used the same 8-core M1 Pro with 16 GiB RAM and an ARM64 Android Emulator as the [original benchmark](REPORT.md).

| Request through verified result | Original median | Updated median | Updated trials |
|---|---:|---:|---:|
| Tap on one running device | 1.007 s | 0.556 s | 50 taps |
| Rewind a running emulator to the saved draft and tap | 5.639 s | 2.304 s | 5 rewinds |
| Fork a saved draft into one newly launched emulator and tap | 6.672 s | 4.808 s | 5 batches |
| Fork a saved draft into two newly launched emulators and tap both | 9.185 s | 6.010 s | 5 batches |

The original comparison had 20 alternating warm taps and two trials per restore scenario. The two runs used separate snapshots with the same unsaved name, company and focused field. Each timer includes trainer gRPC, the returned PNG, accessibility XML, foreground activity and client-side verification. The fork timer starts at the request for an already saved checkpoint and ends when the last child has passed. It excludes saving the checkpoint, the later contact save and scoring.

**49 of 50 warm taps finished within one second.** The slowest took 1.056 s. p95 uses the nearest rank. Taps alternated between the phone and company fields. In the updated benchmark the XML had to show the requested field focused, and the screenshot had to show its blue underline on that field alone. All 15 fresh child trajectories went on to save the intended number for their branch. Every tested pair produced opposite rewards on the requested phone number. The original comparison checked XML focus, decoded the returned PNG and checked that pixels changed between alternating taps.

A separate full fidelity run verified that the parent kept its unsaved draft, both children saved their own numbers, a checkpoint of the alternate child retained its contact, and the app-level restore reconstructed the original form. [Fidelity results](latency-fidelity.json)

## Changes to the running system

The accessibility controller can now stay inside the guest across a checkpoint with its original host shell disconnected. Each child connects independently. A GUI step samples the tree until it stabilizes and returns that tree directly. Screenshot capture uses the emulator display on the host and falls back to Android screencap. In a separate intermediate run with the original screenshot path, the median warm tap was 685 ms over 30 taps. These small sequential experiments include normal host scheduling variation.

## Where restore time went

Across three separately instrumented launches, the median APFS clone took **5 ms**. The first successful Android boot-state query took **3.82 s** measured from the initial query attempt, including emulator startup, transport availability and retries. The trace cannot distinguish those components. In existing emulator processes the native snapshot-load command itself took **1.31 s** median. Each restore also checked the native snapshot tag, a volatile checkpoint identifier and the installed Contacts package. [Restore trace](restore-profile.json)

Two SDK flags were also tested using a dedicated checkpoint created with each flag enabled. With `OnDemandSnapshotLoad`, the child stayed offline through the 150 s Android readiness deadline. With `SnapshotAdb`, two fresh one-child restores reached a verified tap in 7.75 s and 5.22 s. Both subsequently saved the correct number. The default backend remains the fastest measured fresh option. [Feature trials](restore-features.json)

## Preparing specific branches ahead of time

Three batches per size restored the exact draft, connected the observer and stopped each VM before the request. The timed operation resumed each VM and performed the visually verified tap.

| Prepared children | Median preparation | Median resume through all taps |
|---|---:|---:|
| 1 | 4.655 s | 0.482 s |
| 2 | 5.698 s | 1.169 s |

This trades advance work and one occupied emulator slot per child for lower response latency. Each prepared copy is specific to one checkpoint. The fresh-fork results above include creating every child after the request.

## Reproduce

Run this from the repository with the prepared base installed. The command creates a new draft checkpoint and starts its own temporary loopback service.

```sh
uv run --python 3.12 python -m scripts.latency --taps 50 --rewinds 5 --rounds 5 --parked 3 --out .lab/latency-repeat.json
```

The JSON contains the checkpoint ID. To trace the same machine restore, pass that ID to the following command.

```sh
uv run --python 3.12 python -m scripts.restore_profile CHECKPOINT_ID --out .lab/restore-profile-repeat.json
```

[Original timing samples](latency-before.json) · [Intermediate samples](latency-after.json) · [Updated timing and branch outcomes](latency-mac.json) · [Host and emulator image](host.json)
