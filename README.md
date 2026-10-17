# pve-template-prep

One interview. Up to three releases of one distro. A disk image, a template VM, or a stopped VM that already exists.

Proxmox will let you click the cloud-image ritual until the mouse files a complaint. This asks once.

| Build | What lands | Enter does this |
| --- | --- | --- |
| Template | A new VM named `<distro>-<release>-cloud` | Next free VMID from 9001, then `qm template` |
| Disk image | `<distro>-<release>-pve.img` or `.qcow2` in a directory | Publishes the files and leaves every VM alone |
| Stopped VM | Guest prep on the OS disk in place | You type a VMID that already exists |

> [!IMPORTANT]
> Nothing is built until the last screen. A backup ends when you type `YES`. A delete with no backup ends when you type `DELETE`. Red text is that delete.

> [!CAUTION]
> Replacing a template, replacing its boot disk, or prepping a stopped VM fails when the guest is a linked clone, or when a template still has linked clones. A clone on another node counts. If that check cannot be read, the guest is left alone. A normal VM cannot take the place of a template.

## Install

A live run is **root on the PVE node**. Read `install.sh` before you run it.

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

The next run checks for a newer version on its own. It reads the version string on `main` and asks `Fetch it now? [y/N]`. Enter keeps this copy. `y` installs that version and starts it. `--version` only prints the release.

`--dry-run` asks the same questions and prints the commands. It does not need root, and it does not download or change the host.

Downloads land in `/var/tmp/pve-template-prep/cache`. **A disk backup in that directory is never deleted.**

<details>
<summary>Installer, updates, and the cache</summary>

The installer creates `/opt/pve-template-prep`, pulls `pve-template-prep.py`, `pve_prep/`, and `vendor/` from GitHub, checks that those files are root-owned and not writable by anyone else, and links the command to `/usr/local/sbin/pve-template-prep`. `vendor/` is questionary, prompt_toolkit, and wcwidth, pinned in `vendor/VERSIONS`. No pip. No `python3-venv`. It also installs `libguestfs-tools` and `qemu-utils` if they are missing. Python 3, `qm`, and `pvesm` are already on PVE 9.

Somewhere else: `DEST=/somewhere bash install.sh`, or `sudo DEST=/somewhere bash install.sh`. A clone is for the unit tests. One downloaded script, with no `pve_prep/` directory beside it, will not start.

`sudo` only has to set the effective uid to 0. `qm`, `pvesm`, `qemu-img`, and `virt-customize` are children of that process and stay root. They do not need their own sudoers rules. A `NOEXEC` tag on the sudoers command blocks them, and then nothing interesting happens.

`pve-template-prep --version` prints the release and exits. It does not look for a newer copy. A run prints that same line, then an installed copy checks `main`. The check reads the version string and sends nothing else. A newer version asks `Fetch it now? [y/N]`. Enter keeps this copy. `y` runs `install.sh` into this install directory and starts that version. That includes a patch bump: a release added, a release removed, or a bugfix. A git checkout does not ask. If GitHub cannot be read, the questions start anyway. `PVE_PREP_SKIP_UPDATE=1` skips the check. Each release then prints a cyan line naming what it is building, the green virt-customize output with a blank line on either side, and a white line naming what finished.

There are no distro or release flags. The questions are the interface. Run the dry-run before the real one.

The cache is mode `0700`, owned by root. At the end of a run, Enter keeps the working files. `y` deletes them after every release succeeds. A failed release also keeps the download, even if you asked to clean up. A dry-run deletes nothing. `virt-customize --install` fetches packages from inside the image. A live run needs outbound network.

</details>

## Root password SSH

When guest prep is on, the next question can let root log in over SSH with a password. Enter writes `/etc/ssh/sshd_config.d/00-enableroot.conf` on every family. `n` leaves the image default. Guest prep off skips the question.

```
PasswordAuthentication yes
PermitRootLogin yes
```

> [!WARNING]
> The cloud-init user has to be `root`. This tool does not set a password. A blank user becomes `ubuntu`, `debian`, `almalinux`, `fedora`, or `cloudlinux`, and root stays locked.

To put the image default back, on the guest:

```sh
rm /etc/ssh/sshd_config.d/00-enableroot.conf
systemctl restart ssh || systemctl restart sshd
```

Debian and Ubuntu name the unit `ssh`. Alma, CloudLinux, and Fedora name it `sshd`. The second restart runs when the first unit is not there. Tighten SSH after the first login.

<details>
<summary>Why the file is named 00-</summary>

Deleting that file is the off switch. OpenSSH keeps the first value it reads, and `Include` is at the top of `sshd_config`. Ubuntu 22.04, 24.04, and 26.04 already set `PasswordAuthentication no` in `60-cloudimg-settings.conf`, so a later file cannot turn password login back on. `00-` is read first. It also wins if cloud-init later writes `PasswordAuthentication no` into `50-cloud-init.conf`. `ssh_pwauth: false` does not undo this file.

</details>

## What it asks

Each question says what that step does before it asks. Empty input accepts the default. A wrong typed answer asks again, and that next prompt says why. On a terminal the choices are menus. A pipe gets numbered lines. Space or Enter selects a one-choice menu. Space marks a release, and Enter accepts that list. You type VMIDs, `DELETE`, and `YES`.

| | Question | Enter |
| --- | --- | --- |
| 1 | Distro | |
| 2 | One to three releases. The number is the menu, not the version. Debian `1, 2` is 12 and 13. Fedora `1, 2` is 43 and 44. | |
| 3 | `1` template, `2` disk image, `3` stopped VM | Template |
| 4 | One VMID per release. A list or a range is rejected. | Next free ID from 9001, in template mode |
| 5 | A directory for a published image, or `VM Disk Storage Path` for a VM | |
| 6 | Volume format on directory, NFS, and CIFS. `1` qcow2, `2` raw. ZFS, LVM, LVM-thin, RBD, and iSCSI skip this. | qcow2 |
| 7 | Guest prep | Yes |
| 8 | Root password SSH. Skipped when guest prep is off. | Yes. Removal is [above](#root-password-ssh). |
| 9 | Hardware, in template mode: bridge, memory, cores | `vmbr0`, 2048 MB, 2 cores |
| 10 | `qm template`, in template mode | Convert |
| 11 | Delete the working files in the prep cache | Keep them. `y` deletes them. Disk backups stay. |
| 12 | Back up the disks that will be replaced. One answer for the whole list. Skipped when nothing is being replaced. | Yes. `n` deletes them with no copy. |
| 13 | Read the summary. A backup ends on `YES`. No backup ends on `DELETE`. | |

When guest prep is on, the summary also says `root-ssh=yes` or `root-ssh=no`.

<details>
<summary>The long version of those questions</summary>

A backup, or a run that replaces nothing, starts when you type `YES` or `yes`. A delete with no backup starts when you type `DELETE`. `X` or `no` exits. Nothing on the host has been built until that word. The update question, when it appears, is earlier, and yes replaces this install before these questions.

On a terminal the menus use one dark palette. A pipe, `NO_COLOR`, or `TERM=dumb` stays plain. `TERM=linux` stays on the 16 ANSI colors. `COLORTERM=truecolor` or `24bit` uses 24-bit. Anything else on a terminal, including `xterm-256color` after SSH, uses 256 colors. `PROMPT_TOOLKIT_COLOR_DEPTH` can force one of those. A header is green, the body is off-white, and the choices are cyan. The current row is a cyan background with near-black text. The question label is dim. Arrow keys move. A number highlights that row. Space or Enter selects it. The Enter default is the first row. Moving the pointer does not leave that row painted as the selection. Releases are the one list where Space marks and Enter accepts. Red is the screen that permanently deletes a template or its disk.

Releases are a checkbox: one to three. A codename such as `bookworm` works on the numbered path. A `vendor/` tree that will not import keeps that numbered path too.

1. Distro, by number.
2. Releases, by number, comma-separated. **The numbers are the menu, not the version.** A codename still works (`bookworm`, `noble`).
3. What to build. Enter selects the template.
4. VMIDs, one question per release. The question names the release (`VMID for Debian 13. Enter uses 9002.`). One number. A value that cannot be used is asked again, and the next prompt says why.
   - Template: Enter assigns the next free ID from 9001. IDs already in use are skipped, quietly, including a hole such as a taken 9002. Two releases that both take the default become 9001 and 9002, or 9003 and 9004 when those first two are taken. A typed ID that is already in use has to be a template. The template is deleted and replaced. A normal VM is refused. A linked clone is refused. A template that still has linked clones is refused, including a clone on another node.
   - Disk image only: Enter on the first release publishes a file for every selected release and does not touch a VM. Each later release needs its own VMID. An existing VMID has to be a template. Its boot disk is replaced. The same refusals apply. A VMID that is not in use asks you to try another ID, or to create a new VM template at that ID.
   - Stopped-VM prep: each release asks for a VMID that already exists. There is no free-ID default. A template is refused. A linked clone is refused.
5. Where it goes. A published disk image asks for the file format, then a directory. `1` raw (`.img`, full size), `2` QEMU qcow2. Enter selects raw. The directory is a path, not a storage id. A template, an insert, or a stopped VM asks for `VM Disk Storage Path`. A number picks from the detected list. A storage id that is not in that list is asked again.
6. Volume format, after that storage, when the disk will be imported. That answer is `qm importdisk --format`. The skipped storage types say the volume will be raw. CIFS is a file share, including when the volume is raw. There is no block CIFS type. A stopped VM does not ask.
7. Guest prep. The prompt says what it changes. Enter means yes. `n` skips `virt-customize`.
8. Root password SSH, asked only when guest prep is on. Enter writes the drop-in. `n` leaves the image default. The cloud-init user must be `root`. The question does not repeat the revert command. That stays in [Root password SSH](#root-password-ssh).
9. Template mode: bridge, memory, and cores. Enter keeps `vmbr0`, 2048 MB, and 2 cores. `n` changes those three. Machine type is `q35`, the SCSI controller is virtio-scsi, the display is a serial console, and the guest agent is on. The rest can be changed later with `qm set`.
10. Template mode: convert the new VM with `qm template`. Enter converts. `n` leaves a normal VM. A template is cloned, not booted.
11. Delete the working files in `/var/tmp/pve-template-prep/cache` when the run finishes. This is the prep cache, not the rest of `/var/tmp`. A disk backup in that directory is never deleted.
12. Disks that will be replaced. One question, after the other options. Enter copies every one of them into the cache. `n` does not. A VMID with no OS disk is named, and the question is still asked. A new VMID, a published file, and stopped-VM prep skip this. A published file that already exists is renamed aside. That path does not ask.
13. Read the summary, then one gate.
   - Backup: the screen lists the VMIDs and asks for `YES`. `X` exits. There is no second confirm.
   - No backup, template mode: **the templates listed below will be permanently deleted.** Type `DELETE`. `X` exits.
   - No backup, disk image: **the existing disk for the templates listed below will be permanently deleted.** The template config stays. Type `DELETE`. `X` exits.
   - Nothing to replace: type `YES`.
   Nothing on the host has been built until that word.

</details>

## The three paths

**Image.** A directory path, not a storage id. A ZFS or LVM id will not do, however convincing the GUI is about it. An existing VMID has to be a template. Its boot disk is replaced and the rest of the config stays. A normal VM is refused. Needs `qemu-img`.

**Template.** The image is customized as a file, then imported. Enter runs `qm template`. `n` leaves a normal VM with the same disk, cloud-init drive, and boot order. This path builds a new VM. It does not keep the old firewall and options. Needs `qemu-img`, `qm`, and `pvesm`.

**Existing.** No download. The VM is stopped, and it is not already a template. A linked clone is refused. `pvesm path` has to be a regular file. A zvol or an LVM volume is refused. Needs `qm`, `pvesm`, and, if prep is on, `virt-customize`.

One release failing does not cancel the rest, unless a template was destroyed and its replacement did not finish. Then the run stops. Exit status is 0 only when every selected release succeeds.

<details>
<summary>Import format, backups, and linked clones</summary>

Any storage that accepts `images` works for a template. ZFS, LVM, LVM-thin, RBD, and iSCSI allocate a raw volume. Directory, NFS, and CIFS pass `--format qcow2` or `--format raw`. The working copy in the cache stays qcow2 until that import, so a raw volume does not expand there first. A published raw file still does. The guest name is `<distro>-<release>-cloud`. Guest prep also needs `virt-customize`.

A template VMID in image mode is how you refresh a template without rebuilding it. Firewall, options, and the rest of the config stay. The boot disk is replaced, and the guest is still a template when it is done. **Linked clones block that.** A clone on another node counts when the disk is on shared storage. A normal VM is refused.

Enter never lands on a VMID that is already in use. Replacing one means you typed that ID.

Template mode replaces a whole template. A normal VM is not destroyed. The guest has to be stopped. A running guest is left alone. The check, and the disk copy when you asked for one, run before the new image is downloaded. `qemu-img convert` writes the copy into the cache. On a terminal that command prints its own progress. The copy counts only when that command exits 0 and the file is not empty. The line is `VM disk copy completed, building new template images now...`. A failed copy leaves the template alone and does not build the new image. Skipping the backup requires typing `DELETE`. `X` exits. If that destroy succeeds and the replacement does not finish, the run stops. **The download is kept in that case**, even when you asked to delete it. The disk backup is kept either way. Cleanup does not remove it.

**A linked clone is not changed, on any of the three actions.** A template that still has linked clones is not destroyed, and its disk is not replaced. A linked clone is a disk that still depends on the template base volume. A full clone does not. Clones on other cluster nodes count. Shared storage is where those show up. The check reads every guest config under `/etc/pve/nodes` and the storage volume list. If either one cannot be read, the guest is left alone. Stopped-VM prep refuses a template and refuses a guest that is itself a linked clone.

Disk-image mode does not destroy the template. The boot disk is replaced and the rest of the config stays. Backup copies the old disk into the cache first. It is not left attached. Existing mode never destroys a VM, never inserts a downloaded image, and does not prep a template or a linked clone.

The last screen lists every VMID that will be replaced, after the summary has already named bridge, memory, and cores. Read it.

</details>

## Guest prep

Default yes. Image and template mode apply it to the downloaded file before publish or import. Existing mode applies it to the OS disk after the hardware settings.

- Serial console
- `net.ifnames=0`, so the guest gets a stable `eth0` and not whatever `enp0s18` the kernel felt like that day
- qemu-guest-agent
- growpart
- chrony
- Reset machine-id and SSH host keys
- Root password SSH, when that question is accepted. The file and how to remove it are under [Root password SSH](#root-password-ssh).

Debian and Ubuntu: mask AppArmor, purge snapd. A cloud template does not need a snap of anything.

Alma, CloudLinux, and Fedora: SELinux permissive, firewalld disabled. Same EL path, on purpose.

`virt-customize` uses `LIBGUESTFS_BACKEND=direct` when that variable is unset, so libguestfs starts qemu itself instead of asking libvirt for permission.

## Catalog

Each distro is one file, `pve_prep/distros/<distro>.json`. Adding or dropping a release is a row in that file. Fedora's directory index and CloudLinux's `catalog.json` picker stay in `catalog.py`. Guest prep is one path per family (`deb` or `el`) in `customize.py`.

| Distro | Releases | Notes |
| --- | --- | --- |
| debian | 12, 13 | |
| ubuntu | 22.04, 24.04, 26.04 | |
| alma | 8, 9, 10 | |
| cloudlinux | 8, 9, 10 | Minimal OpenStack qcow2. Filename and SHA256 come from `catalog.json` at download. |
| fedora | 43, 44 | The Generic qcow2 is chosen from the release index at download. |

No CentOS. Fedora 42 is not listed. It stopped building. CloudLinux logs in as `cloudlinux`. Its packages do not update until the guest is registered with a license. Unlicensed, it is a very calm brick. CloudLinux 6 and 7 are not listed.

## Not in this version

Rocky, openSUSE, Arch, RHEL, and UEFI images. Absent, not quietly half-built.

PVE 9. One distro per run. Up to three releases. The last prompt still wants the word `YES`.
