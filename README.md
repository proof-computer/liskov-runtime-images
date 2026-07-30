# liskov-runtime-images

Reproducible, provenance-rich AArch64 rootfs images for Liskov-managed Acurast
Cargo/PRoot workloads.

This repository makes the complete image transformation public. Every release
starts from immutable upstream bytes, verifies every source digest before
extraction, overlays the released static `liskov-runtime-contact` helper and
the loopback-only Acurast `getifaddrs` compatibility shim, and emits a
deterministic plain `tar.xz` rootfs accepted by Acurast Cargo.

## Image tracks

| Target | Upstream trust root | Status |
| --- | --- | --- |
| `debian-trixie` | Exact official Debian `trixie-slim` AArch64 OCI platform-manifest, config, and layer digests | Release candidate; intended maintained default after its Acurast canary |
| `v4-control` | Exact Termux PRoot-Distro v4.30.1 Ubuntu Questing AArch64 release asset | Compatibility control only |

Acurast consumes an image URL and SHA-256, not a PRoot-Distro major version.
PRoot-Distro v5 no longer publishes distribution rootfs assets; it materializes
OCI images instead. The maintained track therefore uses the official Debian
OCI image as its trust root and records PRoot-Distro v5.5.0 as a compatibility
reference. It does not publish host-specific output from PRoot-Distro
`install` or its restore-oriented `backup` format.

The v4 control is deliberately not the maintained default. Its upstream asset
is retained to distinguish an Acurast archive/PRoot compatibility failure from
a newer OCI-rootfs problem.

## Declared Liskov overlay

The base filesystem receives exactly these Liskov-owned paths:

```text
/usr/local/bin/liskov-runtime-contact
/usr/local/lib/libgetifaddrs_override.so
/usr/share/doc/liskov-runtime-contact/LICENSE
/usr/share/liskov-runtime-images/getifaddrs_override.c
/usr/share/liskov-runtime-images/provenance.json
```

The helper is the exact static AArch64 release binary from
[`proof-computer/liskov-runtime-cargo`](https://github.com/proof-computer/liskov-runtime-cargo).
The Apache-2.0 shim is compiled deterministically from the included source and
implements the loopback-only workaround documented for Cargo/PRoot by
[Acurast](https://docs.acurast.com/developers/build/cargo-runtime-environment/#network-interfaces-getifaddrs).
Liskov's bootstrap exports it through `LD_PRELOAD` only when the verified
library is present in the rootfs.
The embedded provenance record identifies the upstream material, base
inventory, helper release, and deterministic archive policy. Numeric ownership,
timestamps, member order, and compression are normalized and declared
separately from the filesystem overlay.

Operating-system packages retain their original licenses. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and each release's SPDX SBOM.

## Verify a release

Download the image and its metadata from the same GitHub release, then:

```sh
sha256sum --check SHA256SUMS
gh attestation verify \
  liskov-runtime-image-debian-trixie-aarch64.tar.xz \
  --repo proof-computer/liskov-runtime-images \
  --source-digest <release-source-commit> \
  --signer-workflow proof-computer/liskov-runtime-images/.github/workflows/ci.yml
```

Each target publishes:

```text
<stem>.tar.xz
<stem>.files.json
<stem>.overlay.json
<stem>.provenance.json
<stem>.spdx.json
BUILD-MANIFEST.json
SHA256SUMS
```

`files.json` inventories every archive member, including symlinks, hardlinks,
device metadata, and extended-attribute digests. `overlay.json` isolates the
five declared additions. `BUILD-MANIFEST.json` binds the repository, source
commit, release version, material-input fingerprint, complete target/file set,
sizes, digests, and qualifying workflow run. GitHub's artifact attestations
bind every final file to that public CI workflow and source commit.

## Build and reproduce

Requirements:

- Python 3.11 or newer
- GNU tar
- XZ Utils
- an AArch64 C compiler (`cc` on AArch64 or `aarch64-linux-gnu-gcc` elsewhere;
  override the executable with `LISKOV_AARCH64_CC`)
- outbound HTTPS to GitHub Releases and the Docker registry

Build one target:

```sh
python3 scripts/build-image.py debian-trixie --output-dir out/debian-trixie
python3 scripts/build-image.py v4-control --output-dir out/v4-control
```

Prove two independent materializations are byte-identical:

```sh
scripts/verify-reproducible.sh debian-trixie
scripts/verify-reproducible.sh v4-control
```

These double-build commands are retained for release troubleshooting. They are
not the ordinary pre-commit gate and do not replace the authoritative native
ARM64 release qualification.

The source lock contains no mutable trust anchor. For the OCI image, the build
downloads the exact platform manifest, verifies its SHA-256, requires the exact
locked config and layer descriptors, verifies every blob, validates
`linux/arm64/v8`, applies OCI whiteouts, and only then creates the rootfs.

## Validation

The change-aware local gate runs unit, classifier, workflow-contract,
shell-syntax, and Python compile checks for every change. Material changes also
construct each affected target once:

```sh
scripts/validate-change.sh
```

CI has three fail-closed modes:

- `fast` for proven non-image inputs such as documentation, ordinary tests,
  release/canary workflows, and canary manifests; no rootfs is constructed;
- `material` for the source lock, builder/extractor/archive/SBOM code, overlay,
  native/PRoot validation code, recipe code, or any unknown path; each target is
  constructed once;
- `release` only when the commit updates a valid `release-intent.json` whose
  declared version, exact target set, and material-input fingerprint match the
  checked-out tree; each target is constructed twice in clean output trees.

Material and release CI then:

- compares every output byte-for-byte in release mode;
- executes the embedded static helper directly on the native ARM64 host;
- compares the locked OCI layer materialization with the exact PRoot-Distro
  v5.5.0 extractor at commit `0b2a3aa8dd88cd83f2cf681836c66f7bc6b22d26`;
- checks the single root directory and canonical metadata;
- verifies the embedded helper SHA-256, AArch64 ELF machine, static linkage,
  and lack of a dynamic interpreter;
- validates the shim's AArch64 shared-object shape, exported functions,
  loopback-only result, source digest, and provenance binding;
- boots the exact uploaded artifact under QEMU/PRoot in a separate job;
- resolves the production Liskov hostname;
- proves abstract Unix bridge-socket access and fail-closed exit status;
- exercises the static helper's bundled-root HTTPS path from the Debian image
  without installing a rootfs CA bundle or client;
- in release mode, packages checksums, metadata, `BUILD-MANIFEST.json`, and
  GitHub build attestations into a commit-named bundle retained for 90 days.

Tag publication requires the tag to point at the exact release-intended commit
and match its declared version. It locates the successful release-mode run,
verifies the manifest, checksums, complete target set, workflow/run identity,
source commit, and every attestation, then publishes those files unchanged.
Missing, expired, incomplete, additional, mismatched, or unattested files fail
closed. Release publication contains no construction or QEMU/PRoot job.

Successful local and CI smoke tests are necessary but not sufficient for
support. A release candidate becomes the maintained default only after a
bounded Acurast A/B canary: v4 control first, then the OCI-derived candidate,
with signed Liskov runtime contact and downstream command execution observed.

## Updating inputs

Changing any upstream image, helper version, manifest, config, or layer digest
requires:

1. updating `sources.lock.json`;
2. reviewing the source and overlay diff;
3. updating `release-intent.json` in the release-intended material commit (or
   in a subsequent qualification-only commit) with the fingerprint printed by
   `python3 scripts/change-classifier.py fingerprint`;
4. passing the two-clean-build native ARM64 and QEMU/PRoot release CI;
5. tagging that exact commit with the declared release version so publication
   reuses the qualified bundle;
6. running a new bounded Acurast canary before promotion.

Never replace an existing release asset in place.
