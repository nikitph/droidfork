# Android branching benchmark

Two Android 15 Contacts rollouts per batch on an 8-core Apple M1 Pro with 16 GiB RAM. Each starts from the same clean fixture. The scripted user opens Contacts, creates an unsaved name and company, and then chooses a phone number. One branch saves the requested number. The counterfactual saves a different number. The judge reads the Contacts Provider outside the GUI action channel.

| Method | Paired batches | Median batch, s | Range, s | Rollouts per host-hour | Host-seconds per positive reward |
|---|---:|---:|---:|---:|---:|
| Cold boot and replay | 5 | 47.74 | 46.69–48.71 | 150.9 | 47.71 |
| Warm rewind and replay | 5 | 24.39 | 22.99–26.00 | 295.4 | 24.37 |
| On-demand machine fork | 5 | 40.96 | 40.72–43.38 | 174.0 | 41.38 |

[Batch timings for every trial](benchmark.png). Throughput is extrapolated from the summed wall time of the measured batches.

Every timed policy step, including the first tap in each child, returned a screenshot, accessibility tree and foreground activity over gRPC. Timing includes checking the alternate number. The fork and cold paths include launch and release. The warm path measures a running worker pool. Provisioning and retiring that pair cost an additional median 5.61 s per batch if done from scratch. At a supplied host price of P per hour, multiply host-seconds per positive reward by P / 3600. The experiment used local hardware and no paid inference.

The final series recorded 290 GUI actions. 13 returned the documented idle timeout. The policy checked the observed fields and the saved provider state. The raw file records 0 failed batch attempts in this series. Development runs preceded the final series.

## Where the time went

| Fork path stage | Median, s |
|---|---:|
| Reset parent | 3.78 |
| Enter shared GUI prefix | 12.50 |
| Capture and seal machine checkpoint | 2.70 |
| Request through all Android package-manager readiness checks | 4.30 |
| Tap Phone in every child and receive observations | 4.85 |
| Request through every first verified GUI action, including observations | 9.25 |
| Enter numbers, save and score | 8.32 |
| Release devices | 1.68 |

The request-to-first-action row overlaps the preceding two rows. Independent workers replay their prefixes in parallel.

## Isolation and fidelity

The provider returned the requested number in one child and the alternate number in its sibling. The unsaved parent retained 0 persisted rows. Restoring a snapshot of the alternate child retained its own saved contact. The upstream AndroidWorld initializer and verifier independently returned [1.0, 0.0] for the children and 0.0 for the parent.

[Parent draft](evidence/parent-draft.png) · [Correct child](evidence/child-0-saved.png) · [Counterfactual child](evidence/child-1-saved.png) · [Upstream verifier results](androidworld.json)

- App accessibility signature matched across both children immediately after the same tap in 5/5 timed fork batches.
- Complete accessibility XML matched across both children immediately after the same tap in 2/5 timed fork batches.
- Exact screenshot pixels matched across both children immediately after the same tap in 3/5 timed fork batches.

## App checkpoint measurements

Across 5 drafts in one app, 5/5 restores retained the pre-existing contact, erased the subsequently saved counterfactual, reconstructed the unsaved fields and focus, and completed the task with the requested number. Median capture was 0.19 s. Median warm restore was 6.73 s. Database blobs occupied 319,488 bytes each. The five manifests referenced 2 distinct database blobs and occupied 469–471 bytes. Full-screen pixels matched exactly in 4/5 cases.

## Fan-out from a saved checkpoint

These runs keep the source parent offline. Each trial creates all children concurrently, taps Phone, verifies the inherited fields and focus, and records the operating system's process memory charge. The physical footprint covers an entire emulator process, including graphics and runtime components.

| Live children | Trials | Median ready, s | Median all acted, s | Median process footprint per child, MiB |
|---:|---:|---:|---:|---:|
| 1 | 3 | 3.77 | 6.09 | 5,087 |
| 2 | 3 | 4.69 | 7.64 | 5,096 |
| 3 | 3 | 5.66 | 15.06 | 5,124 |

## Interpretation

The stock-emulator fork delivered 1.15× the throughput of cold replay and 0.59× the throughput of warm replay on this workload. These are small-sample, single-machine observations. The spec's sub-second 16-child goal, 32-way ≥99% screen-and-tree match and Linux/KVM host density remain unverified. The strict equality counts above include status-bar and blinking-cursor changes.

A separate experiment enabled the emulator's QuickbootFileBacked feature and started a child with a hard link to a sealed 2.5 GiB RAM image. The emulator reported loading the snapshot in 112 ms. Android stayed offline through the 150 s readiness deadline, so this experiment did not establish usable shared guest RAM. The timings above use the working native snapshot backend.

[Raw gRPC trials](bench.json) · [App trials](app-benchmark.json) · [Fan-out and memory](fanout.json) · [Functional test](functional.json) · [Host and image](host.json)
