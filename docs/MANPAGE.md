# PVE-TEMPLATE-PREP(8)

## NAME

pve-template-prep - build Proxmox VE 9 cloud-image templates, template boot disks, and standalone disk images

## SYNOPSIS

```
pve-template-prep [--dry-run] [-v | --verbose]
pve-template-prep -V | --version
pve-template-prep -h | --help
```

## DESCRIPTION

`pve-template-prep` downloads official cloud images, verifies them, prepares them for Proxmox VE, and delivers them in one of three forms. One run handles one distribution and one to three of its releases. There are no distribution, release, or VMID options: the program is driven by an interactive session ([INTERACTIVE SESSION](#interactive-session)). Nothing on the host changes until the final confirmation.

For each selected release the program:

1. Downloads the image and verifies its published checksum ([IMAGE SOURCES](#image-sources)).
2. Converts it to qcow2 in the cache with `qemu-img convert`.
3. Runs guest prep on the file with `virt-customize` ([GUEST PREP](#guest-prep)).
4. Delivers it according to the job: `qm importdisk` into a VM, or publication into a storage `import/` directory.

### Jobs

| Job | Result |
| --- | --- |
| Create new Proxmox PVE VM templates | A new VM named `<distro>-<release>-cloud`. Converted with `qm template` unless declined. |
| Update existing Proxmox PVE template VM disk images | A new boot disk on an existing template. The rest of the template's configuration is unchanged. |
| Create standalone Proxmox PVE VM disk images | `<distro>-<release>-pve.qcow2` or `.raw` in a storage's `import/` directory. |

Every disk written is a `.qcow2` or `.raw` file on file storage ([STORAGE](#storage)).

## OPTIONS

| Option | Description |
| --- | --- |
| `--dry-run` | Run the whole session and print the commands. Nothing is downloaded or changed, and root is not required. Block storage that can be read is still left out of menus. The build-time storage check is skipped, because `pvesm` may be unreadable without root. The cache question is asked and nothing is deleted. |
| `-v`, `--verbose` | Show each `virt-customize` command and its log instead of the progress line. |
| `-V`, `--version` | Print `pve-template-prep <x.y.z>` and exit 0. Does not check for updates. |
| `-h`, `--help` | Print usage and exit 0. |

An unrecognized option prints usage and exits 2.

## REQUIREMENTS

| Requirement | Needed by |
| --- | --- |
| Proxmox VE 9, x86_64 | all jobs |
| Root (not for `--dry-run`) | all live runs |
| `qemu-img` | create, update, standalone |
| `qm`, `pvesm` | create, update |
| `virt-customize` (`guestfs-tools`) | all jobs, because guest prep always runs |
| Outbound HTTPS to the image mirrors | all live runs |
| Outbound network from the guest appliance | all live runs: `virt-customize --install` fetches packages from inside the image |
| Outbound HTTPS to GitHub | update check, `install.sh` |

Python 3, `qm`, `pvesm`, and `qemu-img` are present on a stock PVE 9 node. `guestfs-tools` and `libguestfs-xfs` are not; `install.sh` installs whichever of them is missing, checked per package, with `--no-install-recommends`, which skips `libguestfs-tools`, `guestfish`, `guestmount`, and `virt-p2v`. `libguestfs-xfs` is named explicitly so XFS root filesystems (RHEL-family guests) can be mounted. A missing tool, a missing `libguestfs-xfs` package, or a `dpkg-query` that cannot answer, stops the run before the interview. The message is `missing <name>` or `could not check libguestfs-xfs with dpkg-query`, then `apt-get install --no-install-recommends guestfs-tools libguestfs-xfs`.

`sudo` only has to set the effective uid to 0. The helper commands run as children and stay root, so they need no sudoers rules of their own. A `NOEXEC` tag on the sudoers entry blocks them.

## INTERACTIVE SESSION

On a terminal, choices are menus. On a pipe they are numbered lines. Empty input takes the default. An invalid answer is asked again with a reason. Space marks a release and Enter accepts the list; in every other menu Space or Enter selects the highlighted row. A number highlights that row. Each completed menu is replaced by one line naming the choice.

Yes/no fields show `(y/n) [Yes]:` or `(y/n) [No]:`. Enter accepts the bracketed value. `y`, `yes`, `n`, and `no` answer it. `YES`, `DELETE`, and VMIDs are typed in full. `X` exits at the final gate; end of input exits at any prompt with status 1.

### 1. What to build

Section **PVE Template and Disk Image Builder**, question `Highlight desired template or image type and press enter`. `1` (default) creates templates, `2` updates template disks, `3` writes standalone images. This choice decides which of the later questions are asked.

### 2. Distribution

`Please choose a distro. Highlight a selection and press enter`. One of Debian, Ubuntu, AlmaLinux, CloudLinux, Fedora.

### 3. Releases

`Please choose release(s). Highlight selection(s) and press enter`. One to three. The numbers index the menu, not the version: for Debian, `1, 2` is 12 and 13. On the numbered path a release may be given by codename (`bookworm`, `noble`).

### 4. VMIDs (create and update)

Section **Template VMID(s)**, one field per release. Standalone images skip this step.

- **Create:** `Debian 13 VM Template ID [9002]:`. Enter takes the bracket, the next free ID from 9001 (see [VMIDS](#vmids-and-replacement)). A typed ID that is in use must be a template; it is replaced. Two releases that both take the default receive consecutive free IDs.
- **Update:** `Ubuntu 24.04 VM Template ID:`, no default. The ID must be an existing template.

A list or range is rejected. A refused ID is asked again and the next prompt states why.

### 5. Storage

- **Create:** section **VM Disk Storage**, `Choose the storage for the new VM disk`. Lists file storage that accepts `images`. A line above the list names storages left out, for example `Not listed: local-zfs (ZFS), local-lvm (LVM-thin). Disk images go on file storage only.` When the VMID already exists and its disk is on file storage, that storage is listed first. If no file storage accepts disk images the session ends with `No file storage accepts the VM disk.`
- **Update:** not asked. The storage of the current boot disk is used. A boot disk on block storage is refused at the VMID question.
- **Standalone:** lists storages whose `storage.cfg` content includes `import` and which have a `path`. One storage serves every release in the run. If none qualifies the session ends.

### 6. Format

Section **Disk Format**. `1` qcow2 (default), `2` raw. For a new template the answer is passed to `qm importdisk --format`. For a standalone image it is the file extension. An update does not ask: it keeps the format of the disk being replaced.

### 7. Defaults

A table of `Setting` and `Default` is shown, followed by `Accept these defaults? (y/n) [Yes]:`.

| Setting | Default | Applies to |
| --- | --- | --- |
| Password SSH | yes | all jobs |
| Root SSH | yes | all jobs |
| Bridge | `vmbr0` | create |
| Memory | 2048 MB | create |
| Cores | 2 | create |
| `qm template` | yes | create |

Answering `n` asks each setting in turn. SSH is described in [SSH ACCESS](#ssh-access). On the create path the VM is also configured with machine type `q35`, CPU type `x86-64-v2-AES`, a virtio-scsi controller, a serial console, a cloud-init drive, and the guest agent flag; these can be changed afterwards with `qm set`. Declining `qm template` leaves a normal VM with the same disk, cloud-init drive, and boot order.

### 8. Backup

Asked once, after the other options, and only when a disk or template would be replaced: `Create a backup copy of existing images? (y/n) [Yes]:`. Yes copies every disk about to be replaced into `/var/backups/pve-template-prep`. No deletes without a copy. A VMID with no OS disk is named in the question. A new VMID, or a published file with no predecessor, skips this step.

### 9. Summary and confirmation

Section **Summary**: a table with one row per release.

| Job | Columns |
| --- | --- |
| Create | Release, VMID, Name, Storage, Format, Existing |
| Update | Release, VMID, Storage, Format, Existing disk |
| Standalone | Release, Format, File |

The Existing column reads `new`, `replace`, `back up, replace`, `delete VM`, or `back up, delete VM`. A cell that deletes without a backup is red. Below the table: `Guest prep: yes   Password SSH: yes   Root SSH: yes`, plus `qm template` and hardware settings on the create path.

Section **Confirm Selections** states the consequence in one line and asks for one word:

| Situation | Word |
| --- | --- |
| Nothing is replaced | `YES` |
| Existing disks are backed up, then replaced | `YES` |
| Existing templates are deleted, no backup | `DELETE` |
| Existing boot disks are deleted, no backup | `DELETE` |

`X` exits with no change. Under `--dry-run` the screen says nothing has been built.

### After the builds

`Delete working files in /var/tmp/pve-template-prep/cache? (y/n) [No]:`. See [CACHE AND BACKUPS](#cache-and-backups). When the run wrote a disk backup, the last line is `VM Disk image backups located at /var/backups/pve-template-prep`.

## STORAGE

Disk images are written only to file storage: types `dir`, `nfs`, `cifs`, `btrfs`, `cephfs`, and `glusterfs`. ZFS, LVM, LVM-thin, RBD, iSCSI, and any type that cannot be read are not file storage. The rule is applied when menus are built, when an update VMID is checked, and again before the download of a live run.

| Content type | Used for |
| --- | --- |
| `images` | New template disks, imported with `qm importdisk` |
| `import` | Standalone images, written to `<path>/import/` |

The cache copy of an image remains qcow2 until import, so a raw volume does not expand in the cache. A standalone raw file is raw in the cache. A format mismatch between an image and the disk it replaces would break the VM configuration, so an update always uses the existing disk's storage and format, read from `qm config`; a disk whose format cannot be determined is refused.

Standalone files are named `<distro>-<release>-pve.qcow2` or `.raw`; the extension is the format. A file of the same name is renamed to `<name>.bak.<UTC>` first. When the cache and the import directory are on different filesystems the file is copied: all-zero 1 MiB chunks are skipped so the result stays sparse, the copy is written to `.<name>.<pid>.partial`, synced, and renamed into place.

Standalone images are the route to block storage. Proxmox converts the file when it is imported, and the target may be ZFS, LVM, LVM-thin, or RBD ([EXAMPLES](#examples)).

## VMIDS AND REPLACEMENT

**Free IDs.** The default for a create-path field is the lowest ID from 9001 that is not in use. An ID is in use when it is a VM or container on any cluster node, read once from `/etc/pve/.vmlist`; if that file cannot be read, each candidate is probed with `qm status` and `pct status`, and an ID is free only when neither has a config for it. Unused gaps are filled. When no free ID exists in the next 256, the field has no default. When the host cannot be checked, the field says so and an existing VM will not be replaced.

**What may be replaced.** Only templates. A normal VM, a container ID, a linked clone, and a template that still has linked clones are refused, and the next prompt names the reason. Enter never selects an ID that is in use; replacement means the ID was typed.

**Linked clones.** A linked clone is a disk that still depends on the template's base volume; a full clone does not. The check reads every guest configuration under `/etc/pve/nodes` and the storage volume list, so clones on other nodes count. If either cannot be read, the guest is left alone. The check guards both a template replacement and a boot disk replacement.

**Create path.** The old VM is destroyed and a new one created, so firewall rules and options are not preserved. The VM must be stopped; a running guest is left alone. The check and any requested backup happen before the new image is downloaded. The backup is `qemu-img convert` into the backup directory; it counts only if the command exits 0 and the file is not empty, and `VM disk copy completed, building new template images now...` is printed. A failed copy leaves the template untouched and the build does not start. If the old VM is destroyed and the replacement then fails, the run stops, the download is kept regardless of the cache answer, and the backup is kept.

**Update path.** The boot disk is replaced on its current storage. Backup copies the old disk first; the old disk is not left attached. The template configuration and the template status are unchanged. The new disk is the unused volume that `qm importdisk` added; an unused disk the template already had is left alone. During the swap the template flag is cleared and the guest carries the tag `pve-prep-swap-<vmid>`; its own tags are restored when it is a template again. If the swap fails, the imported volume is deleted and the flag restored. If the flag cannot be restored, the tag stays and the guest still counts as a template here: run the update again to finish it, or run `qm template <vmid>`.

A release that fails does not stop the others, except as stated above.

## GUEST PREP

Guest prep runs on every image the program builds. It cannot be skipped and is never run on a disk the program did not download. The image family (`deb` or `el`) comes from the catalog entry. The command is `virt-customize --format qcow2 -a <file>` (`--format raw` for a raw standalone file), with `LIBGUESTFS_BACKEND=direct` unless already set.

Packages installed: `qemu-guest-agent`, `cloud-init`, `openssh-server`, `ca-certificates`, `curl`, `sudo`, `chrony`, and the growpart package (`cloud-guest-utils` on Debian and Ubuntu, `cloud-utils-growpart` on the EL family).

| Applies to | Action |
| --- | --- |
| All | Serial console on `ttyS0` at 115200 (kernel arguments, plus a getty). `net.ifnames=0` and `biosdevname=0`, so the interface is `eth0`. |
| All | Timezone UTC. `chrony` set to `makestep 1.0 3`. |
| All | `cloud-init clean`; machine-id reset; `ssh_host_*` removed. |
| All | Shell history, logs, `/tmp`, DHCP leases, the systemd random seed, and persistent NIC rules cleared. |
| Debian, Ubuntu | AppArmor masked. `snapd` purged. |
| AlmaLinux, CloudLinux, Fedora | SELinux set to permissive. `firewalld` disabled. |
| If chosen | SSH drop-in ([SSH ACCESS](#ssh-access)). Written after the cleanup, so cleanup cannot remove it. |

`virt-sysprep` is not used.

## SSH ACCESS

Two questions control SSH in the guest. Each yes appends one line to `/etc/ssh/sshd_config.d/00-enableroot.conf` inside the image:

| Question | Line |
| --- | --- |
| `Allow password authentication for SSH?` | `PasswordAuthentication yes` |
| `Enable direct SSH login for 'root' user?` | `PermitRootLogin yes` |

Both no: no file is written and the image defaults stand. Password only: root remains at the image's `PermitRootLogin`, normally `prohibit-password`, so root needs a key. Root only: password login stays at the image default, which is `no` on Ubuntu images.

The file is named `00-` because OpenSSH keeps the first value it reads and `Include` sits at the top of `sshd_config`. Ubuntu sets `PasswordAuthentication no` in `60-cloudimg-settings.conf`, and cloud-init may write it into `50-cloud-init.conf`; a `00-` file takes precedence over both. `ssh_pwauth: false` does not undo it.

The file sets no password. Proxmox applies the cloud-init password to the cloud-init user only, so for root login the user must be `root`. A blank user becomes `ubuntu`, `debian`, `almalinux`, `fedora`, or `cloudlinux`, and root stays locked.

To revert on a guest:

```sh
rm /etc/ssh/sshd_config.d/00-enableroot.conf
systemctl restart ssh || systemctl restart sshd
```

The unit is `ssh` on Debian and Ubuntu, `sshd` on the others.

## IMAGE SOURCES

Images are the 64-bit (`x86_64` / `amd64`) cloud images published by each distribution. The download is compared with the published checksum; a mismatch or a missing checksum aborts that release.

| Distribution | Releases | Image | Checksum |
| --- | --- | --- | --- |
| Debian | 12 (bookworm), 13 (trixie) | `cloud.debian.org/images/cloud/<codename>/latest/debian-<rel>-genericcloud-amd64.qcow2` | `SHA512SUMS` |
| Ubuntu | 22.04 (jammy), 24.04 (noble), 26.04 (resolute) | `cloud-images.ubuntu.com/releases/<codename>/release/ubuntu-<rel>-server-cloudimg-amd64.img` | `SHA256SUMS` |
| AlmaLinux | 8, 9, 10 | `repo.almalinux.org/almalinux/<rel>/cloud/x86_64/images/AlmaLinux-<rel>-GenericCloud-latest.x86_64.qcow2` | `CHECKSUM` |
| CloudLinux | 8, 9, 10 | Minimal OpenStack qcow2, chosen from `images.cloudlinux.com/catalog.json` | SHA256 from the same catalog row |
| Fedora | 43, 44 | `Fedora-Cloud-Base-Generic-<rel>-*.x86_64.qcow2`, the newest compose in the `download.fedoraproject.org` release index (compared as numbers: `1.10` is newer than `1.9`) | The `CHECKSUM` file for that compose |

Notes: CloudLinux logs in as `cloudlinux` and its packages do not update until the guest is registered with a license. Not offered: CentOS, Rocky, openSUSE, Arch, RHEL, Fedora 42 and earlier, CloudLinux 6 and 7, and UEFI images.

Each distribution is one file, `pve_prep/distros/<distro>.json`: menu name, package family, checksum algorithm, source pattern, and a row per release (version, codename, aliases, EOL flag). Adding or dropping a release is an edit to that file.

## UPDATES

An installed copy compares its `x.y.z` (`pve_prep/__init__.py`) with the one on the `main` branch at GitHub. Only the version string is read; nothing is sent. If they differ the program asks `Fetch it now? (y/n) [No]:`. Yes downloads `install.sh` and runs it into the same install directory, then restarts with `PVE_PREP_SKIP_UPDATE=1`. No continues with the running copy. If GitHub cannot be read the session starts anyway. A git checkout is never updated. `-V` does not check.

Commits that keep the same version string are not offered. They are picked up by running a freshly downloaded `install.sh`, which upgrades in place and removes files the release no longer ships.

## CACHE AND BACKUPS

| Directory | Contents | Cleanup |
| --- | --- | --- |
| `/var/tmp/pve-template-prep/cache/` | Downloads and `.work` files. Mode `0700`, root. | Removed by answering yes to the cache question. A cache hit prints `reuse` and the path. |
| `/var/backups/pve-template-prep/` | Disk backups named `vm-<vmid>-<bus>.bak.<UTC>.qcow2`. Mode `0700`, root. Created on first use. | Never opened by cleanup. A backup left in the cache by an older release is skipped as well. |

Every directory on the way to either directory must belong to root and must not be writable by other users unless it is sticky, as `/var/tmp` is. Symlinks are followed and the target is checked the same way. Otherwise the run stops with `unsafe cache dir` or `unsafe backup dir` and the owner's uid. If another user created `/var/tmp/pve-template-prep`, remove it and run again.

A failed release asks the same cache question and reports that the download is still present; Enter keeps it, and a later run reuses it.

## PROGRESS OUTPUT

On a terminal each release prints an accent-colored line naming what is being built, then `Converting <name> image...` with `qemu-img -p` progress, then `Preparing <name> image for Proxmox PVE...` with one estimated progress line (`(percent/100%)  <phase>`). The bracketed numbers in `virt-customize` output are elapsed seconds, so its log is hidden unless `-v` is given. When output is not a terminal, plain lines are printed and the `virt-customize` log is shown. On failure the captured log is printed followed by the error. Terminal echo is disabled during the session so type-ahead is drawn once.

Colors come from `pve_prep/palette.json`. The theme is `PVE_PREP_THEME`, otherwise the terminal's reported background (OSC 11, with a DA1 reply as end marker and a one-second limit), otherwise `COLORFGBG`, otherwise dark. A background with Rec. 709 luma of 0.5 or more is light.

## ENVIRONMENT

| Variable | Effect |
| --- | --- |
| `PVE_PREP_SKIP_UPDATE` | `1` skips the update check. Set by the program for the restart after an update. |
| `PVE_PREP_THEME` | `dark` or `light` selects that palette and skips the background query. |
| `COLORFGBG` | Fallback background hint. The last field is a color index: 7 and 9-15 are light, others dark. |
| `NO_COLOR` | Any value disables color and the background query. |
| `TERM` | `dumb` is plain, `linux` is 16-color ANSI, anything else on a terminal is 256-color. |
| `COLORTERM` | `truecolor` or `24bit` selects 24-bit color. |
| `PROMPT_TOOLKIT_COLOR_DEPTH` | `DEPTH_1_BIT`, `DEPTH_4_BIT`, `DEPTH_8_BIT`, or `DEPTH_24_BIT` overrides `TERM` and `COLORTERM`. |
| `LIBGUESTFS_BACKEND` | Passed to `virt-customize`. Default `direct` when unset. |

`install.sh` also reads:

| Variable | Default | Effect |
| --- | --- | --- |
| `DEST` | `/opt/pve-template-prep` | Install directory. Absolute. A symlink is refused. |
| `BIN_LINK` | `/usr/local/sbin/pve-template-prep` | Command link. |
| `REPO` | `hyper-focused/PVE-Template-Prep` | GitHub `owner/name`. |
| `REF` | `main` | Branch to download. |

## FILES

| Path | Description |
| --- | --- |
| `/opt/pve-template-prep/` | Installation: `pve-template-prep.py`, `pve_prep/`, `vendor/`, `LICENSE`, `NOTICE`. Root-owned, not writable by others. |
| `/usr/local/sbin/pve-template-prep` | Link to the installed script. |
| `pve_prep/distros/<distro>.json` | Catalog, one file per distribution. |
| `pve_prep/palette.json` | Colors. For `dark` and `light`, each role (header, body, option, highlight, success, note, warning, danger, accent) has an `xterm` index (16-255), an `ansi` fallback (30-37, 90-97), and optional `bold`. A broken file turns color off with one line on stderr. |
| `vendor/` | prompt_toolkit, questionary, and wcwidth, versions pinned in `vendor/VERSIONS`. |
| `/var/tmp/pve-template-prep/cache/` | Download cache. |
| `/var/backups/pve-template-prep/` | Disk backups. |
| `<storage path>/import/` | Destination of standalone images. |
| `/etc/ssh/sshd_config.d/00-enableroot.conf` | Written inside the guest image when an SSH question is answered yes. |

Read, never written: `/etc/pve/storage.cfg`, `/etc/pve/.vmlist`, `/etc/pve/nodes/*/qemu-server/*.conf`, and `/dev/tty` (for the background color query).

## EXIT STATUS

| Code | Meaning |
| --- | --- |
| 0 | Every selected release succeeded. Also `--version` and `--help`. |
| 1 | A release failed; the session was aborted (`X`, end of input, or no usable storage); the update did not finish; the caller is not root; or a required tool is missing. |
| 2 | Invalid command-line option. |

## EXAMPLES

Preview a session without touching the host:

```sh
pve-template-prep --dry-run
```

Build templates for one distribution, accepting every default (one distribution per run, up to three releases):

```sh
pve-template-prep
```

Refresh a template's boot disk: choose job `2`, enter the template's VMID, and keep the backup default.

Make a template on ZFS. Run the program with job `3` and choose an import storage and format. The file lands in `<path>/import/`, for example `/var/lib/vz/import/debian-13-pve.qcow2`. Then import it with ZFS as the target. In the web UI use **Hardware, Add, Import Hard Disk**. On the CLI:

```sh
qm create 9001 --name debian-13-cloud --memory 2048 --cores 2 --machine q35 \
  --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-single --serial0 socket --vga serial0 --agent enabled=1
qm set 9001 --scsi0 local-zfs:0,import-from=local:import/debian-13-pve.qcow2,discard=on,ssd=1
qm set 9001 --ide2 local-zfs:cloudinit --boot order=scsi0
qm template 9001
```

The same procedure works for LVM, LVM-thin, and RBD.

Install or upgrade:

```sh
curl -fsSLO https://raw.githubusercontent.com/hyper-focused/PVE-Template-Prep/main/install.sh
bash install.sh
```

## SEE ALSO

`qm(1)`, `pvesm(1)`, `qemu-img(1)`, `virt-customize(1)`, the Proxmox VE Administration Guide (storage and `import` content), [README](../README.md), [DESIGN](DESIGN.md), [CHANGELOG](CHANGELOG.md).

## LICENSE

Apache License 2.0. Copyright 2026 Robert Tyree and hyper-focused. `LICENSE` holds the license text and `NOTICE` the attribution; redistribution carries `NOTICE` (section 4(d)). Running the program owes nothing. `vendor/` keeps its own licenses in `vendor/LICENSES`: prompt_toolkit is BSD-3-Clause; questionary and wcwidth are MIT.
