# pve-template-prep plan

Prompt-driven Proxmox VE 9 builder. One distro per run, up to three releases. Official images only: Debian, Ubuntu, Alma, CloudLinux, Fedora. No CentOS, Rocky, openSUSE, Arch, or RHEL.

The interview rules below are the contract. questionary draws them on a terminal. It does not decide them.

## Interview

Each step says what it does before it asks. A bad answer asks that question again. `X` exits. The last answer is `YES` or `yes`. `no` exits too.

1. Distro, one per run. Guest prep follows that family (`deb` or `el`).
2. Releases, one to three. On the numbered path the numbers are the menu, not the version. Debian `2, 3` is 12 and 13. A codename still works there.
3. What to build. `1` complete template, `2` disk image only, `3` prep a stopped VM in place. Enter on the numbered path selects the template.
4. Disk format, except for the stopped VM. `1` ZFS raw (a file-based raw image is `.img` and full size), `2` QEMU qcow2. Enter selects raw.
5. VMIDs, one per release. Enter on a template uses the next free IDs from 9001 and never lands on an ID that is already in use. An occupied ID has to be typed. Image mode: Enter publishes a file. A typed in-use VMID gets the new disk inserted. Existing mode requires a VMID that is already in use.
6. Storage when the result is a VM disk. The id has to accept images. A published file asks for a directory, not a storage id.
7. Per VMID, and only when that VM has an OS disk (or the check cannot tell). `1` backs the disk up. `2` does not, and then the operator types `DELETE`. `X` exits. A template replace with no OS disk skips the backup menu and still requires `DELETE`. An image insert with no OS disk does not.
8. Guest prep. Enter applies it. `n` leaves the image as published.
9. Template hardware is one list for every distro: 2048 MB, 2 cores, virtio on `vmbr0`, virtio-scsi-single, scsi0 `discard=on,ssd=1`, ide2 cloud-init, SeaBIOS, serial console, guest agent, `/dev/urandom`. Enter keeps bridge, memory, and cores. `n` changes those three. The rest is changed later with `qm set`.
10. Summary, then `YES`.

`Job.backup_vmids` is the set that gets a copy. Template mode copies OS disks with `qemu-img convert` before `qm destroy`. Image mode keeps the old disk and attaches the new one on the next scsi slot. Anything else replaces the boot disk. Collision on the job is derived from that set. The entry script keys the runtime off `backup_vmids`.

No live template build from this tree unless someone asks for one.

## Widgets

questionary is the renderer. `pve_prep/prompts.py` owns the flow. `pve_prep/ui.py` is the only module that imports the libraries, and it imports them lazily.

On a TTY, with the vendor tree importable:

- Distro, build, format, storage, and the per-VMID backup choice are arrow menus. The explanation is printed once, then a blank line, then the menu. The numbered list is not printed again above the widget. A number highlights that row. Enter accepts it. Enter on Build accepts the template. Enter on Format accepts raw.
- The "do not back up" row is red (`class:danger`).
- Releases are a checkbox. Space marks a release. One to three. The result is still the release strings, in menu order. Codenames are not on this path.
- VMIDs, `DELETE`, `YES`, the image directory, bridge, memory, and cores stay typed. The template VMID field is prefilled with the free default, so Enter accepts it. `DELETE` and `YES` are not prefilled. `X` still exits.
- Guest prep and the hardware keep/change question are yes/no. Enter accepts the default. `n` changes it.

No TTY, a pipe, or a vendor tree that will not import: the numbered lines, including codenames. Unit tests drive that path. They do not need a virtualenv.

Ctrl-C or Ctrl-D during a widget cancels the interview. `main` prints `aborted` and returns 1.

## Vendor

Pinned tags, importable packages and licenses only. No wheels. No C extension. No pip. No `python3-venv`. No extra apt packages for the UI. `install.sh` copies `vendor/` out of the GitHub archive of this repo. It does not clone the three upstream repos on the node.

| Package | Tag | License |
| --- | --- | --- |
| questionary | 2.1.1 | MIT |
| prompt_toolkit | 3.0.52 | BSD |
| wcwidth | 0.9.2 | MIT |

prompt_toolkit 3.0.52 is the release questionary 2.1.1 was fixed for, and it still imports on Python 3.8 through 3.13. PVE 9 is 3.13. Its version string comes from `importlib.metadata`, so `vendor/prompt_toolkit-3.0.52.dist-info/METADATA` is part of the tree.

wcwidth 0.9.2 can build a C extension. This tree omits `_wcwidth_c` and uses the pure-Python fallback. Do not replace it with a macOS wheel.

`libguestfs-tools` and `qemu-utils` are still the only packages the installer adds, and only when `virt-customize` or `qemu-img` is missing.

## Install

`/opt/pve-template-prep`, link `/usr/local/sbin/pve-template-prep`. Root-owned, not a symlink, not group or world writable. That check covers `vendor/` too. `pve_prep/` is eight modules, including `ui.py`.

A Mac venv is not relocatable onto the node. Do not commit one, and do not path-edit one.

## Not in this pass

No commit, no push, and no copy to pve02 until asked. No live `qm` run. Clicking through the menus over SSH is the next check, on a TTY (`ssh -t`). A pipe will look like the numbered interview, which is what it should do.
