# Changelog

## Unreleased

- Add the digest-pinned Debian Trixie AArch64 OCI-derived release candidate.
- Add the exact Termux PRoot-Distro v4.30.1 Ubuntu Questing AArch64
  compatibility control.
- Embed `liskov-runtime-contact` v0.2.5 through an exact five-path overlay,
  including owned pre-contact reporting and a budget-correct, closed-enum
  bridge probe that preserves the documented exact short request ID after
  decision-critical probes.
- Enable log capture only for the bounded v4 bridge probe; final v4 and Debian
  canaries remain unchanged and keep log capture disabled.
- Include the source and deterministic AArch64 shared object for Acurast's
  documented loopback-only `getifaddrs` override.
- Add deterministic archives, inventories, overlay ledgers, SPDX SBOMs,
  provenance records, native ARM64/PRoot validation, checksums, and GitHub
  artifact attestations.
- Classify commits into fast, material, and release modes so ordinary material
  development builds each target once while release-intended commits alone
  prove two clean native builds and retain an attested, commit-bound bundle.
- Publish tags only from the exact qualified bundle; release and canary-pin
  workflows never reconstruct a rootfs.
