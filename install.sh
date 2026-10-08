#!/bin/bash
# Install pve-template-prep into /opt and link /usr/local/sbin/pve-template-prep.
# Fetch this file, read it, then: sudo bash install.sh
# Overrides: DEST, REPO, REF, BIN_LINK.

set -euo pipefail

DEST="${DEST:-/opt/pve-template-prep}"
REPO="${REPO:-hyper-focused/PVE-Template-Prep}"
REF="${REF:-main}"
BIN_LINK="${BIN_LINK:-/usr/local/sbin/pve-template-prep}"
ARCHIVE_URL="https://github.com/${REPO}/archive/refs/heads/${REF}.tar.gz"

MODULES=(
  __init__.py
  catalog.py
  customize.py
  download.py
  job.py
  prompts.py
  ui.py
  vm.py
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

echo "Downloading ${ARCHIVE_URL}"
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

for name in "${MODULES[@]}"; do
  if [[ ! -f "$src/pve_prep/$name" ]]; then
    echo "archive is missing pve_prep/$name" >&2
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

install -d -o root -g root -m 0755 "$DEST"
install -d -o root -g root -m 0755 "$DEST/pve_prep"
install -o root -g root -m 0755 "$src/pve-template-prep.py" "$DEST/pve-template-prep.py"
for name in "${MODULES[@]}"; do
  install -o root -g root -m 0644 "$src/pve_prep/$name" "$DEST/pve_prep/$name"
done
copy_tree "$src/vendor" "$DEST/vendor"

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
for name in "${MODULES[@]}"; do
  confirm_path "$DEST/pve_prep/$name"
done
while IFS= read -r -d '' path; do
  confirm_path "$path"
done < <(find "$DEST/vendor" -print0)

if [[ ! -x "$DEST/pve-template-prep.py" ]]; then
  echo "entry script is not executable" >&2
  exit 1
fi

python3 - "$DEST" <<'PY'
import ast
import sys
from pathlib import Path

root = Path(sys.argv[1])
paths = [root / "pve-template-prep.py", *sorted((root / "pve_prep").glob("*.py"))]
if len(list((root / "pve_prep").glob("*.py"))) != 8:
    raise SystemExit("pve_prep is missing modules")
vendor = root / "vendor"
paths.extend(sorted(vendor.rglob("*.py")))
for path in paths:
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
sys.path.insert(0, str(vendor))
sys.path.insert(0, str(root))
import pve_prep.catalog  # noqa: E402
import prompt_toolkit  # noqa: E402
import questionary  # noqa: E402
import wcwidth  # noqa: E402

if wcwidth.HAS_C_EXTENSION:
    raise SystemExit("wcwidth C extension loaded; ship the Python fallback")
if list((vendor / "wcwidth").glob("_wcwidth_c.*")):
    raise SystemExit("wcwidth C extension must not be shipped")
PY

if ! command -v virt-customize >/dev/null 2>&1 || ! command -v qemu-img >/dev/null 2>&1; then
  if ! command -v apt-get >/dev/null 2>&1; then
    echo "Install libguestfs-tools and qemu-utils, then re-run." >&2
    exit 1
  fi
  echo "Installing libguestfs-tools and qemu-utils"
  apt-get update
  apt-get install -y libguestfs-tools qemu-utils
fi

install -d -o root -g root -m 0755 "$(dirname "$BIN_LINK")"
ln -sfn "$DEST/pve-template-prep.py" "$BIN_LINK"

echo "Installed ${DEST}"
echo "Run: sudo pve-template-prep"
echo "Dry run, no root: pve-template-prep --dry-run"
