# pve-template-prep

Proxmox will let you click through the same cloud-image ritual until the mouse files a complaint. This asks the questions once, then builds up to three releases of one distro: a disk image, a template VM, or a stopped VM that already exists.

## Install and run

A live run is **root on the PVE node**. Use the block that matches the prompt you already have. **Read `install.sh` before you run it.**

Already root:

```sh
curl -fsSLO https://raw.githubusercontent.com/hyper-focused/PVE-Template-Prep/main/install.sh
bash install.sh
pve-template-prep
```

With sudo:

```sh
curl -fsSLO https://raw.githubusercontent.com/hyper-focused/PVE-Template-Prep/main/install.sh
sudo bash install.sh
sudo pve-template-prep
```

The installer creates `/opt/pve-template-prep`, pulls `pve-template-prep.py`, `pve_prep/`, and `vendor/` from GitHub, checks that those files are root-owned and not writable by anyone else, and links the command to `/usr/local/sbin/pve-template-prep`. `vendor/` is questionary, prompt_toolkit, and wcwidth, pinned in `vendor/VERSIONS`. No pip. No `python3-venv`. It also installs `libguestfs-tools` and `qemu-utils` if they are missing. Python 3, `qm`, and `pvesm` are already on PVE 9.

Somewhere else: `DEST=/somewhere bash install.sh`, or `sudo DEST=/somewhere bash install.sh`. A clone is for the unit tests. One downloaded script, with no `pve_prep/` directory beside it, will not start.

`sudo` only has to set the effective uid to 0. `qm`, `pvesm`, `qemu-img`, and `virt-customize` are children of that process and stay root. They do not need their own sudoers rules. A `NOEXEC` tag on the sudoers command blocks them, and then nothing interesting happens.

`pve-template-prep --version` prints the release and exits. It does not look for a newer copy. A run prints that same line, then an installed copy checks `main`. The check reads the version string and sends nothing else. A newer version asks `Fetch it now? [y/N]`. Enter keeps this copy. `y` runs `install.sh` into this install directory and starts that version. That includes a patch bump: a release added, a release removed, or a bugfix. A git checkout does not ask. If GitHub cannot be read, the questions start anyway. `PVE_PREP_SKIP_UPDATE=1` skips the check. Each release then prints a cyan line naming what it is building, the green virt-customize output with a blank line on either side, and a white line naming what finished.

`--dry-run` asks the same questions and prints the commands. It does not need root, and it does not download or change the host. There are no distro or release flags. The questions are the interface. Run the dry-run before the real one.

Downloads land in `/var/tmp/pve-template-prep/cache`, mode `0700`, owned by root. At the end, Enter keeps the working files. `y` deletes them after every release succeeds. **A disk backup in that directory is never deleted.** A failed release also keeps the download, even if you asked to clean up. A dry-run deletes nothing. `virt-customize --install` fetches packages from inside the image. A live run needs outbound network.

## What it asks

Each question says what that step does before it asks. Empty input accepts the default when the question shows one. A wrong answer asks that question again. The last line is `Type YES`. `YES` or `yes` starts the run. `X` or `no` exits. Nothing on the host has been built until that YES. The update question, when it appears, is earlier, and yes replaces this install before these questions.

On a terminal the same questions are menus, colored for a dark background with the 16 ANSI colors. A header is green, the explanation under it is white, then a green line, then the choices in cyan. The current row is a cyan background with black text. The question label is bright black, and `(Y/n)` is white. Arrow keys move, a number highlights that row, and Enter accepts it. The Enter default is the first row. Moving the pointer does not leave that row painted as the selection. Red is only the "do not back up" row and a sentence that says a VM will be deleted. If the text is red, it is load-bearing.

Releases are a checkbox: space marks one, and one to three are allowed. VMIDs, `DELETE`, and `YES` are typed. A pipe, or a `vendor/` tree that will not import, keeps the numbered lines below. A codename such as `bookworm` works on that numbered path.

1. Distro, by number.
2. Releases, by number, comma-separated, one to three. **The numbers are the menu, not the version.** On Debian, `1, 2` is 12 and 13. On Fedora, `1, 2` is 43 and 44. A codename still works (`bookworm`, `noble`).
3. What to build. `1` complete PVE VM template, `2` PVE VM disk image only, `3` prep a stopped VM in place. Enter selects the template.
4. VMIDs, one question per release. The question names the release (`Provide a VMID for Debian 13`). One number. A list or a range is rejected.
   - Template: Enter assigns the next free ID from 9001. IDs already in use are skipped, quietly, including a hole such as a taken 9002. Two releases that both take the default become 9001 and 9002, or 9003 and 9004 when those first two are taken. A typed ID that is already in use replaces that VM. The question says the existing VM will be deleted.
   - Disk image only: Enter on the first release publishes a file for every selected release and does not touch a VM. Each later release needs its own VMID. An existing VMID receives the new disk. A VMID that is not in use asks you to try another ID, or to create a new VM template at that ID.
   - Stopped-VM prep: each release asks for a VMID that already exists. There is no free-ID default.
5. Where it goes. A published disk image asks for the file format, then a directory. `1` raw (`.img`, full size), `2` QEMU qcow2. Enter selects raw. The directory is a path, not a storage id. A template, an insert, or a stopped VM asks for `VM Disk Storage Path`. A number picks from the detected list. A storage id that is not in that list is asked again.
6. Volume format, after that storage, when the disk will be imported. Directory, NFS, CIFS, and the other file storages ask. `1` QEMU qcow2, `2` raw. Enter selects qcow2. That answer is `qm importdisk --format`. ZFS, LVM, LVM-thin, RBD, and iSCSI do not ask. The line says the volume will be raw. CIFS is a file share, including when the volume is raw. There is no block CIFS type. A stopped VM does not ask.
7. Disks that are already there, asked once per occupied VMID while the VMIDs are collected.
   - Template: **the VM will be deleted.** `1` copies the disk into the cache first. `2` does not. `2` then requires `DELETE`. `X` exits. A VM with no OS disk skips the backup menu and still requires `DELETE`.
   - Disk image: the VM stays. `1` keeps the old disk and attaches the new one. `2` deletes the old disk, after `DELETE`.
   - That VMID is a template: the config stays and the boot disk is replaced. `1` copies the old disk into the cache. It is not left attached. **A template with linked clones is not changed**, including clones on other nodes.
   - A published file that already exists is renamed aside. That path does not ask.
8. Guest prep. The prompt says what it changes. Enter means yes. `n` skips `virt-customize`.
9. Template mode: the hardware that will be applied, then bridge, memory, and cores. Enter keeps `vmbr0`, 2048 MB, and 2 cores. `n` asks for those three. Machine type is `q35`. CPU type is `x86-64-v2-AES`. SCSI type, serial console, cloud-init, and the rest are listed and can be changed later with `qm set`.
10. Template mode: convert the new VM with `qm template`. Enter converts. `n` leaves a normal VM. A template is cloned, not booted.
11. Delete the working files in `/var/tmp/pve-template-prep/cache` when the run finishes. Enter keeps them. `y` deletes them. A disk backup in that directory stays. This is the prep cache, not the rest of `/var/tmp`.
12. Read the summary. Type `YES`.

## What the three products do

**Image.** Download, optionally customize, and write `<distro>-<release>-pve.img` or `.qcow2` into the directory you named. Needs `qemu-img`. Does not need `qm` unless you typed an existing VMID. The directory is a filesystem path. A ZFS or LVM storage id will not do, however convincing the GUI is about it. An existing VMID keeps that VM and gets the new disk inserted.

A template VMID is how you refresh a template without rebuilding it. Firewall, options, and the rest of the config stay. The boot disk is replaced, and the guest is still a template when it is done. **Linked clones block that.** A clone on another node counts when the disk is on shared storage. If the clone check cannot be read, the template is left alone.

**Template.** Customize the downloaded image while it is still a file, then `qm importdisk` into the storage you named. Any storage that accepts `images` works. ZFS, LVM, LVM-thin, RBD, and iSCSI allocate a raw volume. Directory, NFS, and CIFS pass `--format qcow2` or `--format raw`. The working copy in the cache stays qcow2 until that import, so a raw volume does not expand there first. A published raw file still does. The guest name is `<distro>-<release>-cloud`. Enter on the conversion question runs `qm template`. `n` leaves a normal VM with the same disk, cloud-init drive, and boot order. This path builds a new VM. It does not keep the old firewall and options. A template that already has linked clones is not destroyed. Needs `qemu-img`, `qm`, and `pvesm`. Guest prep also needs `virt-customize`.

**Existing.** No download. The VM must be stopped, and it must not already be a template. Prep runs on the OS disk in place, so `pvesm path` has to be a regular file. A zvol or an LVM volume is refused. libguestfs does not get to improvise on a block device. Needs `qm`, `pvesm`, and, if prep is on, `virt-customize`. Does not need `qemu-img`.

One release failing does not cancel the rest, unless a template was destroyed and its replacement did not finish. In that case the run stops. The exit status is 0 only when every selected release succeeds.

## Replacing a VMID

Enter never lands on a VMID that is already in use. Replacing one means you typed that ID.

Template mode replaces the whole VM, template or not, after the new image is downloaded and prepped. The VM has to be stopped. A running VM is left alone. Each in-use VMID is asked on its own. Backup copies that OS disk into the cache with `qemu-img convert` before `qm destroy`. Skipping the backup requires typing `DELETE`. `X` exits. If that destroy succeeds and the replacement does not finish, the run stops. **The download is kept in that case**, even when you asked to delete it. The disk backup is kept either way. Cleanup does not remove it.

**A template with linked clones is not destroyed, and its disk is not replaced.** A linked clone is a disk that still depends on the template base volume. A full clone does not. Clones on other cluster nodes count. Shared storage is where those show up. The check reads every guest config under `/etc/pve/nodes` and the storage volume list. If either one cannot be read, the template is left alone.

Disk-image mode does not destroy the VM. On a normal VM, backup attaches the new disk on the next free scsi slot and leaves the old disk attached. It does not copy the old disk anywhere else. Overwrite replaces the boot disk and deletes the previous volume. On a template, the boot disk is replaced and the rest of the config stays. Backup copies the old disk into the cache first. Existing mode never destroys a VM, never inserts a downloaded image, and does not prep a template.

The confirm screen lists every VMID that will be replaced, plus bridge, memory, and cores. Read it. `YES` is the whole safety interlock.

## Catalog

Each distro is one file, `pve_prep/distros/<distro>.json`. A release is a row in that file: version, codename, aliases, and whether it is EOL. The image URL is a pattern on the same file. Adding or dropping a release is data, which is the whole reason it is not hardcoded into a function named after a codename. Fedora's directory index and CloudLinux's `catalog.json` picker stay in `catalog.py`, because those are parsers, not version numbers. Guest prep is still one path per family (`deb` or `el`) in `customize.py`.

| Distro | Releases | Notes |
| --- | --- | --- |
| debian | 12, 13 | |
| ubuntu | 22.04, 24.04, 26.04 | |
| alma | 8, 9, 10 | |
| cloudlinux | 8, 9, 10 | Minimal OpenStack qcow2. Filename and SHA256 come from `catalog.json` at download. |
| fedora | 43, 44 | The Generic qcow2 is chosen from the release index at download. |

No CentOS. Fedora 42 is not listed. It stopped building. CloudLinux logs in as `cloudlinux`. Its packages do not update until the guest is registered with a license. Unlicensed, it is a very calm brick. CloudLinux 6 and 7 are not listed.

## Guest prep

Default yes. Image and template mode apply it to the downloaded file before publish or import. Existing mode applies it to the OS disk after the hardware settings.

- Serial console
- `net.ifnames=0`, so the guest gets a stable `eth0` and not whatever `enp0s18` the kernel felt like that day
- qemu-guest-agent
- growpart
- chrony
- Reset machine-id and SSH host keys

Debian and Ubuntu: mask AppArmor, purge snapd. A cloud template does not need a snap of anything.

Alma, CloudLinux, and Fedora: SELinux permissive, firewalld disabled. Same EL path, on purpose.

`virt-customize` uses `LIBGUESTFS_BACKEND=direct` when that variable is unset, so libguestfs starts qemu itself instead of asking libvirt for permission.

## Not in this version

Rocky, openSUSE, Arch, RHEL, and UEFI images. Absent, not quietly half-built.

PVE 9. One distro per run. Up to three releases. The last prompt still wants the word `YES`.
