# AGENTS.md — liskov-runtime-images

This public repository owns reproducible, provenance-rich AArch64 rootfs
images curated for Liskov-managed Acurast Cargo/PRoot workloads.

## Scope

- Keep source materials immutable and digest-pinned in `sources.lock.json`.
- Treat `v4-control` as a compatibility control, not the maintained default.
- Build the maintained image from the exact single-platform OCI manifest
  digest, never from a mutable tag.
- Overlay only the source and deterministically compiled AArch64 shared object
  for the documented Acurast `getifaddrs` compatibility override and the
  generated Liskov provenance record.
- Never embed `liskov-runtime-contact` or its license. The control plane
  snapshots the verified helper beside generated `acurast.sh`; image validation
  may inject one exact released helper only into an ephemeral test root.
- Keep image construction independent of `liskov-rs`. The reusable
  `liskov-github-actions` workflow may consume release artifacts later.

## Supply-chain invariants

- Verify every downloaded manifest, config, layer, and rootfs archive before
  extracting it. Verify the test-only helper before injecting it into a smoke
  root.
- Reject archive path traversal and symlink-parent traversal.
- Do not use PRoot-Distro `install` or `backup` output as a release artifact:
  those commands apply host-specific fixups or produce restore bundles.
- Normalize archive order, timestamps, numeric ownership, and compression.
- A source-lock change is a new image revision and needs a reviewed provenance
  diff plus a fresh Acurast canary.
- Never commit generated rootfs archives, caches, credentials, or canary
  secrets.

## Validation

Before every commit, run the change-aware local gate:

```sh
scripts/validate-change.sh
```

Every change runs unit, classifier, workflow-contract, shell-syntax, and Python
compile checks. A material local change constructs each affected target once.
Use `scripts/verify-reproducible.sh <target>` only for release troubleshooting;
the authoritative two-clean-build proof runs once in native ARM64 release CI.

Material CI constructs each affected target once, runs
`scripts/verify-native-aarch64.sh`, and validates the exact archive through
QEMU/PRoot. A commit that updates a valid `release-intent.json` constructs each
target twice in fresh trees, compares every output byte, passes the same native
and QEMU/PRoot gates, and emits the commit-bound attested release bundle. Tag
publication must only promote that bundle; it never constructs an image.

Live Acurast canaries are explicit, bounded release gates; they are not
ordinary tests and must not be dispatched merely to validate CI plumbing.
