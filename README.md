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
  --repo proof-computer/liskov-runtime-images
```

Each target publishes:

```text
<stem>.tar.xz
<stem>.files.json
<stem>.overlay.json
<stem>.provenance.json
<stem>.spdx.json
```

`files.json` inventories every archive member, including symlinks, hardlinks,
device metadata, and extended-attribute digests. `overlay.json` isolates the
five declared additions. GitHub's artifact attestation binds the final files
to the public workflow and source commit.

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

The source lock contains no mutable trust anchor. For the OCI image, the build
downloads the exact platform manifest, verifies its SHA-256, requires the exact
locked config and layer descriptors, verifies every blob, validates
`linux/arm64/v8`, applies OCI whiteouts, and only then creates the rootfs.

## Validation

Offline unit tests cover source-lock structure, traversal rejection,
symlink-parent rejection, OCI whiteouts, symlink inventory binding, and
canonical archive reproduction:

```sh
make test
```

GitHub CI builds every image twice on a public native ARM64 runner. It then:

- compares every output byte-for-byte;
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
- publishes checksums, metadata, and GitHub build attestations.

Successful local and CI smoke tests are necessary but not sufficient for
support. A release candidate becomes the maintained default only after a
bounded Acurast A/B canary: v4 control first, then the OCI-derived candidate,
with signed Liskov runtime contact and downstream command execution observed.

## Updating inputs

Changing any upstream image, helper version, manifest, config, or layer digest
requires:

1. updating `sources.lock.json`;
2. reviewing the source and overlay diff;
3. reproducing both builds;
4. passing native ARM64 and QEMU/PRoot CI;
5. publishing a release candidate;
6. running a new bounded Acurast canary before promotion.

Never replace an existing release asset in place.
