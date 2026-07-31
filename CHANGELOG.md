# Changelog

## Unreleased

- Add the digest-pinned Debian Trixie AArch64 OCI-derived release candidate.
- Add the exact Termux PRoot-Distro v4.30.1 Ubuntu Questing AArch64
  compatibility control.
- Remove `liskov-runtime-contact`, its license, source lock, provenance fields,
  inventory entries, and SPDX relationships from both published image lanes.
  Liskov now snapshots the verified helper beside generated `acurast.sh`.
- Make the bounded v4 bridge probe an explicit server-authorized bootstrap mode
  over the ordinary `/bin/true` command; customer command strings are never
  interpreted as internal control signals.
- Enable log capture only for the bounded v4 bridge probe; final v4 and Debian
  canaries remain unchanged and keep log capture disabled.
- Retain the source and deterministic AArch64 shared object for Acurast's
  documented loopback-only `getifaddrs` override.
- Add deterministic archives, inventories, overlay ledgers, SPDX SBOMs,
  provenance records, native ARM64/PRoot validation, checksums, and GitHub
  artifact attestations.
- Classify commits into fast, material, and release modes so ordinary material
  development builds each target once while release-intended commits alone
  prove two clean native builds and retain an attested, commit-bound bundle.
- Publish tags only from the exact qualified bundle; release and canary-pin
  workflows never reconstruct a rootfs.
