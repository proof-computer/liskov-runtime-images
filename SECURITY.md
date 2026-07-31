# Security

Please report vulnerabilities privately through GitHub's security-advisory
flow for this repository. Do not open a public issue containing exploit details
or credentials.

Release consumers should verify both `SHA256SUMS` and GitHub artifact
attestations before using an image. A release archive is immutable; source-lock
updates produce a new release rather than changing an existing asset.

The curated image controls rootfs provenance. It does not turn PRoot into a
hardware-isolated runtime and does not replace Acurast processor or Liskov
runtime-contact verification. Published images contain no runtime-contact
helper; the Liskov launch bundle supplies an independently verified helper.
