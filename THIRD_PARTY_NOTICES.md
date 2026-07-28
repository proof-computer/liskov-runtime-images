# Third-party materials

The Apache-2.0 license in this repository applies to the repository's build
code and documentation. It does not relicense the operating-system packages
inside generated rootfs images.

The `v4-control` image starts from the exact Ubuntu Questing AArch64 rootfs
published by Termux PRoot-Distro v4.30.1. PRoot-Distro is GPL-3.0-only; Ubuntu
packages retain their respective licenses.

The maintained candidate starts from the exact Debian Trixie slim AArch64 OCI
manifest recorded in `sources.lock.json`. Debian packages retain their
respective licenses.

The overlaid `liskov-runtime-contact` binary and its accompanying license are
published by `proof-computer/liskov-runtime-cargo` under Apache-2.0.

Each release includes an SPDX SBOM and an exact source/overlay provenance
record. The files installed by Debian and Ubuntu contain the authoritative
package copyright and license notices.
