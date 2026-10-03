# Ubuntu Resolute arm64 snapshot source contract

This is the source proof for `BKLG-20261003-r2km`. It establishes inputs for a
later `ubuntu-resolute` image-build packet; it does not add a target or select a
release snapshot. The verified probe is the Ubuntu Snapshot Service at
`20261001T000000Z`, read on 2026-10-03. The
[official snapshot guide](https://snapshot.ubuntu.com/) defines timestamp IDs;
the [Ubuntu Server guide](https://ubuntu.com/server/docs/how-to/software/snapshot-service/)
shows separate base, updates and security suites at one snapshot ID.

## Verified arm64 archive pockets

The common base URL for this probe is
`https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/`. Each URL in this table
returned HTTP 200 on both GET and HEAD. The `InRelease` files were verified
with `gpgv` using the digest-pinned keyring below (exit 0 for each). Each
downloaded `main/binary-arm64/Packages.xz` matched the SHA-256 and byte count
in its suite's signed `InRelease` `SHA256:` section.

| Suite | `dists/<suite>/InRelease` SHA-256 | `dists/<suite>/main/binary-arm64/Packages.xz` SHA-256 | Index bytes |
| --- | --- | --- | ---: |
| `resolute` | `45f95ce276cdba3e41870516a130e03c58b8b7a79e9546b0efe9e526d255740c` | `537715817d9ed77cc919f5123edb91a48d9ae97abf05588a8f18208cc7227e08` | 1,468,492 |
| `resolute-updates` | `802e675dd9de4c7f3916434a95e7c1d8eec0e82886622d7805ab19a2c6fe0365` | `e6cd17330f8825769e786b1e158013c97a5b75263a05e768859898373b0ef11d` | 700,712 |
| `resolute-security` | `1d5041572116a8b23aabf79ac7439ad8af83d57ad3fb0f9aa0d4523ec10c5908` | `2f085228f01710e54bf6594b985a0dbfd9940277a8b5d9604cde98536363863a` | 568,596 |

The full URL for each item is the base URL followed by its table column's
path, for example
`https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/dists/resolute-security/main/binary-arm64/Packages.xz`.
All three signed files identify `Origin: Ubuntu`, include `arm64` in
`Architectures`, and list `main` in `Components`. The base `InRelease` is dated
2026-04-23; the updates and security files are dated 2026-09-30. The guessed
`/ubuntu-ports/20261001T000000Z/dists/resolute/InRelease` path returned HTTP
401; the verified arm64 indexes are under `/ubuntu/`.

## Keyring and signature proof

The signed `resolute` arm64 `main` package index names `ubuntu-keyring`
version `2023.11.28.1build1` (`Architecture: all`) at
`pool/main/u/ubuntu-keyring/ubuntu-keyring_2023.11.28.1build1_all.deb`.
At the same snapshot URL, that package returned HTTP 200 and its SHA-256 was
`c377ccf26964f4c206c05c0bc7adb708e031beb919bb9a0ff63af983d064cd66`,
matching the index record. The extracted
`/usr/share/keyrings/ubuntu-archive-keyring.gpg` SHA-256 was
`80a36b0a6de2f69f49d2df75ef473ccde121e9e190b9ea01d20a4f63778d5c31`.
`gpgv` returned 0 on all three `InRelease` files with that keyring and reported
signing key fingerprint `F6ECB3762474EDA9D21B7022871920D1991BC93C`
(`Ubuntu Archive Automatic Signing Key (2018)`).

For the future builder, the **reviewed, locked keyring SHA-256 is the initial
trust anchor**. The package-index match checks that the archive serves the
same keyring package; it does not independently establish trust in a keyring
downloaded from the archive it verifies. The builder must check both the
package SHA-256 and extracted keyring SHA-256 before trusting archive metadata.

## Reproduce the readback

These commands use filenames without spaces and work in fish or a POSIX shell.
Repeat the download for each suite and compare its hashes with the table.

```sh
curl -fLSs https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/dists/resolute/InRelease -o resolute.InRelease
curl -fLSs https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/dists/resolute/main/binary-arm64/Packages.xz -o resolute.Packages.xz
curl -fLSs https://snapshot.ubuntu.com/ubuntu/20261001T000000Z/pool/main/u/ubuntu-keyring/ubuntu-keyring_2023.11.28.1build1_all.deb -o ubuntu-keyring.deb
sha256sum resolute.InRelease resolute.Packages.xz ubuntu-keyring.deb
ar p ubuntu-keyring.deb data.tar.zst > ubuntu-keyring-data.tar.zst
tar --zstd -xf ubuntu-keyring-data.tar.zst ./usr/share/keyrings/ubuntu-archive-keyring.gpg
sha256sum usr/share/keyrings/ubuntu-archive-keyring.gpg
gpgv --keyring usr/share/keyrings/ubuntu-archive-keyring.gpg resolute.InRelease
```

The download hashes are the base-suite and package hashes above; the keyring
hash is the extracted-file hash above. The `gpgv` command exited 0. Repeating
the last command for `resolute-updates.InRelease` and
`resolute-security.InRelease` also exited 0. For each index, compare the
downloaded hash and byte count with its signed `InRelease` `SHA256:` entry for
`main/binary-arm64/Packages.xz`; all three comparisons passed. Do not treat
HTTP 200 alone as archive authenticity.

## Proposed image-source contract

- **Name and source:** `ubuntu-resolute`, Ubuntu 26.04, `arm64`, exact
  `snapshot.ubuntu.com/ubuntu/<timestamp>/` URL. Use `resolute`,
  `resolute-updates` and `resolute-security` from the **same** snapshot ID,
  `main` component only. The probe ID is not a production lock or a version.
- **Keyring and package set:** pin the `ubuntu-keyring` package version and
  package SHA-256, plus the extracted archive-keyring SHA-256; pin the names of
  the included runtime packages (the Debian target uses `ca-certificates` and
  `curl`). The Ubuntu builder can retain a digest-pinned Debian tool container
  for `mmdebstrap`, but it must acquire and verify the Ubuntu keyring without
  assuming `apt-get install ubuntu-keyring` from Debian sources works.
- **Package versions:** the snapshot ID plus each signed pocket index pins
  the candidate package versions. Resolve the fixed package-name set across
  the three pockets at that ID, and record the resolved versions and source
  pocket in the image inventory/SBOM and provenance. At this probe,
  `ca-certificates` is `20260223` in base and `20260601~26.04.1` in both
  updates/security; `curl` is `8.18.0-1ubuntu2` in base and
  `8.18.0-1ubuntu2.7` in both updates/security. A release build must pin its
  own snapshot ID and all three `InRelease` digests before construction;
  moving the ID is a new revision, not a mutable archive read.
- **Lock and builder delta:** today's `sources.lock.json` has one
  `snapshot.inReleaseUrl`/`inReleaseSha256` and one `suite`; the builder writes
  one apt source. The Ubuntu packet should represent three named pockets, each
  with exact suite, URL and `InRelease` digest, and verify the signed index
  hash before package resolution. Feed `mmdebstrap` explicit apt source entries
  for all three suites at one timestamp and the pinned Ubuntu keyring. The
  [mmdebstrap manual](https://manpages.debian.org/trixie/mmdebstrap/mmdebstrap.1.en.html)
  documents a sources-list file or multiple mirror entries; do not rely on
  implicit suite-updates/security discovery. Preserve the normalized archive,
  inventory, SBOM, provenance, two-build reproduction and native/QEMU smoke.
- **Guest fixups:** declare deterministic `/etc/hosts` and
  `/etc/resolv.conf` as the Ubuntu `keep` paths from the
  [PRoot-Distro ledger](proot-fixup-ledger.md). Decide and declare
  `/etc/hostname` and apt-cache removals from Ubuntu bootstrap evidence, as the
  Debian target does. The three common shim/provenance overlay paths remain
  unchanged. Do not import the v4 Mozilla PPA, Termux `aid_*` identities,
  Android binds or login profile snippets.

This readback verifies source metadata and package-index integrity. It does
not prove dependency resolution, an Ubuntu image build, QEMU/PRoot behavior,
an Acurast canary or promotion. Those are the code and person-run stages that
follow.
