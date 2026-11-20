# Fresh Android fork through a verified Contacts tap on an M1 Pro

The original 3.490 s request spent 1.083 s reaching the guest and another 2.407 s delivering and verifying the tap. On the original two-vCPU, software-rendered configuration, a fresh child reached the guest in 0.505 s and passed in 1.570 s. That reduced the two intervals by 0.578 s and 1.342 s respectively. These are individual observed requests. [Original runs](fresh-tap.json) · [Two-vCPU runs](fresh-profile/two-cpu.json)

The fastest complete request reached **1.145 s**, 145 ms over the goal, on a four-vCPU checkpoint with the M1 Pro GPU. The clock included the Android tree, the rendered frame and host verification. Native touchscreen requests in that six-run series had a median of **1.233 s**. The first run of a newly staged executable in the companion Android-gesture series took 14.511 s. Median and best-case results are distinct from cold-start performance.

| Milestone on the 1.145 s request | Time since request |
|---|---:|
| Private child disks created and new process spawned | 0.014 s |
| Guest connected and returned the saved volatile identity | 0.468 / 0.469 s |
| Captured initial Company-focused frame / sent touchscreen up | 0.487 / 0.487 s |
| Full live tree arrived with Phone focused | 0.879 s |
| Decoded rendered frame, confirmed both underlines and finished verification | 1.145 s |

On this request, 0.392 s elapsed from sending the tap to receiving the tree and another 0.265 s through frame capture and verification. The latter includes waiting for the renderer to supply the screenshot. An independent capture experiment retained the first frame with the right underline while the guest returned its hierarchy, then rechecked the frame against the live bounds. Its best result was 1.175 s. On an earlier run, a frame already showed Phone at 0.742 s but the tree arrived at 1.526 s. Neither half alone establishes a verified tap. [Fastest run data](fresh-profile/host-fixed-adb.json) · [Independent frame trials](fresh-profile/host-first-frame.json) · [Earlier overlap trials](fresh-profile/host-overlap.json) · [Initial frame](fresh-profile/evidence/host-fixed-adb/touch-direct-map-best-before.png) · [Verified frame](fresh-profile/evidence/host-fixed-adb/touch-direct-map-best.png) · [Live tree](fresh-profile/evidence/host-fixed-adb/touch-direct-map-best.xml)

## Where the original time went

The native log begins immediately after launch. The host had already spent 0.011 s creating the listener, private AVD and process. The interval estimates below align that log with the host's request clock. Logging marks the receipt of printed lines, so the subintervals are approximate. [Original and instrumented native timelines](fresh-profile/startup-traces.json)

| Portion of the original 1.083 s | Approximate duration |
|---|---:|
| Set up the child, including launch | 0.011 s |
| Process launch to first AVD configuration message | 0.300 s |
| Configuration through start of gfxstream load | 0.124 s |
| Load gfxstream through renderer entry | 0.242 s |
| Set up graphics, virtual board and devices through start of snapshot load | 0.247 s |
| Load the named snapshot | 0.134 s |
| Resume through guest pipe connection | 0.025 s |

The original host received the full XML at 2.476 s, the PNG at 3.483 s and verified it at 3.490 s. The guest had no internal trace in this run. An instrumented replay of the same draft took 1.120 s to connect and 3.438 s to pass. Its guest timings below are cumulative from entering the observer's request handler. Capture and hierarchy serialization overlap. Host and guest clocks have different origins. [Guest observer traces](fresh-profile/guest-baseline.json)

| Stage in the instrumented replay | Guest elapsed |
|---|---:|
| Read and authenticate request | 0.057 s |
| Check that Company initially had focus | 0.132 s |
| Dispatch gesture / Android reports gesture complete | 0.156 / 0.302 s |
| Find Phone focused and request screenshot | 0.874 s |
| Android screenshot callback | 1.249 s |
| Finish serializing full accessibility tree | 1.474 s |
| Check that the screenshot shows the new underline | 1.893 s |
| Finish PNG encoding | 2.090 s |

The time from gesture completion to finding Phone focused was 0.572 s in this replay. Obtaining and checking the rendered pixels continued after accessibility reported the focus change. A second replay had to take another screenshot. Android rate-limits successive accessibility screenshots and the observer waited 350 ms before retrying. Host-side PNG verification itself took about 5–7 ms in these original-style runs.

## What reduced latency

- A native stack sample during gfxstream loading found dyld applying fixups through global symbol lookups. Relinking the Mac backend with normal two-level lookup while retaining dynamic lookup for unresolved host symbols cut a repeat renderer library load from roughly 242 ms to 5 ms. Supplying the same SDK kernel already decompressed reduced early launch work. Both changes operate on shared program or source files. [Renderer patch](../experiments/fresh-restore/renderer-link.patch) · [Kernel identity and native timings](fresh-profile/startup-traces.json)
- The emulator ordinarily searches for ADB in multiple locations, runs candidate binaries to identify their protocols, and checks the active daemon. An opt-in path to a specific installed ADB probes it once and preserves normal discovery if that fails. On the four-vCPU hardware-rendered fixture, selecting ADB brought a repeat connection to 0.468 s. [Emulator patch](../experiments/fresh-restore/fixed-adb.patch) · [Run timings](fresh-profile/host-fixed-adb.json)
- The live emulator framebuffer supplies a full-resolution screenshot without a guest-side bitmap copy, PNG encoding or accessibility screenshot rate limit. The benchmark tried both an Android-dispatched gesture and an emulator touchscreen down/up pair. The host still needs to see the actual focus change in Contacts and in the renderer. Aggressive screenshot polling sometimes delayed the tree. [Renderer and input benchmark](../experiments/fresh-restore/benchmark.py)
- Five alternating pairs exercised both builds on **one immutable two-vCPU checkpoint**. The original path passed 5/5, with a 3.271 s median. The host-input/concurrent-capture path passed 4/5, with a 2.544 s median among passes. Connection medians across all attempts were 1.102 s and 0.577 s respectively. One optimized child closed the stream without returning a verified transition. These pairs used the concurrent-capture variant, which was slower than the best individual native-input result. [Paired raw attempts](fresh-profile/matched/paired.json) · [Parent isolation check](fresh-profile/matched/isolation.json)
- Separate four- and six-vCPU checkpoints retained the same Android image, app, draft, display and verifier. The host GPU logs identify the Apple M1 Pro and Metal. With four vCPUs and fixed ADB, all 12 attempts passed. All six native touchscreen requests passed with a median of 1.233 s. Raising native vCPU scheduling priority had a best time of 1.163 s. The six-vCPU trial and the trial fetching and checking focus in a single fresh hierarchy also stayed above a second. [Four-vCPU hardware rendering](fresh-profile/host-overlap.json) · [Six-vCPU hardware rendering](fresh-profile/host-six.json) · [Single-tree timings](fresh-profile/host-onepass.json) · [vCPU scheduling trials](fresh-profile/host-qos.json)
- Wiring the whole source, pre-touching selected RAM pages, and supplying a private Mach view of an anonymous checkpoint did not produce a faster passing child in the pilots. The corrected anonymous-source prototype passed in 3.896 s and 3.653 s. [Anonymous-source trials](fresh-profile/mach.json)

## What counts as a pass

The stopwatch starts before creating the child AVD, disk clones, control listener and emulator process. The guest returns a volatile ID from the named checkpoint. Its full live accessibility hierarchy must contain the unsaved Mira Shah / Northline Studio draft with Phone focused. The host decodes the 480 × 854 PNG and checks for the blue underline on Phone and its absence on Company. The Android-dispatched gesture checks initial Company focus in the live guest. The native touchscreen path also captures a live initial child frame and checks the opposite underline state. Winning runs used ordinary host file caches and a sealed, file-backed RAM image. There was no existing child process, private disk or reserved VM. First launches are included in the raw records. Successful isolation runs saved a contact in a child after timing and reopened the saved parent to check that it still had the unsaved Company-focused draft. [Reproduction](../experiments/fresh-restore/README.md)
