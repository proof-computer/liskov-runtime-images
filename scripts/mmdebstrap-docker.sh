#!/usr/bin/env bash
# Materialize a Debian-family rootfs from an immutable archive snapshot inside
# a digest-pinned builder container, and write the resulting plain tar to
# standard output. Every input is passed through the LISKOV_APT_* environment
# variables that scripts/build-image.py sets from sources.lock.json; the
# script itself pins nothing.
#
# LISKOV_APT_POCKETS selects the multi-pocket path. The one-pocket Debian path
# below it is unchanged: one apt source, and the keyring package installed
# from that same snapshot.
set -euo pipefail

if [[ -n "${LISKOV_APT_POCKETS:-}" ]]; then
  for variable in \
    LISKOV_APT_BUILDER_IMAGE \
    LISKOV_APT_MMDEBSTRAP_PACKAGE \
    LISKOV_APT_MMDEBSTRAP_VERSION \
    LISKOV_APT_SUITE \
    LISKOV_APT_COMPONENTS \
    LISKOV_APT_VARIANT \
    LISKOV_APT_INCLUDE \
    LISKOV_APT_ARCHITECTURE \
    LISKOV_APT_POCKET_LINES \
    LISKOV_APT_FOREIGN_KEYRING_URL \
    LISKOV_APT_FOREIGN_KEYRING_PACKAGE_SHA256 \
    LISKOV_APT_FOREIGN_KEYRING_MEMBER \
    LISKOV_APT_FOREIGN_KEYRING_SHA256 \
    SOURCE_DATE_EPOCH; do
    if [[ -z "${!variable:-}" ]]; then
      echo "${variable} must be set" >&2
      exit 2
    fi
  done

  # The foreign archive keyring is downloaded from its locked snapshot URL
  # and checked before apt can see it. It is never installed from the
  # Debian builder image's apt source.
  docker run --rm \
    --platform "linux/${LISKOV_APT_ARCHITECTURE}" \
    --env LISKOV_APT_MMDEBSTRAP_PACKAGE \
    --env LISKOV_APT_MMDEBSTRAP_VERSION \
    --env LISKOV_APT_SUITE \
    --env LISKOV_APT_COMPONENTS \
    --env LISKOV_APT_VARIANT \
    --env LISKOV_APT_INCLUDE \
    --env LISKOV_APT_ARCHITECTURE \
    --env LISKOV_APT_POCKET_LINES \
    --env LISKOV_APT_FOREIGN_KEYRING_URL \
    --env LISKOV_APT_FOREIGN_KEYRING_PACKAGE_SHA256 \
    --env LISKOV_APT_FOREIGN_KEYRING_MEMBER \
    --env LISKOV_APT_FOREIGN_KEYRING_SHA256 \
    --env SOURCE_DATE_EPOCH \
    "${LISKOV_APT_BUILDER_IMAGE}" \
    sh -eu -c '
      apt-get update -qq >&2
      DEBIAN_FRONTEND=noninteractive apt-get install -qq --yes --no-install-recommends \
        "${LISKOV_APT_MMDEBSTRAP_PACKAGE}=${LISKOV_APT_MMDEBSTRAP_VERSION}" \
        curl \
        ca-certificates >&2
      work=$(mktemp -d)
      curl -fLSs "${LISKOV_APT_FOREIGN_KEYRING_URL}" -o "${work}/keyring.deb"
      echo "${LISKOV_APT_FOREIGN_KEYRING_PACKAGE_SHA256}  ${work}/keyring.deb" | sha256sum -c >&2
      dpkg-deb -x "${work}/keyring.deb" "${work}/root"
      member="${LISKOV_APT_FOREIGN_KEYRING_MEMBER#./}"
      case "${member}" in
        /*|*..*)
          echo "foreign keyring member path is unsafe: ${member}" >&2
          exit 1
          ;;
      esac
      keyring="${work}/root/${member}"
      if [ ! -f "${keyring}" ]; then
        echo "foreign keyring package is missing ${member}" >&2
        exit 1
      fi
      actual=$(sha256sum "${keyring}" | cut -d " " -f 1)
      if [ "${actual}" != "${LISKOV_APT_FOREIGN_KEYRING_SHA256}" ]; then
        echo "foreign archive keyring digest mismatch: expected ${LISKOV_APT_FOREIGN_KEYRING_SHA256}, got ${actual}" >&2
        exit 1
      fi
      signed_by="/${member}"
      mkdir -p "$(dirname "${signed_by}")"
      cp "${keyring}" "${signed_by}"
      timestamp=$(printf "%s\n" "${LISKOV_APT_POCKET_LINES}" | sed -n "s/.*\\([0-9]\\{8\\}T[0-9]\\{6\\}Z\\).*/\\1/p" | head -n 1)
      if [ -z "${timestamp}" ]; then
        echo "apt-snapshot pocket lines have no snapshot timestamp" >&2
        exit 1
      fi
      components=$(printf "%s" "${LISKOV_APT_COMPONENTS}" | tr "," " ")
      set --
      while IFS="|" read -r suite archive_url; do
        [ -n "${suite}" ] || continue
        case "${archive_url}" in
          *"${timestamp}"*) ;;
          *)
            echo "mixed-timestamp apt-snapshot pocket: ${archive_url}" >&2
            exit 1
            ;;
        esac
        set -- "$@" "deb [signed-by=${signed_by} check-valid-until=no] ${archive_url} ${suite} ${components}"
      done <<EOF
${LISKOV_APT_POCKET_LINES}
EOF
      if [ "$#" -eq 0 ]; then
        echo "apt-snapshot pockets are empty" >&2
        exit 1
      fi
      exec mmdebstrap \
        --mode=root \
        --format=tar \
        --variant="${LISKOV_APT_VARIANT}" \
        --architectures="${LISKOV_APT_ARCHITECTURE}" \
        --include="${LISKOV_APT_INCLUDE}" \
        --keyring="${signed_by}" \
        --aptopt="Acquire::Check-Valid-Until \"false\"" \
        --skip=check/signed-by \
        --setup-hook="copy-in ${signed_by} $(dirname "${signed_by}")" \
        --logfile=/dev/stderr \
        "${LISKOV_APT_SUITE}" - "$@"
    '
  exit $?
fi

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
