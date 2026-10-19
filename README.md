# pve-template-prep

One interview. Up to three releases of one distro. A new template, an updated template disk, or a standalone qcow2.

Proxmox will let you click the cloud-image ritual until the mouse files a complaint. This asks once.

| Build | What lands | Enter does this |
| --- | --- | --- |
| Create new Proxmox PVE VM templates | A new VM named `<distro>-<release>-cloud` | Next free VMID from 9001, then `qm template` |
| Update existing Proxmox PVE template VM disk images | The boot disk of a template that already exists | That template's VMID. Storage and format stay |
| Create standalone Proxmox PVE VM disk images | `<distro>-<release>-pve.qcow2` in that storage's `import/` directory | The first storage whose content includes `import` |

> [!IMPORTANT]
> Nothing is built until the last screen. A backup ends when you type `YES`. A delete with no backup ends when you type `DELETE`. Red text is that delete.

> [!CAUTION]
> Replacing a template, or replacing its boot disk, fails when the guest is a linked clone, or when a template still has linked clones. A clone on another node counts. If that check cannot be read, the guest is left alone. A normal VM cannot take the place of a template.

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

The next run checks for a newer version on its own. It reads the version string on `main` and asks `Fetch it now? (y/n) [No]:`. Enter keeps this copy. `y` installs that version and starts it. `-V` or `--version` only prints the release. `-v` or `--verbose` shows the virt-customize commands instead of the progress bar.

`--dry-run` asks the same questions and prints the commands. It does not need root, and it does not download or change the host.

Downloads land in `/var/tmp/pve-template-prep/cache`. **A disk backup in that directory is never deleted.**

<details>
<summary>Installer, updates, and the cache</summary>

The installer creates `/opt/pve-template-prep`, pulls `pve-template-prep.py`, `pve_prep/`, and `vendor/` from GitHub, checks that those files are root-owned and not writable by anyone else, and links the command to `/usr/local/sbin/pve-template-prep`. `vendor/` is questionary, prompt_toolkit, and wcwidth, pinned in `vendor/VERSIONS`. No pip. No `python3-venv`. It also installs `libguestfs-tools` and `qemu-utils` if they are missing. Python 3, `qm`, and `pvesm` are already on PVE 9.

Somewhere else: `DEST=/somewhere bash install.sh`, or `sudo DEST=/somewhere bash install.sh`. A clone is for the unit tests. One downloaded script, with no `pve_prep/` directory beside it, will not start.

`sudo` only has to set the effective uid to 0. `qm`, `pvesm`, `qemu-img`, and `virt-customize` are children of that process and stay root. They do not need their own sudoers rules. A `NOEXEC` tag on the sudoers command blocks them, and then nothing interesting happens.

`pve-template-prep -V` or `--version` prints the release and exits. It does not look for a newer copy. A run prints that same line, then an installed copy checks `main`. The check reads the version string and sends nothing else. A newer version asks `Fetch it now? (y/n) [No]:`. Enter keeps this copy. `y` runs `install.sh` into this install directory and starts that version. That includes a patch bump: a release added, a release removed, or a bugfix. A git checkout does not ask. If GitHub cannot be read, the questions start anyway. `PVE_PREP_SKIP_UPDATE=1` skips the check. Each release then prints a cyan line naming what it is building. On a terminal, `Converting <name> image...` is followed by the qemu-img progress, then `Preparing <name> image for Proxmox PVE...` and one progress line. virt-customize's own bracketed numbers are elapsed seconds, so that command log stays hidden. `-v` or `--verbose` shows it. A pipe shows it too. A white line names what finished.

There are no distro or release flags. The questions are the interface. Run the dry-run before the real one.

The cache is mode `0700`, owned by root. After the builds finish, the question is `Delete working files in /var/tmp/pve-template-prep/cache? (y/n) [No]:`. Enter keeps them. `y` deletes them. A disk backup in that directory is never deleted. A failed release asks the same question and says the download is still there. Enter keeps it. A dry-run asks and does not delete. `virt-customize --install` fetches packages from inside the image. A live run needs outbound network.

</details>

## Root password SSH

Guest prep always runs. Root can log in over SSH with a password. Enter on the defaults turns that on with the other defaults. `n` asks it by itself, and Enter on that question writes `/etc/ssh/sshd_config.d/00-enableroot.conf` on every family. `n` there leaves the image default.

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
| 1 | What to build. `1` create a template, `2` update a template disk, `3` standalone qcow2 | Create a template |
| 2 | Distro | |
| 3 | One to three releases. The number is the menu, not the version. Debian `1, 2` is 12 and 13. Fedora `1, 2` is 43 and 44. | |
| 4 | One VMID per release, for a new template or an update. Standalone skips this. A list or a range is rejected. | Next free ID from 9001, on the create-template path |
| 5 | Import storage for a standalone image. A new template asks `VM Disk Storage Path`. An update does not ask. | The first import storage. On a replace, the current disk's storage |
| 6 | Volume format when a new template is created on directory, NFS, or CIFS. A standalone image is qcow2. An update keeps that disk's format. | qcow2 |
| 7 | Accept the defaults that apply: root SSH, and on the create path the bridge, memory, cores, and `qm template`. Guest prep always runs. The field is `(y/n) [Yes]:`. | Yes. `n` asks each one. |
| 8 | Back up the disks that will be replaced. One answer for the whole list. Skipped when nothing is being replaced. The field is `(y/n) [Yes]:`. | Yes. `n` deletes them with no copy. |
| 9 | Read the summary. A backup ends on `YES`. No backup ends on `DELETE`. | |

The summary says `root-ssh=yes` or `root-ssh=no`. After the builds, one question deletes the working files in the prep cache: `Delete working files in /var/tmp/pve-template-prep/cache? (y/n) [No]:`. Enter keeps them. `y` deletes them. Disk backups stay. A failed release asks too, and Enter keeps the download.

<details>
<summary>The long version of those questions</summary>

A backup, or a run that replaces nothing, starts when you type `YES` or `yes`. A delete with no backup starts when you type `DELETE`. `X` or `no` exits. Nothing on the host has been built until that word. The update question, when it appears, is earlier, and yes replaces this install before these questions.

On a terminal the menus use one dark palette. A pipe, `NO_COLOR`, or `TERM=dumb` stays plain. `TERM=linux` stays on the 16 ANSI colors. `COLORTERM=truecolor` or `24bit` uses 24-bit. Anything else on a terminal, including `xterm-256color` after SSH, uses 256 colors. `PROMPT_TOOLKIT_COLOR_DEPTH` can force one of those. A header is green, the body is off-white, and the choices are cyan. The current row is a cyan background with near-black text. The question label is dim. Arrow keys move. A number highlights that row. Space or Enter selects it. The Enter default is the first row. Moving the pointer does not leave that row painted as the selection. Releases are the one list where Space marks and Enter accepts. Red is the screen that permanently deletes a template or its disk.

Releases are a checkbox: one to three. A codename such as `bookworm` works on the numbered path. A `vendor/` tree that will not import keeps that numbered path too.

1. What to build. Enter selects **Create new Proxmox PVE VM templates**. `2` is **Update existing Proxmox PVE template VM disk images**. `3` is **Create standalone Proxmox PVE VM disk images**. The menu does not describe the rows.
2. Distro. The question is `Please choose a distro`.
3. Releases, by number, comma-separated. **The numbers are the menu, not the version.** A codename still works (`bookworm`, `noble`). The question is `Please choose release(s).` On a terminal the line under the menu is `highlight selection(s) and press enter`. Space still marks a release.
4. VMIDs, one question per release, on the create and update paths. Standalone skips this. On create, the prompt is `Select new/existing VMID for new Debian 13 VM Template.` An existing template VMID is replaced completely. Enter uses the first free ID over 9000. The field is `Debian 13 VMID [9002]: ` and it is empty. The default is only in the brackets. Typing replaces it. One number. A value that cannot be used is asked again, and the next prompt says why.
   - Create: Enter assigns the next free ID from 9001. IDs already in use are skipped, quietly, including a hole such as a taken 9002. Two releases that both take the default become 9001 and 9002, or 9003 and 9004 when those first two are taken. A typed ID that is already in use has to be a template. The template is deleted and replaced. A normal VM is refused. A linked clone is refused. A template that still has linked clones is refused, including a clone on another node.
   - Update: the prompt asks for an existing template VMID. A new boot disk is created for that template. Every release needs a VMID. A free VMID is refused. A normal VM is refused, and the next prompt says so. A linked clone is refused the same way.
5. Where it goes. A standalone image lists storages whose `storage.cfg` content includes `import`, and writes `<distro>-<release>-pve.qcow2` into that storage's `import/` directory. The file is qcow2. Format is not asked. Proxmox chooses the volume format when that file is imported later. Updating a template boot disk uses the storage that disk is already on, and the format that disk already has. Those questions are not asked. A new template asks `VM Disk Storage Path`. When that VMID already exists, its disk's storage is first in the list. Enter keeps it. The field is not prefilled. A number picks from the list. A storage id that is not in the list is asked again.
6. Volume format, when a new template will be imported onto directory, NFS, or CIFS. That answer is `qm importdisk --format`. ZFS, LVM, LVM-thin, RBD, and iSCSI stay raw and do not ask. CIFS is a file share, including when the volume is raw. There is no block CIFS type. An update uses the format that disk already has, raw or qcow2, and does not ask. Importing the other format breaks the VM config. A standalone image does not ask. It is qcow2.
7. Accept these defaults. Guest prep is not a question. It is applied on every image this tool builds. Enter accepts the defaults this path still asks: root password SSH, and on the create path the bridge, memory, cores, and `qm template`. The cloud-init user must be `root`. `n` asks each one. A standalone file, or an update, asks only the root question. The root question does not repeat the revert command. That stays in [Root password SSH](#root-password-ssh). Hardware and `qm template` are the create path. Bridge, memory, and cores stay `vmbr0`, 2048 MB, and 2 cores unless you change them. Machine type is `q35`, the SCSI controller is virtio-scsi, the display is a serial console, and the guest agent flag is on. The rest can be changed later with `qm set`. `n` on `qm template` leaves a normal VM. A template is cloned, not booted.
8. Disks that will be replaced. One question, after the other options. Enter copies every one of them into the cache. `n` does not. A VMID with no OS disk is named, and the question is still asked. A new VMID and a published file skip this. A published file that already exists is renamed aside. That path does not ask.
9. Read the summary, then one gate.
   - Backup: the screen lists the VMIDs and asks for `YES`. `X` exits. There is no second confirm.
   - No backup, template mode: **the templates listed below will be permanently deleted.** Type `DELETE`. `X` exits.
   - No backup, disk image: **the existing disk for the templates listed below will be permanently deleted.** The template config stays. Type `DELETE`. `X` exits.
   - Nothing to replace: type `YES`.
   Nothing on the host has been built until that word.

After the builds, the cache question. Enter keeps the working files in `/var/tmp/pve-template-prep/cache`. `y` deletes them. This is the prep cache, not the rest of `/var/tmp`. A disk backup in that directory is never deleted. A failed release says the download is still there. Enter keeps it. A dry-run asks and does not delete.

</details>

## The three paths

**Create new Proxmox PVE VM templates.** The image is customized as a file, then imported. Enter runs `qm template`. `n` leaves a normal VM with the same disk, cloud-init drive, and boot order. This path builds a new VM. It does not keep the old firewall and options. You are asked for the storage. When the VMID already exists, Enter keeps the storage its disk is on. Needs `qemu-img`, `qm`, and `pvesm`.

**Update existing Proxmox PVE template VM disk images.** An existing template VMID is required. Its boot disk is replaced on the storage that disk is already on, in the format that disk already uses. Storage and format are not asked. A free VMID is refused. A normal VM is refused. Needs `qemu-img`, `qm`, and `pvesm`.

**Create standalone Proxmox PVE VM disk images.** A qcow2 file, `<distro>-<release>-pve.qcow2`, written into a storage `import/` directory. The menu lists only storages whose content includes `import`. Format is not asked. When that file is imported in Proxmox, the volume format is chosen there. Needs `qemu-img`. Does not need `qm`.

One release failing does not cancel the rest, unless a template was destroyed and its replacement did not finish. Then the run stops. Exit status is 0 only when every selected release succeeds.

<details>
<summary>Import format, backups, and linked clones</summary>

Any storage that accepts `images` works for a new template. ZFS, LVM, LVM-thin, RBD, and iSCSI allocate a raw volume. Directory, NFS, and CIFS pass `--format qcow2` or `--format raw`. The working copy in the cache stays qcow2 until that import, so a raw volume does not expand there first. A standalone file is already qcow2. The guest name is `<distro>-<release>-cloud`. Guest prep also needs `virt-customize`.

An update refreshes a template without rebuilding it. Firewall, options, and the rest of the config stay. The boot disk is replaced, and the guest is still a template when it is done. **Linked clones block that.** A clone on another node counts when the disk is on shared storage. A normal VM is refused.

Enter never lands on a VMID that is already in use. Replacing one means you typed that ID.

Template mode replaces a whole template. A normal VM is not destroyed. The guest has to be stopped. A running guest is left alone. The check, and the disk copy when you asked for one, run before the new image is downloaded. `qemu-img convert` writes the copy into the cache. On a terminal that command prints its own progress. The copy counts only when that command exits 0 and the file is not empty. The line is `VM disk copy completed, building new template images now...`. A failed copy leaves the template alone and does not build the new image. Skipping the backup requires typing `DELETE`. `X` exits. If that destroy succeeds and the replacement does not finish, the run stops. **The download is kept in that case**, even when you asked to delete it. The disk backup is kept either way. Cleanup does not remove it.

**A linked clone is not changed, on either action.** A template that still has linked clones is not destroyed, and its disk is not replaced. A linked clone is a disk that still depends on the template base volume. A full clone does not. Clones on other cluster nodes count. Shared storage is where those show up. The check reads every guest config under `/etc/pve/nodes` and the storage volume list. If either one cannot be read, the guest is left alone.

Disk-image mode does not destroy the template. The boot disk is replaced on its current storage and the rest of the config stays. Backup copies the old disk into the cache first. It is not left attached.

The last screen lists every VMID that will be replaced, after the summary has already named bridge, memory, and cores. Read it.

</details>

## Guest prep

Always, on an image this tool builds. There is no skip, and there is no in-place prep of a disk it did not build. Image and template builds apply it to the downloaded file before publish or import.

- Serial console
- `net.ifnames=0`, so the guest gets a stable `eth0` and not whatever `enp0s18` the kernel felt like that day
- qemu-guest-agent
- growpart
- chrony
- Reset machine-id and SSH host keys
- Clear shell history, logs, DHCP leases, `/tmp`, the systemd random seed, and persistent NIC rules
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

## Since 1.2.0

The questions above are the current interview. This is what changed on `main` after release 1.2.0. The version string is still `1.2.0`, so an installed copy does not offer these changes. `Fetch it now?` asks only when `main` carries a newer `x.y.z`. `install.sh` installs current `main` and does not read that string.

**What to build is the first question.** Enter creates a template. `2` replaces a template boot disk. `3` writes a standalone qcow2. The menu rows have no notes. On a terminal the line under a menu is `highlight selection(s) and press enter`. Guest prep runs on every image this tool downloads. A disk already on the host is left alone.

**A replaced boot disk stays where it is.** Storage and format are read from that disk. Those questions are not asked. A free VMID is refused. A new template still asks for storage, and an existing disk's storage is the first row. A standalone image is qcow2. The storage menu lists volumes whose content includes `import`, and the file lands in that storage's `import/` directory.

**The field stays empty. The default is in the brackets.** A create-template VMID looks like `Debian 13 VMID [9002]: `. Enter accepts the bracket. A yes or no that does not need a specific word looks like `Accept these defaults? (y/n) [Yes]: ` or `Delete working files in /var/tmp/pve-template-prep/cache? (y/n) [No]: `. `y` and `n` answer it. Menus still need a selection. `YES` and `DELETE` are still typed in full.

**A terminal shows two progress headings.** After the cyan start line, `Converting <name> image...` is qemu-img. `Preparing <name> image for Proxmox PVE...` is one estimated line for guest prep. virt-customize's own bracketed numbers are elapsed seconds. `-v` shows that log. `-V` prints the version.

**One yes covers the remaining defaults.** Enter on that question keeps root SSH, and on the create path the bridge, memory, cores, and `qm template`. `n` asks each of those. Cache cleanup is after the builds. Enter keeps the working files. Disk backups stay.

## Not in this version

Rocky, openSUSE, Arch, RHEL, and UEFI images. Absent, not quietly half-built.

PVE 9. One distro per run. Up to three releases. The last prompt still wants the word `YES`.
