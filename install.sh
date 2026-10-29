#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Robert Tyree and hyper-focused
# Install pve-template-prep into /opt and link /usr/local/sbin/pve-template-prep.
# Fetch this file, read it, then: sudo bash install.sh
# Overrides: DEST, REPO, REF, BIN_LINK.

set -euo pipefail

# Closing notes. A pipe, NO_COLOR, or TERM=dumb stays plain.
# Success is a medium light green (xterm 78). Labels use the header cyan (xterm 45).
report_installed() {
  local version="$1"
  local green="" label="" bold="" reset=""
  if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-}" != "dumb" ]]; then
    bold=$'\033[1m'
    reset=$'\033[0m'
    if [[ "${TERM:-}" == "linux" || "${TERM:-}" == "eterm-color" ]]; then
      green=$'\033[92m'
      label=$'\033[36m'
    else
      green=$'\033[38;5;78m'
      label=$'\033[38;5;45m'
    fi
  fi
  echo
  printf '%s\n' "${green}Installation of pve-template-prep ${version} complete!${reset}"
  echo
  printf '%s\n' "${label}To run: (as root or sudo):${reset} ${bold}pve-template-prep${reset}"
  printf '%s\n' "${label}To do a dry run:${reset} ${bold}pve-template-prep --dry-run${reset}"
}

DEST="${DEST:-/opt/pve-template-prep}"
REPO="${REPO:-hyper-focused/PVE-Template-Prep}"
REF="${REF:-main}"
BIN_LINK="${BIN_LINK:-/usr/local/sbin/pve-template-prep}"
ARCHIVE_URL="https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz"

MODULES=(
  __init__.py
  build.py
  catalog.py
  cronscript.py
  customize.py
  download.py
  enroll.py
  job.py
  offer.py
  prompts.py
  runlog.py
  safedir.py
  termbg.py
  ui.py
  unattended.py
  update.py
  vm.py
)
# Config read at run time, next to the modules.
DATA_FILES=(
  palette.json
)
# License terms, next to the entry script. Apache-2.0 section 4 needs both in every copy.
ROOT_FILES=(
  LICENSE
  NOTICE
)

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this as root: sudo bash install.sh" >&2
  exit 1
fi

for cmd in curl tar python3 install stat; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "missing ${cmd}" >&2
    exit 1
  fi
done

if [[ "$DEST" != /* || "$BIN_LINK" != /* ]]; then
  echo "DEST and BIN_LINK must be absolute paths" >&2
  exit 1
fi

case "$DEST" in
  / | /usr | /usr/local | /usr/local/sbin | /etc | /var | /opt)
    echo "refusing to use DEST=$DEST" >&2
    exit 1
    ;;
esac

if [[ -L "$DEST" ]]; then
  echo "refusing to install through a symlink: $DEST" >&2
  exit 1
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

echo "Downloading pve-template-prep....."
curl -fsSL "$ARCHIVE_URL" -o "$work/src.tar.gz"
tar -xzf "$work/src.tar.gz" -C "$work"

src=""
for dir in "$work"/*/; do
  if [[ -n "$src" ]]; then
    echo "archive contained more than one directory" >&2
    exit 1
  fi
  src="${dir%/}"
done

if [[ -z "$src" || ! -f "$src/pve-template-prep.py" || ! -d "$src/pve_prep" ]]; then
  echo "archive is missing pve-template-prep.py or pve_prep/" >&2
  exit 1
fi

for name in "${MODULES[@]}" "${DATA_FILES[@]}"; do
  if [[ ! -f "$src/pve_prep/$name" ]]; then
    echo "archive is missing pve_prep/$name" >&2
    exit 1
  fi
done

# Same string -V prints. Parsed, not imported as the program.
version="$(
  PYTHONPATH="$src" python3 - "$src/pve_prep/__init__.py" <<'PY'
import sys
from pathlib import Path

from pve_prep.update import parse_version, version_key

found = parse_version(Path(sys.argv[1]).read_text(encoding="utf-8"))
if found is None or version_key(found) is None:
    raise SystemExit(1)
print(found)
PY
)" || {
  echo "pve_prep/__init__.py has no x.y.z __version__" >&2
  exit 1
}
for name in "${ROOT_FILES[@]}"; do
  if [[ ! -f "$src/$name" ]]; then
    echo "archive is missing $name" >&2
    exit 1
  fi
done

shopt -s nullglob
distro_files=( "$src"/pve_prep/distros/*.json )
shopt -u nullglob
if [[ ${#distro_files[@]} -eq 0 ]]; then
  echo "archive is missing pve_prep/distros/*.json" >&2
  exit 1
fi
for file in "${distro_files[@]}"; do
  if [[ -L "$file" ]]; then
    echo "distro config is a symlink: $file" >&2
    exit 1
  fi
done

for name in questionary prompt_toolkit wcwidth; do
  if [[ ! -f "$src/vendor/$name/__init__.py" ]]; then
    echo "archive is missing vendor/$name" >&2
    exit 1
  fi
done

if [[ ! -f "$src/vendor/VERSIONS" ]]; then
  echo "archive is missing vendor/VERSIONS" >&2
  exit 1
fi

meta=( "$src"/vendor/prompt_toolkit-*.dist-info/METADATA )
if [[ ! -f "${meta[0]}" ]]; then
  echo "archive is missing prompt_toolkit package metadata" >&2
  exit 1
fi

if find "$src/vendor" -type l -print -quit | grep -q .; then
  echo "vendor tree contains a symlink" >&2
  exit 1
fi

copy_tree() {
  local from="$1"
  local to="$2"
  local dir file rel
  install -d -o root -g root -m 0755 "$to"
  while IFS= read -r -d '' dir; do
    rel="${dir#"$from"/}"
    install -d -o root -g root -m 0755 "$to/$rel"
  done < <(find "$from" -mindepth 1 -type d -print0)
  while IFS= read -r -d '' file; do
    rel="${file#"$from"/}"
    install -o root -g root -m 0644 "$file" "$to/$rel"
  done < <(find "$from" -type f -print0)
}

echo "Installing pve-template-prep ${version} at ${DEST}....."
install -d -o root -g root -m 0755 "$DEST"
install -d -o root -g root -m 0755 "$DEST/pve_prep"
install -o root -g root -m 0755 "$src/pve-template-prep.py" "$DEST/pve-template-prep.py"
for name in "${ROOT_FILES[@]}"; do
  install -o root -g root -m 0644 "$src/$name" "$DEST/$name"
done
for name in "${MODULES[@]}" "${DATA_FILES[@]}"; do
  install -o root -g root -m 0644 "$src/pve_prep/$name" "$DEST/pve_prep/$name"
done
install -d -o root -g root -m 0755 "$DEST/pve_prep/distros"
for file in "${distro_files[@]}"; do
  install -o root -g root -m 0644 "$file" "$DEST/pve_prep/distros/${file##*/}"
done
copy_tree "$src/vendor" "$DEST/vendor"

# A module or distro file an older release shipped and this one does not.
# Both directories belong to the installer, and the catalog loads every
# distros/*.json it finds, so a leftover would still be read.
shipped_distros=()
for file in "${distro_files[@]}"; do
  shipped_distros+=("${file##*/}")
done
prune_stale() {
  local dir="$1" pattern="$2"
  shift 2
  local path name keep wanted
  shopt -s nullglob
  for path in "$dir"/$pattern; do
    name="${path##*/}"
    keep=0
    for wanted in "$@"; do
      if [[ "$name" == "$wanted" ]]; then
        keep=1
        break
      fi
    done
    if [[ "$keep" -eq 0 ]]; then
      echo "removing $path (not in this release)"
      rm -f -- "$path"
    fi
  done
  shopt -u nullglob
}
prune_stale "$DEST/pve_prep" "*.py" "${MODULES[@]}"
prune_stale "$DEST/pve_prep/distros" "*.json" "${shipped_distros[@]}"

confirm_path() {
  local path="$1"
  local owner bits
  if [[ -L "$path" ]]; then
    echo "installed path is a symlink: $path" >&2
    exit 1
  fi
  owner="$(stat -c '%u' "$path")"
  bits="$(stat -c '%A' "$path")"
  if [[ "$owner" != "0" ]]; then
    echo "not owned by root: $path" >&2
    exit 1
  fi
  if [[ "${bits:5:1}" == "w" || "${bits:8:1}" == "w" ]]; then
    echo "group or world writable: $path ($bits)" >&2
    exit 1
  fi
}

confirm_path "$DEST"
confirm_path "$DEST/pve_prep"
confirm_path "$DEST/pve-template-prep.py"
for name in "${ROOT_FILES[@]}"; do
  confirm_path "$DEST/$name"
done
for name in "${MODULES[@]}" "${DATA_FILES[@]}"; do
  confirm_path "$DEST/pve_prep/$name"
done
confirm_path "$DEST/pve_prep/distros"
for file in "$DEST"/pve_prep/distros/*.json; do
  confirm_path "$file"
done
while IFS= read -r -d '' path; do
  confirm_path "$path"
done < <(find "$DEST/vendor" -print0)

if [[ ! -x "$DEST/pve-template-prep.py" ]]; then
  echo "entry script is not executable" >&2
  exit 1
fi

python3 - "$DEST" "${MODULES[@]}" <<'PY'
import ast
import sys
from pathlib import Path

root = Path(sys.argv[1])
expected = set(sys.argv[2:])
installed = {path.name for path in (root / "pve_prep").glob("*.py")}
missing = sorted(expected - installed)
extra = sorted(installed - expected)
if missing:
    raise SystemExit("pve_prep is missing " + ", ".join(missing))
if extra:
    raise SystemExit("pve_prep has modules this release does not ship: " + ", ".join(extra))
paths = [root / "pve-template-prep.py", *sorted((root / "pve_prep").glob("*.py"))]
vendor = root / "vendor"
paths.extend(sorted(vendor.rglob("*.py")))
for path in paths:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
sys.path.insert(0, str(vendor))
sys.path.insert(0, str(root))
import pve_prep.catalog  # noqa: E402
import pve_prep.cronscript  # noqa: E402
import pve_prep.enroll  # noqa: E402
import pve_prep.offer  # noqa: E402
import pve_prep.runlog  # noqa: E402
import pve_prep.unattended  # noqa: E402
from pve_prep.ui import load_palette  # noqa: E402

load_palette(root / "pve_prep" / "palette.json")
import prompt_toolkit  # noqa: E402
import questionary  # noqa: E402
import wcwidth  # noqa: E402

if wcwidth.HAS_C_EXTENSION:
    raise SystemExit("wcwidth C extension loaded; ship the Python fallback")
if list((vendor / "wcwidth").glob("_wcwidth_c.*")):
    raise SystemExit("wcwidth C extension must not be shipped")
PY

# qemu-img ships with pve-qemu-kvm. Do not ask apt for qemu-utils on PVE.
# Checked per package, not by virt-customize alone: libguestfs-xfs is only a
# Recommends of libguestfs0, so a host with virt-customize can still lack it,
# and EL-family XFS guests then fail to mount.
GUESTFS_PACKAGES=(guestfs-tools libguestfs-xfs)

package_installed() {
  dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q '^install ok installed$'
}

missing_packages=()
for pkg in "${GUESTFS_PACKAGES[@]}"; do
  if ! command -v dpkg-query >/dev/null 2>&1 || ! package_installed "$pkg"; then
    missing_packages+=("$pkg")
  fi
done
if (( ${#missing_packages[@]} > 0 )); then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Install ${GUESTFS_PACKAGES[*]}, then re-run." >&2
    exit 1
  fi
  echo "Installing ${missing_packages[*]}"
  apt-get update
  # Skip Recommends: they pull in libguestfs-tools, guestfish, guestmount, virt-p2v.
  apt-get install -y --no-install-recommends "${missing_packages[@]}"
fi

install -d -o root -g root -m 0755 "$(dirname "$BIN_LINK")"
ln -sfn "$DEST/pve-template-prep.py" "$BIN_LINK"

report_installed "$version"
