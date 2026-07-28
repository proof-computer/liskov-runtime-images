# AGENTS.md — liskov-runtime-images

This public repository owns reproducible, provenance-rich AArch64 rootfs
images curated for Liskov-managed Acurast Cargo/PRoot workloads.

## Scope

- Keep source materials immutable and digest-pinned in `sources.lock.json`.
- Treat `v4-control` as a compatibility control, not the maintained default.
- Build the maintained image from the exact single-platform OCI manifest
  digest, never from a mutable tag.
- Overlay only the released `liskov-runtime-contact` binary, its Apache-2.0
  license, and the generated Liskov provenance record.
- Keep image construction independent of `liskov-rs`. The reusable
  `liskov-github-actions` workflow may consume release artifacts later.

## Supply-chain invariants

- Verify every downloaded manifest, config, layer, rootfs archive, and helper
  archive before extracting it.
- Reject archive path traversal and symlink-parent traversal.
- Do not use PRoot-Distro `install` or `backup` output as a release artifact:
  those commands apply host-specific fixups or produce restore bundles.
- Normalize archive order, timestamps, numeric ownership, and compression.
- A source-lock change is a new image revision and needs a reviewed provenance
  diff plus a fresh Acurast canary.
- Never commit generated rootfs archives, caches, credentials, or canary
  secrets.

## Validation

Before every commit:

```sh
python3 -m unittest discover -s tests -v
python3 -m compileall -q scripts tests
scripts/verify-reproducible.sh v4-control
scripts/verify-reproducible.sh debian-trixie
```

The GitHub ARM64 job additionally runs `scripts/verify-native-aarch64.sh` and
executes the embedded helper on the native architecture. A separate x86_64 job
boots the exact artifact through QEMU/PRoot with `scripts/smoke-rootfs.sh` and
exercises abstract bridge-socket access. Live Acurast canaries are explicit,
bounded release gates; they are not ordinary tests.
