# pve-template-prep plan

Prompt-driven Proxmox VE 9 builder. One distro per run, up to three releases. Official images only: Debian, Ubuntu, Alma, CloudLinux, Fedora. No CentOS, Rocky, openSUSE, Arch, or RHEL.

The interview rules below are the contract. questionary draws them on a terminal. It does not decide them.

## Interview

Each step says what it does before it asks. A bad answer asks that question again. `X` exits. The last answer is `YES` or `yes`. `no` exits too.

1. Distro, one per run. Guest prep follows that family (`deb` or `el`).
2. Releases, one to three. On the numbered path the numbers are the menu, not the version. Debian `1, 2` is 12 and 13. Fedora `1, 2` is 43 and 44. A codename still works there. Debian and Fedora list two releases. The other distros list three.
3. What to build. `1` complete template, `2` disk image only, `3` prep a stopped VM in place. Enter on the numbered path selects the template.
4. VMIDs, one question per release, named. One number. No list, no range, no counting up from a typed ID. Template: Enter assigns the next free ID from 9001, skipping IDs that are in use, including holes. A typed in-use ID replaces that VM, and the question says so. Image: Enter on the first release publishes a file for every selected release. An in-use VMID gets the new disk. A template VMID keeps its config and gets a new boot disk, unless that template has linked clones on any cluster node. A free image VMID asks to try another ID or create a new VM template at that ID. Existing mode requires a VMID that is already in use.
5. Storage when the result is a VM disk. The id has to accept images. A published file asks for the file format first (`1` raw, Enter selects raw; `2` qcow2), then a directory. That path is not a storage id.
6. Volume format after the storage, when the disk will be imported. File storage (directory, NFS, CIFS, btrfs, CephFS, GlusterFS) asks. `1` qcow2, `2` raw. Enter selects qcow2. That value is `qm importdisk --format`. Block storage (ZFS, LVM, LVM-thin, RBD, iSCSI) does not ask and says the volume will be raw. CIFS is file storage. Raw on that share is still a file. There is no block CIFS type. Existing mode does not ask. The cache copy on an import stays qcow2. A published file uses the format from step 5 for the name and the convert.
7. Per VMID, and only when that VM has an OS disk (or the check cannot tell). `1` backs the disk up. `2` does not, and then the operator types `DELETE`. `X` exits. A template replace with no OS disk skips the backup menu and still requires `DELETE`. An image insert with no OS disk does not. A template with linked clones, including clones on other nodes, is not changed by either path.
8. Guest prep. Enter applies it. `n` leaves the image as published.
9. Template hardware is one list for every distro: 2048 MB, 2 cores, machine `q35`, CPU `x86-64-v2-AES`, virtio on `vmbr0`, virtio-scsi-single, scsi0 `discard=on,ssd=1`, ide2 cloud-init, SeaBIOS, serial console, guest agent, `/dev/urandom`. Enter keeps bridge, memory, and cores. `n` changes those three. The rest is changed later with `qm set`.
10. Template mode only: convert the new VM with `qm template`. Enter converts. `n` leaves a normal VM. Image and existing mode do not ask. A free image VMID that the operator turns into a new template is created with `qm template` and does not ask this again.
11. Delete the working files in `/var/tmp/pve-template-prep/cache` when the run finishes. Enter keeps them. `y` deletes them after every release succeeds. A disk backup in that directory is never deleted. A failed release keeps the download as well. A dry-run does not delete. This is not all of `/var/tmp`.
12. Summary, then `YES`. A VM that was not converted is described as a VM.

`Job.backup_vmids` is the set that gets a copy. Template mode copies OS disks with `qemu-img convert` before `qm destroy`. Image mode keeps the old disk and attaches the new one on the next scsi slot. Anything else replaces the boot disk. Collision on the job is derived from that set. The entry script keys the runtime off `backup_vmids`.

No live template build from this tree unless someone asks for one.

## Widgets

questionary is the renderer. `pve_prep/prompts.py` owns the flow. `pve_prep/ui.py` is the only module that imports the libraries, and it imports them lazily.

On a TTY, with the vendor tree importable:

- Distro, build, format, storage, and the per-VMID backup choice are arrow menus. The explanation is printed once, then a blank line, then the menu. The numbered list is not printed again above the widget. A number highlights that row. Enter accepts it. The pointer is the only highlight. The Enter default is the first row, and that row is not painted as selected after the pointer moves. Enter on Build still accepts the template. Enter on a published-file format accepts raw. Enter on a file-storage volume format accepts qcow2. Block storage does not open that menu.
- Colors assume a dark background and use the 16 ANSI colors only. Headers and the line above a menu are green. Explanations and `(Y/n)` are white. Menu rows are cyan. The current row is a cyan background with black text. The question label is bright black. Red is a destructive row, an EOL release, and a sentence that says a VM will be deleted.
- Releases are a checkbox. Space marks a release. One to three. The result is still the release strings, in menu order. Codenames are not on this path.
- VMIDs, `DELETE`, `YES`, the image directory, bridge, memory, and cores stay typed. Each template release prefills its own free VMID, so Enter accepts that one ID. `DELETE` and `YES` are not prefilled. `X` still exits.
- Guest prep, the hardware keep/change question, template conversion, and cache cleanup are yes/no. Enter accepts the default. Conversion defaults to yes. Cache cleanup defaults to no.

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

`/opt/pve-template-prep`, link `/usr/local/sbin/pve-template-prep`. Root-owned, not a symlink, not group or world writable. That check covers `vendor/` too. `pve_prep/` is nine modules, including `ui.py` and `build.py`. `build.py` is one function per action: publish a file, insert a disk, create a VM, prep a stopped VM. The runner calls the one `action_for` names. Fetch and guest prep stay shared. Release rows are `pve_prep/distros/*.json`, installed beside those modules. A new release is a row in the distro's file. A new distro with a pattern URL is a new file. Fedora's index parser and CloudLinux's catalog parser stay in `catalog.py`. Guest prep stays one path per family.

A Mac venv is not relocatable onto the node. Do not commit one, and do not path-edit one.

## Not in this pass

No commit, no push, and no copy to pve02 until asked. No live `qm` run. Clicking through the menus over SSH is the next check, on a TTY (`ssh -t`). A pipe will look like the numbered interview, which is what it should do.
