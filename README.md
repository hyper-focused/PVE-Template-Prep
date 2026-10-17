# pve-template-prep

One interview. Up to three releases of one distro. A disk image, a template VM, or a stopped VM that already exists.

Proxmox will let you click the cloud-image ritual until the mouse files a complaint. This asks once.

| Build | What lands | Enter does this |
| --- | --- | --- |
| Template | A new VM named `<distro>-<release>-cloud` | Next free VMID from 9001, then `qm template` |
| Disk image | `<distro>-<release>-pve.img` or `.qcow2` in a directory | Publishes the files and leaves every VM alone |
| Stopped VM | Guest prep on the OS disk in place | You type a VMID that already exists |

> [!IMPORTANT]
> Nothing is built until you type `YES`. Red text is load-bearing: a VM will be deleted, or you are on the row that does not back the disk up.

> [!CAUTION]
> A template with linked clones is left alone, including a clone on another node. If that check cannot be read, the template is left alone.

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

Each question says what that step does before it asks. Empty input accepts the default. A wrong answer asks again. On a terminal the choices are menus. A pipe gets numbered lines. Space marks a release. You type VMIDs, `DELETE`, and `YES`.

| | Question | Enter |
| --- | --- | --- |
| 1 | Distro | |
| 2 | One to three releases. The number is the menu, not the version. Debian `1, 2` is 12 and 13. Fedora `1, 2` is 43 and 44. | |
| 3 | `1` template, `2` disk image, `3` stopped VM | Template |
| 4 | One VMID per release. A list or a range is rejected. | Next free ID from 9001, in template mode |
| 5 | A directory for a published image, or `VM Disk Storage Path` for a VM | |
| 6 | Volume format on directory, NFS, and CIFS. `1` qcow2, `2` raw. ZFS, LVM, LVM-thin, RBD, and iSCSI skip this. | qcow2 |
| 7 | What to do with a disk that is already there | |
| 8 | Guest prep | Yes |
| 9 | Root password SSH. Skipped when guest prep is off. | Yes. Removal is [above](#root-password-ssh). |
| 10 | Hardware, in template mode: bridge, memory, cores | `vmbr0`, 2048 MB, 2 cores |
| 11 | `qm template`, in template mode | Convert |
| 12 | Delete the working files in the prep cache | Keep them. `y` deletes them. Disk backups stay. |
| 13 | Read the summary and type `YES` | |

When guest prep is on, the summary also says `root-ssh=yes` or `root-ssh=no`.

<details>
<summary>The long version of those questions</summary>

`YES` or `yes` starts the run. `X` or `no` exits. Nothing on the host has been built until that YES. The update question, when it appears, is earlier, and yes replaces this install before these questions.

On a terminal the menus are colored for a dark background with the 16 ANSI colors. A header is green, the explanation under it is white, then a green line, then the choices in cyan. The current row is a cyan background with black text. The question label is bright black, and `(Y/n)` is white. Arrow keys move, a number highlights that row, and Enter accepts it. The Enter default is the first row. Moving the pointer does not leave that row painted as the selection. Red is only the "do not back up" row and a sentence that says a VM will be deleted.

Releases are a checkbox: one to three. A codename such as `bookworm` works on the numbered path. A `vendor/` tree that will not import keeps that numbered path too.

1. Distro, by number.
2. Releases, by number, comma-separated. **The numbers are the menu, not the version.** A codename still works (`bookworm`, `noble`).
3. What to build. Enter selects the template.
4. VMIDs, one question per release. The question names the release (`Provide a VMID for Debian 13`). One number.
   - Template: Enter assigns the next free ID from 9001. IDs already in use are skipped, quietly, including a hole such as a taken 9002. Two releases that both take the default become 9001 and 9002, or 9003 and 9004 when those first two are taken. A typed ID that is already in use replaces that VM. The question says the existing VM will be deleted.
   - Disk image only: Enter on the first release publishes a file for every selected release and does not touch a VM. Each later release needs its own VMID. An existing VMID receives the new disk. A VMID that is not in use asks you to try another ID, or to create a new VM template at that ID.
   - Stopped-VM prep: each release asks for a VMID that already exists. There is no free-ID default.
5. Where it goes. A published disk image asks for the file format, then a directory. `1` raw (`.img`, full size), `2` QEMU qcow2. Enter selects raw. The directory is a path, not a storage id. A template, an insert, or a stopped VM asks for `VM Disk Storage Path`. A number picks from the detected list. A storage id that is not in that list is asked again.
6. Volume format, after that storage, when the disk will be imported. That answer is `qm importdisk --format`. The skipped storage types say the volume will be raw. CIFS is a file share, including when the volume is raw. There is no block CIFS type. A stopped VM does not ask.
7. Disks that are already there, asked once per occupied VMID while the VMIDs are collected.
   - Template: **the VM will be deleted.** `1` copies the disk into the cache first. `2` does not. `2` then requires `DELETE`. `X` exits. A VM with no OS disk skips the backup menu and still requires `DELETE`.
   - Disk image: the VM stays. `1` keeps the old disk and attaches the new one. `2` deletes the old disk, after `DELETE`.
   - That VMID is a template: the config stays and the boot disk is replaced. `1` copies the old disk into the cache. It is not left attached. **A template with linked clones is not changed**, including clones on other nodes.
   - A published file that already exists is renamed aside. That path does not ask.
8. Guest prep. The prompt says what it changes. Enter means yes. `n` skips `virt-customize`.
9. Root password SSH, asked only when guest prep is on. Enter writes the drop-in. `n` leaves the image default. The cloud-init user must be `root`.
10. Template mode: the hardware that will be applied, then bridge, memory, and cores if you decline the default. Machine type is `q35`. CPU type is `x86-64-v2-AES`. SCSI type, serial console, cloud-init, and the rest are listed and can be changed later with `qm set`.
11. Template mode: convert the new VM with `qm template`. Enter converts. `n` leaves a normal VM. A template is cloned, not booted.
12. Delete the working files in `/var/tmp/pve-template-prep/cache` when the run finishes. This is the prep cache, not the rest of `/var/tmp`.
13. Read the summary. Type `YES`.

</details>

## The three paths

**Image.** A directory path, not a storage id. A ZFS or LVM id will not do, however convincing the GUI is about it. An existing VMID keeps that VM and gets the new disk inserted. Needs `qemu-img`.

**Template.** The image is customized as a file, then imported. Enter runs `qm template`. `n` leaves a normal VM with the same disk, cloud-init drive, and boot order. This path builds a new VM. It does not keep the old firewall and options. Needs `qemu-img`, `qm`, and `pvesm`.

**Existing.** No download. The VM is stopped, and it is not already a template. `pvesm path` has to be a regular file. A zvol or an LVM volume is refused. Needs `qm`, `pvesm`, and, if prep is on, `virt-customize`.

One release failing does not cancel the rest, unless a template was destroyed and its replacement did not finish. Then the run stops. Exit status is 0 only when every selected release succeeds.

<details>
<summary>Import format, backups, and linked clones</summary>

Any storage that accepts `images` works for a template. ZFS, LVM, LVM-thin, RBD, and iSCSI allocate a raw volume. Directory, NFS, and CIFS pass `--format qcow2` or `--format raw`. The working copy in the cache stays qcow2 until that import, so a raw volume does not expand there first. A published raw file still does. The guest name is `<distro>-<release>-cloud`. Guest prep also needs `virt-customize`.

A template VMID in image mode is how you refresh a template without rebuilding it. Firewall, options, and the rest of the config stay. The boot disk is replaced, and the guest is still a template when it is done. **Linked clones block that.** A clone on another node counts when the disk is on shared storage.

Enter never lands on a VMID that is already in use. Replacing one means you typed that ID.

Template mode replaces the whole VM, template or not, after the new image is downloaded and prepped. The VM has to be stopped. A running VM is left alone. Each in-use VMID is asked on its own. Backup copies that OS disk into the cache with `qemu-img convert` before `qm destroy`. Skipping the backup requires typing `DELETE`. `X` exits. If that destroy succeeds and the replacement does not finish, the run stops. **The download is kept in that case**, even when you asked to delete it. The disk backup is kept either way. Cleanup does not remove it.

**A template with linked clones is not destroyed, and its disk is not replaced.** A linked clone is a disk that still depends on the template base volume. A full clone does not. Clones on other cluster nodes count. Shared storage is where those show up. The check reads every guest config under `/etc/pve/nodes` and the storage volume list. If either one cannot be read, the template is left alone.

Disk-image mode does not destroy the VM. On a normal VM, backup attaches the new disk on the next free scsi slot and leaves the old disk attached. It does not copy the old disk anywhere else. Overwrite replaces the boot disk and deletes the previous volume. On a template, the boot disk is replaced and the rest of the config stays. Backup copies the old disk into the cache first. Existing mode never destroys a VM, never inserts a downloaded image, and does not prep a template.

The confirm screen lists every VMID that will be replaced, plus bridge, memory, and cores. Read it. `YES` is the whole safety interlock.

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
