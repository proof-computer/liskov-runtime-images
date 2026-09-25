#!/usr/bin/env bash
# Materialize a Debian-family rootfs from an immutable archive snapshot inside
# a digest-pinned builder container, and write the resulting plain tar to
# standard output. Every input is passed through the LISKOV_APT_* environment
# variables that scripts/build-image.py sets from sources.lock.json; the
# script itself pins nothing.
set -euo pipefail

for variable in \
  LISKOV_APT_BUILDER_IMAGE \
  LISKOV_APT_MMDEBSTRAP_PACKAGE \
  LISKOV_APT_MMDEBSTRAP_VERSION \
  LISKOV_APT_KEYRING_PACKAGE \
  LISKOV_APT_KEYRING_VERSION \
  LISKOV_APT_KEYRING_PATH \
  LISKOV_APT_KEYRING_SHA256 \
  LISKOV_APT_ARCHIVE_URL \
  LISKOV_APT_SUITE \
  LISKOV_APT_COMPONENTS \
  LISKOV_APT_VARIANT \
  LISKOV_APT_INCLUDE \
  LISKOV_APT_ARCHITECTURE \
  SOURCE_DATE_EPOCH; do
  if [[ -z "${!variable:-}" ]]; then
    echo "${variable} must be set" >&2
    exit 2
  fi
done

docker run --rm \
  --platform "linux/${LISKOV_APT_ARCHITECTURE}" \
  --env LISKOV_APT_MMDEBSTRAP_PACKAGE \
  --env LISKOV_APT_MMDEBSTRAP_VERSION \
  --env LISKOV_APT_KEYRING_PACKAGE \
  --env LISKOV_APT_KEYRING_VERSION \
  --env LISKOV_APT_KEYRING_PATH \
  --env LISKOV_APT_KEYRING_SHA256 \
  --env LISKOV_APT_ARCHIVE_URL \
  --env LISKOV_APT_SUITE \
  --env LISKOV_APT_COMPONENTS \
  --env LISKOV_APT_VARIANT \
  --env LISKOV_APT_INCLUDE \
  --env LISKOV_APT_ARCHITECTURE \
  --env SOURCE_DATE_EPOCH \
  "${LISKOV_APT_BUILDER_IMAGE}" \
  sh -eu -c '
    # The builder toolchain comes from the same immutable snapshot as the
    # image, so both are pinned by one timestamp.
    printf "deb [check-valid-until=no] %s %s %s\n" \
      "${LISKOV_APT_ARCHIVE_URL}" "${LISKOV_APT_SUITE}" \
      "$(printf "%s" "${LISKOV_APT_COMPONENTS}" | tr "," " ")" \
      > /etc/apt/sources.list
    rm -f /etc/apt/sources.list.d/*
    apt-get update -qq >&2
    DEBIAN_FRONTEND=noninteractive apt-get install -qq --yes --no-install-recommends \
      "${LISKOV_APT_MMDEBSTRAP_PACKAGE}=${LISKOV_APT_MMDEBSTRAP_VERSION}" \
      "${LISKOV_APT_KEYRING_PACKAGE}=${LISKOV_APT_KEYRING_VERSION}" >&2
    actual=$(sha256sum "${LISKOV_APT_KEYRING_PATH}" | cut -d " " -f 1)
    if [ "${actual}" != "${LISKOV_APT_KEYRING_SHA256}" ]; then
      echo "archive keyring digest mismatch: ${actual}" >&2
      exit 1
    fi
    exec mmdebstrap \
      --mode=root \
      --format=tar \
      --variant="${LISKOV_APT_VARIANT}" \
      --architectures="${LISKOV_APT_ARCHITECTURE}" \
      --components="${LISKOV_APT_COMPONENTS}" \
      --include="${LISKOV_APT_INCLUDE}" \
      --keyring="${LISKOV_APT_KEYRING_PATH}" \
      --aptopt="Acquire::Check-Valid-Until \"false\"" \
      --logfile=/dev/stderr \
      "${LISKOV_APT_SUITE}" - "${LISKOV_APT_ARCHIVE_URL}"
  '
