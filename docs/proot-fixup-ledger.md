# PRoot-Distro fixup ledger

This is the source inventory for `BKLG-20260925-tc5t`, not a build recipe.
The reference is PRoot-Distro v5 at
[`c76286a2fb57884dae11bc2ea469949bbcdec135`](https://github.com/termux/proot-distro/tree/c76286a2fb57884dae11bc2ea469949bbcdec135).
Its install path has no Debian or Ubuntu plugin: after extraction, the same
three rootfs writers run for both distributions. The v4-only rows below use
[`v4.38.0`](https://github.com/termux/proot-distro/tree/v4.38.0) and are
identified separately. Paths are guest paths unless marked as container state.

**Verdicts:** `keep` means the package-archive image must declare or retain the
named guest path. `drop` means the Termux behavior is unnecessary under Acurast
Cargo/PRoot. `already ours` means Liskov already owns the Acurast-specific
shim; PRoot-Distro does not supply it. A `keep` row that is already implemented
points to the current [snapshot lock](../sources.lock.json), so it does not
imply another overlay change. Liskov's fixup contents need not copy Termux's
host-dependent bytes.

## Debian

| Stage and upstream source | Guest path or behavior | Verdict and reason |
| --- | --- | --- |
| v5 install, [`install.py:340-344`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/install.py#L340-L344), [`rootfs.py:184-205`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/helpers/rootfs.py#L184-L205) | Replace `/etc/resolv.conf` with a plain file containing default nameservers. | **keep** `/etc/resolv.conf`: DNS must work without a Termux-generated file. The current snapshot lock already declares it with Liskov-owned contents. |
| v5 install, [`install.py:346-347`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/install.py#L346-L347), [`rootfs.py:189-221`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/helpers/rootfs.py#L189-L221) | Replace `/etc/hosts` with loopback host entries. | **keep** `/etc/hosts`: local name resolution must be deterministic. The current snapshot lock already declares it. |
| v5 install, [`install.py:349-358`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/install.py#L349-L358), [`rootfs.py:240-270`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/helpers/rootfs.py#L240-L270) | Append the installing Termux user's `aid_*` entry to `/etc/passwd` and `/etc/shadow`; chmod identity files to `0644`. | **drop**: the host UID is processor-specific, and Cargo launches the workload through PRoot rather than a Termux login account. Preserve the distro's own identity files. |
| v5 install, [`rootfs.py:272-290`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/helpers/rootfs.py#L272-L290) | Append installing host groups to `/etc/group` and optional `/etc/gshadow`. | **drop**: those Android/Termux group IDs are host-specific; preserve the distro's own groups. |
| v5 install, [`install.py:362`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/install.py#L362), [`sysdata.py:392-425`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/sysdata.py#L392-L425) | Create `sysdata/` beside the rootfs for fake `/proc` and `/sys` entries. | **drop**: this is container state, not image content. Cargo binds `/proc` and `/sys` at launch. |
| v5 first login, [`login/__init__.py:420-430`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/__init__.py#L420-L430), [`login/env.py:176-203`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/env.py#L176-L203) | Write `/etc/profile.d/termux-profile.sh` to re-export the interactive Termux login environment. | **drop**: Cargo launches the command directly; no Termux login shell or profile export is required. |
| v5 first login, [`login/proot_cmd.py:218-239`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/proot_cmd.py#L218-L239), [`shm.py:60-115`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/shm.py#L60-L115) | Ensure guest `/tmp` exists; create sibling `shm/` and bind it to `/dev/shm`. | **drop** as a PRoot-Distro overlay: the current image smoke uses `/tmp`, while Cargo supplies the device binds. No sibling `shm/` belongs in the archive. |
| v5 first login, [`login/__init__.py:241-281`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/__init__.py#L241-L281), [`login/proot_cmd.py:168-189`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/proot_cmd.py#L168-L189), [`login/bindings.py:35-84`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/bindings.py#L35-L84) | Set Termux/Android environment and bind Android storage and system directories. | **drop**: these are launcher settings for Termux, not portable guest files; Cargo owns its own environment and binds. |
| v4-only common installer, [`proot-distro.sh:553-583`](https://github.com/termux/proot-distro/blob/v4.38.0/proot-distro.sh#L553-L583) | Write `/etc/environment`; rewrite PATH in `/etc/bash.bashrc`, `/etc/profile` and `/etc/login.defs`. | **drop**: v5 provides session environment at login; Cargo supplies its workload environment without Termux shell startup files. |
| v4-only plugin, [`debian.sh:16-20`](https://github.com/termux/proot-distro/blob/v4.38.0/distro-plugins/debian.sh#L16-L20) | Enable `en_US.UTF-8` in `/etc/locale.gen` and run `dpkg-reconfigure locales`. | **drop**: v5 has no distro plugin hook, and Cargo does not require an interactive login locale. Retain archive-provided locale packages and configuration. |
| Acurast compatibility, v5 launcher context [`login/proot_cmd.py:91-125`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/proot_cmd.py#L91-L125) | Loopback-only `getifaddrs` preload at `/usr/local/lib/libgetifaddrs_override.so`. | **already ours**: PRoot-Distro does not add this; Liskov's three-path overlay contains the source, library and provenance record. |

## Ubuntu

The v5 installer has no Ubuntu-specific branch. These rows deliberately repeat
the common v5 work so the Ubuntu archive lane has a complete path ledger.

| Stage and upstream source | Guest path or behavior | Verdict and reason |
| --- | --- | --- |
| v5 install, [`install.py:340-344`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/install.py#L340-L344), [`rootfs.py:184-205`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/helpers/rootfs.py#L184-L205) | Replace `/etc/resolv.conf` with default nameservers. | **keep** `/etc/resolv.conf`: declare deterministic DNS contents in the future Ubuntu snapshot lock. |
| v5 install, [`install.py:346-347`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/install.py#L346-L347), [`rootfs.py:189-221`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/helpers/rootfs.py#L189-L221) | Replace `/etc/hosts` with loopback host entries. | **keep** `/etc/hosts`: declare local host mappings in the future Ubuntu snapshot lock. |
| v5 install, [`install.py:349-358`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/install.py#L349-L358), [`rootfs.py:240-270`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/helpers/rootfs.py#L240-L270) | Append Termux `aid_*` identity to `/etc/passwd` and `/etc/shadow`; chmod identity files. | **drop**: processor-specific identities cannot be baked into a reusable image. Preserve Ubuntu's own files. |
| v5 install, [`rootfs.py:272-290`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/helpers/rootfs.py#L272-L290) | Append Termux host groups to `/etc/group` and `/etc/gshadow`. | **drop**: Android group IDs belong to the device, not the Ubuntu image. |
| v5 install, [`install.py:362`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/install.py#L362), [`sysdata.py:392-425`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/sysdata.py#L392-L425) | Create sibling `sysdata/` for fake `/proc` and `/sys` entries. | **drop**: Cargo binds `/proc` and `/sys`; sibling state is outside the image. |
| v5 first login, [`login/__init__.py:420-430`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/__init__.py#L420-L430), [`login/env.py:176-203`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/env.py#L176-L203) | Write `/etc/profile.d/termux-profile.sh` for later interactive logins. | **drop**: Cargo runs the workload directly, without Termux profile re-entry. |
| v5 first login, [`login/proot_cmd.py:218-239`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/proot_cmd.py#L218-L239), [`shm.py:60-115`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/shm.py#L60-L115) | Ensure `/tmp`; create sibling `shm/` for `/dev/shm`. | **drop** as an overlay: the image smoke uses `/tmp`, and runtime device binds are Cargo-owned. |
| v5 first login, [`login/__init__.py:241-281`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/__init__.py#L241-L281), [`login/proot_cmd.py:168-189`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/proot_cmd.py#L168-L189), [`login/bindings.py:35-84`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/bindings.py#L35-L84) | Set Termux/Android environment and bind Android storage and system paths. | **drop**: launcher-only Termux integration; no archive path. |
| v4-only common installer, [`proot-distro.sh:553-583`](https://github.com/termux/proot-distro/blob/v4.38.0/proot-distro.sh#L553-L583) | Write `/etc/environment`; rewrite PATH in `/etc/bash.bashrc`, `/etc/profile` and `/etc/login.defs`. | **drop**: Cargo supplies the workload environment; no Termux shell startup files are required. |
| v4-only plugin, [`ubuntu.sh:14-17`](https://github.com/termux/proot-distro/blob/v4.38.0/distro-plugins/ubuntu.sh#L14-L17) | Enable `en_US.UTF-8` in `/etc/locale.gen` and run `dpkg-reconfigure locales`. | **drop**: v5 has no distro hook; Cargo does not need an interactive login locale. Preserve Ubuntu's archive configuration. |
| v4-only plugin, [`ubuntu.sh:19-26`](https://github.com/termux/proot-distro/blob/v4.38.0/distro-plugins/ubuntu.sh#L19-L26) | Add Mozilla PPA and `/etc/apt/preferences.d/pin-mozilla-ppa`. | **drop**: browser PPA and its high-priority pin are unrelated to a Cargo runtime and would add a package trust root. |
| Acurast compatibility, v5 launcher context [`login/proot_cmd.py:91-125`](https://github.com/termux/proot-distro/blob/c76286a2fb57884dae11bc2ea469949bbcdec135/proot_distro/commands/login/proot_cmd.py#L91-L125) | Loopback-only `getifaddrs` preload at `/usr/local/lib/libgetifaddrs_override.so`. | **already ours**: this is Liskov's existing overlay, not a PRoot-Distro step. |

## Alpine (deferred)

The v4 Alpine plugin at
[`v4.38.0:distro-plugins/alpine.sh:1-16`](https://github.com/termux/proot-distro/blob/v4.38.0/distro-plugins/alpine.sh#L1-L16)
has no `distro_setup()` body. A musl-built shim and Alpine package-archive
source are separate later work; no Alpine verdict is promoted from the Debian
and Ubuntu tables.

## Scope of the comparison

The pinned v5 install path does **not** set a timezone or remove init or udev
hooks. Those items appear in ADR-0171's initial fixup examples, but there is no
operation for them in the inspected v5 install, rootfs helper, sysdata or
login paths. The v4 Debian and Ubuntu plugin bodies likewise do not do so.
They are not invented as overlay entries.

The current Debian snapshot recipe also declares `/etc/hostname` and removal
of `/var/cache/apt/pkgcache.bin` and `/var/cache/apt/srcpkgcache.bin` in
[`sources.lock.json`](../sources.lock.json). They address mmdebstrap host
inheritance and deterministic output; PRoot-Distro v5 does not apply them.
The three existing Liskov overlay paths remain the whole source/library/
provenance overlay. No privileged mount, device-node creation or setuid fixup
was found in the inspected v5 install path. PRoot `--bind` arguments are
runtime substitutions, not archive mutations.
