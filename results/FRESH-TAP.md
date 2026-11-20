# Fresh Android child to verified tap on Apple Silicon

**The modified emulator reached 3.431 s, with 0 of 12 final launches under one second.** The host started the clock before creating the child, then sent the touch coordinates after the guest connected. The fastest stock run on the same saved Contacts draft took 4.383 s.

| Final configuration | Fresh children | Best verified tap | Median verified tap |
| --- | ---: | ---: | ---: |
| Stock Android Emulator 37.2.12 | 3 | 4.383 s | 4.930 s |
| Modified engine, batched reads | 3 | **3.431 s** | 3.715 s |
| Modified engine, private RAM mapping | 3 | 3.490 s | 7.465 s |
| Modified engine, private mapping with only the saved RAM wired on the host | 3 | 3.460 s | **3.466 s** |

All 12 returned verified Android observations. Each mode also passed a separate isolation check. After timing, one child per mode saved Mira Shah's phone number to Android's Contacts Provider. Restoring the sealed checkpoint showed an empty provider and the original Company-focused draft. Each child had its own AVD directory, APFS-cloned disks and newly launched QEMU process. The resident-source trials held a read-only 2.5 GiB mapping of the saved checkpoint before the requests. The other trials used the ordinary macOS file cache. Cache eviction and machine reboot were outside the experiment. [Individual timings](fresh-tap.json) · [Resident-source timings](fresh-tap-resident.json)

## Where the time went

In the fastest end-to-end trial the host finished cloning the private AVD and spawning QEMU at 12 ms. The first guest connection arrived at 1.644 s. The host received the post-tap accessibility tree at 3.386 s, the screenshot at 3.423 s and completed verification at 3.431 s. The connection and observation timestamps are cumulative from the request. The time before the tree includes dispatching the Android gesture, observing the focus change and collecting the tree.

The fastest mapped trial spent **134 ms inside native snapshot loading**. It reached the guest channel at 1.083 s, returned the tree at 2.476 s and passed pixel verification at 3.490 s. The first mapped launch of the freshly staged development binary took 12.138 s, including 9.439 s to the guest connection. This outlier is included. With three samples per configuration, these ranges describe this run on this Mac. They establish no general latency floor. [Native log excerpts and raw timings](fresh-tap.json)

The experiment changed three native paths. QEMU can coalesce adjacent saved pages into reads of up to 4 MiB. Alternatively it can map an independently writable view of the sealed RAM into the guest's existing Hypervisor.framework slots. For both fast paths it can detect that the local network simulator is ready during the upstream retry sleep. These changes reduced restore and startup work. The first usable Android frame and accessibility observation still took seconds.

## What counted as a tap

The physical host was an Apple M1 Pro with 16 GiB, running macOS 27.0 build 26A428. The guest was an API 35 ARM64 Google APIs AVD with two vCPUs, 2,560 MiB of RAM, SwiftShader and a 480 × 854 display. The final saved node was `849ebb60656c48639a3bb2daf3f8c623`. Its guest observer and relay were installed before capture.

At request time the host created the control listener, cloned the private AVD, launched a fresh process and sent the touch coordinates (237, 703) after connection. The Android accessibility service checked the foreground Contacts package and initial Company focus, dispatched a real touchscreen gesture and captured a fresh tree and full-screen PNG. The host checked the checkpoint marker, original name and company, Phone focus, the blue underline on Phone and its absence on Company. The stopwatch includes the host's PNG decode and pixel checks. ADB was used for fixture creation and the later persistence checks. [Returned screenshot](evidence/fresh-tap/direct-batch.png) · [Returned accessibility tree](evidence/fresh-tap/direct-batch.xml)

## Earlier trials

An earlier relay stored the coordinates in the saved parent and waited for a host command before tapping. With the sealed source pinned, its fastest fully verified fresh child took **3.101 s**. That fixture and protocol differ from the final host-supplied-coordinate trials. [Earlier resident-source trials](native-fresh-resident.json)

The first observer sometimes captured the compositor before it showed the accessibility focus change. Four such observations failed host verification and remain in the raw file. The later observer retried screenshots on the request clock, and the final host verifier kept the same optical check. Pre-faulting writable RAM also shifted several seconds into restore. The completed comparison used a separate private copy of the SDK and left the installed SDK available for the stock controls. [Initial trials](native-fresh.json) · [Network and RAM variants](native-fresh-final.json) · [Direct engine trials](native-fresh-direct.json)

The final ad-hoc signed headless executable has SHA-256 `3d0e848d68a08389e8176ac3e90b0d0ecca6c9174db2a3da4c8f48b55651062f`. [Engine patch](../experiments/fresh-restore/native-restore.patch) · [Build and repeat the benchmark](../experiments/fresh-restore/README.md)
