# liskov-runtime-images

Reproducible, provenance-rich AArch64 rootfs images for Liskov-managed Acurast
Cargo/PRoot workloads.

This repository makes the complete image transformation public. Every release
starts from immutable upstream bytes, verifies every source digest before
extraction, overlays the loopback-only Acurast `getifaddrs` compatibility shim,
and emits a deterministic plain `tar.xz` rootfs accepted by Acurast Cargo.
Runtime images deliberately contain no `liskov-runtime-contact` helper; Liskov
snapshots one verified helper beside the generated `acurast.sh` launch bundle.

## Image tracks

| Target | Upstream trust root | Status |
| --- | --- | --- |
| `debian-trixie-snapshot` | Debian `trixie` bootstrapped by a pinned `mmdebstrap` from an immutable `snapshot.debian.org` timestamp, inside a digest-pinned builder container | **Maintained default**; exact `v0.1.0-rc.13` bytes promoted after the bounded Acurast A/B canary (ADR-0171). A security rebuild is a snapshot-timestamp bump |
| `ubuntu-resolute-snapshot` | Ubuntu 26.04 (`resolute`) arm64 bootstrapped from the pinned `20261001T000000Z` base, updates, and security pockets, with a digest-pinned Ubuntu archive keyring | Release candidate only. Not the maintained default, and not the catalogue name `ubuntu-resolute` |
| `debian-trixie` | Exact official Debian `trixie-slim` AArch64 OCI platform-manifest, config, and layer digests | A/B control; previously the maintained default (`v0.1.0-rc.12`, byte-identical in `v0.1.0-rc.13`) |
| `v4-control` | Exact Termux PRoot-Distro v4.30.1 Ubuntu Questing AArch64 release asset | Compatibility control only |

Acurast consumes an image URL and SHA-256, not a PRoot-Distro major version.
PRoot-Distro v5 no longer publishes distribution rootfs assets; it materializes
OCI images instead, and neither upstream carries a security rebuild of a
published release. The maintained track therefore builds Debian itself from a
pinned archive snapshot (see *The snapshot lane* below). The OCI lane is kept
as the A/B control and records PRoot-Distro v5.5.0 as a compatibility
reference. It does not publish host-specific output from PRoot-Distro
`install` or its restore-oriented `backup` format.

The v4 control is deliberately not the maintained default. Its upstream asset
is retained to distinguish an Acurast archive/PRoot compatibility failure from
a newer OCI-rootfs problem.

### The snapshot lane

Upstream stopped publishing PRoot rootfs tarballs (the last Termux v4 assets
are from December 2025; v5 publishes none), so neither upstream lane can carry
a security rebuild. The `apt-snapshot` kind builds the rootfs itself:

1. `scripts/mmdebstrap-docker.sh` starts the digest-pinned builder image,
   points apt at the locked snapshot timestamp, installs the locked
   `mmdebstrap` and `debian-archive-keyring` versions from that same
   snapshot, verifies the keyring digest, and runs `mmdebstrap` in `root`
   mode with the locked suite, components, variant and include list, writing
   a plain tar to standard output.
2. `scripts/build-image.py` verifies the snapshot `InRelease` digest, extracts
   the tar with the same traversal-safe extractor as the other lanes, writes
   the declared fixups (`/etc/hostname`, `/etc/hosts`, `/etc/resolv.conf`,
   which `mmdebstrap` would otherwise copy from the build host), removes the
   declared apt cache files, and continues with the common overlay, canonical
   archive, inventory, SBOM and provenance steps.

The recipe, the bootstrap archive digest and every applied fixup are recorded
under `aptSnapshot` in the provenance record. Set
`LISKOV_APT_SNAPSHOT_RUNNER` to substitute the container runner.

`ubuntu-resolute-snapshot` uses that same builder through the multi-pocket
path. Before bootstrap it checks the three `20261001T000000Z` pockets
(`resolute`, `resolute-updates`, `resolute-security`) and the digest-pinned
`ubuntu-keyring` package, then applies the declared hostname, hosts, and
resolver fixups and the apt-cache removals. Provenance records the snapshot
timestamp, the three signed releases, the resolved package versions, and those
fixups. The candidate is not promoted and does not register the catalogue
name `ubuntu-resolute`.

## Declared Liskov overlay

The base filesystem receives exactly these Liskov-owned paths:

```text
/usr/local/lib/libgetifaddrs_override.so
/usr/share/liskov-runtime-images/getifaddrs_override.c
/usr/share/liskov-runtime-images/provenance.json
```

The [PRoot-Distro fixup ledger](docs/proot-fixup-ledger.md) records the
per-distro keep/drop decisions. These three paths remain the whole common
overlay; snapshot-specific filesystem fixups are declared separately in
[`sources.lock.json`](sources.lock.json).
The [Ubuntu Resolute snapshot source contract](docs/ubuntu-resolute-snapshot-source.md)
records the verified `20261001T000000Z` arm64 archive pockets and keyring used
by `ubuntu-resolute-snapshot`.

The shim is compiled deterministically from the included source and
implements the loopback-only workaround documented for Cargo/PRoot by
[Acurast](https://docs.acurast.com/developers/build/cargo-runtime-environment/#network-interfaces-getifaddrs).
Liskov's bootstrap exports it through `LD_PRELOAD` only when the verified
library is present in the rootfs.
The embedded provenance record identifies the upstream material, base
inventory, shim source and binary, and deterministic archive policy. Numeric ownership,
timestamps, member order, and compression are normalized and declared
separately from the filesystem overlay.

Operating-system packages retain their original licenses. See
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and each release's SPDX SBOM.

## License

Repository-authored code, scripts, the shipped `getifaddrs` shim source, and
documentation are licensed under the Functional Source License, Version 1.1,
with Apache-2.0 as the future licence (SPDX `FSL-1.1-Apache-2.0`). The text is
[`LICENSE`](LICENSE).

You may use, copy, modify, create derivative works, publicly perform, publicly
display, and redistribute that material for any Permitted Purpose. A Permitted
Purpose is any purpose other than a Competing Use: making the software
available to others in a commercial product or service that substitutes for the
software, substitutes for another product or service we offer using it, or
offers the same or substantially similar functionality. Each version may also
be used under Apache-2.0 on the second anniversary of the date it was made
available.

Earlier image revisions keep the Apache-2.0 licence they shipped with.
Operating-system packages inside a generated rootfs keep their upstream
licences.

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
three declared additions. `BUILD-MANIFEST.json` binds the repository, source
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
python3 scripts/build-image.py ubuntu-resolute-snapshot --output-dir out/ubuntu-resolute-snapshot
python3 scripts/build-image.py v4-control --output-dir out/v4-control
```

Prove two independent materializations are byte-identical:

```sh
scripts/verify-reproducible.sh debian-trixie
scripts/verify-reproducible.sh ubuntu-resolute-snapshot
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
- compares the locked OCI layer materialization with the exact PRoot-Distro
  v5.5.0 extractor at commit `0b2a3aa8dd88cd83f2cf681836c66f7bc6b22d26`;
- checks the single root directory and canonical metadata;
- rejects the removed helper and license paths, including symlink and hard-link
  aliases;
- validates the shim's AArch64 shared-object shape, exported functions,
  loopback-only result, source digest, and provenance binding;
- boots the exact uploaded artifact under QEMU/PRoot in a separate job, using
  the public Termux PRoot `v5.1.107.72` base that Acurast processors bundle,
  built from its digest-pinned source by `scripts/build-termux-proot.sh`
  (distribution PRoot 5.1.0 cannot translate `statx`, which Rust coreutils
  uses for every file lookup);
- resolves the production Liskov hostname;
- proves abstract Unix bridge-socket access and fail-closed exit status;
- downloads one exact released helper as test-only material, verifies its
  digest, size, AArch64 ELF machine, static linkage, and lack of a dynamic
  interpreter, then injects it beside a production-shaped generated
  `acurast.sh` in an ephemeral smoke root;
- exercises that generated launcher through bridge, conditional preload,
  bundled-root HTTPS, bridge-probe, and downstream-command handoff paths
  without adding the helper to a published rootfs;
- in release mode, packages checksums, metadata, `BUILD-MANIFEST.json`, and
  GitHub build attestations into a commit-named bundle retained for 90 days.

Tag publication requires the tag to point at the exact release-intended commit
and match its declared version. It locates the successful release-mode run,
verifies the manifest, checksums, complete target set, workflow/run identity,
source commit, and every attestation, then publishes those files unchanged.
Missing, expired, incomplete, additional, mismatched, or unattested files fail
closed. Release publication contains no construction or QEMU/PRoot job.

New `BUILD-MANIFEST.json` emission uses schema 2. The repository release tag
is the **bundle** identity. The `catalogue` records independently bind
`debian-trixie-snapshot` to the name `debian-trixie`, and
`ubuntu-resolute-snapshot` to `ubuntu-resolute`. The OCI `debian-trixie` and
`v4-control` targets remain diagnostic controls and carry no catalogue version.
A record includes its numeric `MAJOR.MINOR.PATCH`, exact archive SHA-256 and
complete target file set. The manifest's shared `source` and `workflow`
coordinates bind every record to the attested source commit, signer workflow
ref, run ID and run attempt. Attestation verification remains mandatory before
trusting these declarations. A bundle record does not register or promote a
customer catalogue name; registration belongs to the control plane.

Schema-2 release intent retains the existing `version`, `targets` and
`materialInputFingerprint` fields and adds an explicit `catalogue` list:

```json
{
  "target": "debian-trixie-snapshot",
  "name": "debian-trixie",
  "version": "0.1.0",
  "archiveSha256": "<exact candidate archive SHA-256>",
  "previousReleases": [],
  "versionDecision": null
}
```

Include exactly one record for each snapshot target, initially `0.1.0` for
both names. For a subsequent candidate, copy the complete prior identity
history from verified, attested schema-2 bundles into `previousReleases` as
`{"version":"0.1.0","archiveSha256":"<verified digest>"}` records. This is
input evidence, not a catalogue database or an automatic discovery of published
releases: callers must provide the verified history. The validator refuses a
known name/version with different bytes, backwards versions and skipped patch
increments. An unchanged name/version may be carried forward only with its
identical digest; a rebuild advances that name's patch by one independently
of the other name. A new major/minor requires a recorded `versionDecision`
such as `ADR-0200`, backed by the referenced decision. Distro names remain
immutable. Neither prereleases nor build metadata nor shortened numeric
versions are catalogue versions.

The existing `build-manifest.py create` and `validate` commands load this map
from the supplied `--root`'s release intent, compare the exact archive digests,
and reject missing, duplicate or additional names. They work with local fixture
files as well as qualified images; `tests/test_ci_contract.py` exercises that
CLI without a release. New emission requires schema-2 intent. The current
`release-intent.json` stays unchanged in this schema change; the separate exact
candidate packet will supply schema-2 intent and known candidate digests.
Validation retains the strict schema-1 reader, including historical bundles
from before the Ubuntu target, without assigning catalogue meanings to their
bundle versions or rewriting their keys or assets.

Successful local and CI smoke tests are necessary but not sufficient for
support. A release candidate becomes the maintained default only after a
bounded Acurast A/B canary: a control image and the candidate on the same
manager pool, each with signed Liskov runtime contact, downstream command
execution, and finalized Acurast execution success observed.

The maintained default is the Liskov-authored `debian-trixie-snapshot`
archive from `v0.1.0-rc.13`, SHA-256
`ac69afc5c8737db519f653f42f150c814cfd2c1173bececa15fc32258a8e2f9a`,
attested from source commit `e061dfd03f430657b4e937d91cfc804b1670d0f5`.
It was promoted without rebuilding on 2026-10-02 after it and the OCI-derived
`debian-trixie` control (`0639e88d…8377`) both crossed signed runtime contact,
customer-command handoff, and corroborated Acurast execution success on the
same processor (jobs 191395 and 191339). The release tag remains
`v0.1.0-rc.13` because its checked build manifest, checksums, and
attestations are version-bound; the GitHub release notes and this pointer
carry the maintained-default classification.

The previous maintained default, `debian-trixie` from `v0.1.0-rc.12`
(`0639e88d…8377`), stays published and immutable.

## Updating inputs

Changing any upstream image, manifest, config, or layer digest
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
