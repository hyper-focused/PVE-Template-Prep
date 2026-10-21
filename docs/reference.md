# pve-template-prep(8)

The manual. The [README](../README.md) is the short version. This page is every question, every path, every file the tool touches, and why it works the way it does. Nothing in it is optional if you are about to delete a VM or leave root login open.

## Name

pve-template-prep — build Proxmox VE 9 cloud-image templates, template boot disks, and standalone disk images from one interview.

## Synopsis

```
pve-template-prep [--dry-run] [-v | --verbose]
pve-template-prep -V | --version
pve-template-prep -h | --help
```

A live run is root on the PVE node. `--dry-run` is not.

## Description

One run builds one distro, up to three releases, down one of three paths:

| Path | Result |
| --- | --- |
| Create new Proxmox PVE VM templates | A new VM named `<distro>-<release>-cloud`, converted with `qm template` unless you say no |
| Update existing Proxmox PVE template VM disk images | A new boot disk on an existing template. The rest of its config stays |
| Create standalone Proxmox PVE VM disk images | `<distro>-<release>-pve.qcow2` or `.raw` in a storage's `import/` directory |

Every path downloads the vendor cloud image, checks its published checksum, converts it in the cache, and runs guest prep on that file. Then it publishes the file or imports it with `qm importdisk`. Every disk it writes is a `.qcow2` or `.raw` file on file storage. It never writes to a zvol, an LVM volume, RBD, or iSCSI.

There are no distro, release, or VMID flags. The interview is the interface. Nothing on the host changes until the last screen takes `YES` or `DELETE`.

## Options

| Option | Effect |
| --- | --- |
| `--dry-run` | Ask the same questions and print the commands. No download, no change to the host, no root. The storage menu still leaves out block storage it can read. The build-time storage check is skipped, because pvesm may be unreadable without root. The cache question is asked and nothing is deleted. |
| `-v`, `--verbose` | Show virt-customize's command log instead of the one-line progress estimate. |
| `-V`, `--version` | Print `pve-template-prep <x.y.z>` and exit 0. No update check. |
| `-h`, `--help` | Print usage and exit 0. |

An unknown option prints usage and exits 2.

## The interview

Each question says what that step does before it asks. Empty input accepts the default. A wrong answer asks again. On a terminal the choices are menus. A pipe gets numbered lines. Space marks a release. You type VMIDs, `DELETE`, and `YES`.

A backup, or a run that replaces nothing, starts when you type `YES` or `yes`. A delete with no backup starts when you type `DELETE`. `X` or `no` exits. Nothing on the host has been built until that word. The update question, when it appears, is earlier, and yes replaces this install before these questions.

Each step is a section: a heading, a grey rule, then the question. A menu is erased once it is answered, and one line names the choice: `Ubuntu selected.`

On a terminal the colors come from `pve_prep/palette.json`, which has a dark theme and a light theme. The theme follows the terminal background. Nothing readable means dark. `PVE_PREP_THEME=dark` or `light` skips the question. A pipe, `NO_COLOR`, or `TERM=dumb` stays plain. `TERM=linux` stays on the 16 ANSI colors. `COLORTERM=truecolor` or `24bit` uses 24-bit. Anything else on a terminal, including `xterm-256color` after SSH, uses 256 colors. `PROMPT_TOOLKIT_COLOR_DEPTH` can force one of those. Headings and questions are bold cyan, or deep blue on a light background. Choices are light grey. The current row is a bold blue `»` and row, not a filled bar. A ticked release turns teal, and so does the line that names a choice. Notes and rules are grey. Red is for what deletes: the `DELETE` line, and a summary cell that replaces a disk with no backup. Arrow keys move. A number highlights that row. Space or Enter selects it. The Enter default is the first row. Releases are the one list where Space marks and Enter accepts.

Releases are a checkbox: one to three. A codename such as `bookworm` works on the numbered path. A `vendor/` tree that will not import keeps that numbered path too.

1. What to build, under **PVE Template and Disk Image Builder**. The question is `Highlight desired template or image type and press enter`. Enter selects **Create new Proxmox PVE VM templates**. `2` is **Update existing Proxmox PVE template VM disk images**. `3` is **Create standalone Proxmox PVE VM disk images**. The menu does not describe the rows. The line after it is `New VM templates selected.`, `Update existing VM disk images selected.`, or `Standalone VM disk images selected.` There is no in-place prep. Guest prep runs on an image this tool downloads. It does not run on a disk the tool did not build.
2. **Distribution**. The question is `Please choose a distro. Highlight a selection and press enter`. The rows are the `name` in each distro file: Debian, Ubuntu, AlmaLinux, CloudLinux, Fedora.
3. Releases, by number, comma-separated. **The numbers are the menu, not the version.** On Debian, `1, 2` is 12 and 13. On Fedora, `1, 2` is 43 and 44. A codename still works (`bookworm`, `noble`). The section is **Releases** and the question is `Please choose release(s). Highlight selection(s) and press enter`. Space still marks a release. The line after it is `Ubuntu 22.04, 24.04, 26.04 selected.`
4. VMIDs, one question per release, on the create and update paths. Standalone skips this. On create, one note comes first: `Enter takes the first free VMID from 9001.` and `Typing the VMID of an existing template replaces that template.` The field is `Enter Debian 13 template VMID [9002]: ` and it is empty. The default is only in the brackets. Typing replaces it. One number. A list or a range is rejected. A value that cannot be used is asked again, and the next prompt says why.
   - Create: Enter assigns the next free ID from 9001. IDs already in use are skipped, quietly, including a hole such as a taken 9002. Two releases that both take the default become 9001 and 9002, or 9003 and 9004 when those first two are taken. A typed ID that is already in use has to be a template. The template is deleted and replaced. A normal VM is refused. A linked clone is refused. A template that still has linked clones is refused, including a clone on another node.
   - Update: the field is `Enter Ubuntu 24.04 template VMID: `, with no default. A new boot disk is created for that template. Every release needs a VMID. A free VMID is refused. A normal VM is refused, and the next prompt says so. A linked clone is refused the same way.
5. Where it goes. A standalone image lists storages whose `storage.cfg` content includes `import`, and writes `<distro>-<release>-pve.qcow2` or `.raw` into that storage's `import/` directory. Updating a template boot disk uses the storage that disk is already on, and the format that disk already has. Those questions are not asked. A boot disk on ZFS, LVM, LVM-thin, RBD, or iSCSI is refused at the VMID question: `VMID 910 boot disk: vm-data is ZFS. Disk images go on file storage only. Type another VMID.` A new template asks under **VM Disk Storage**: `Choose the storage for the new VM disk`. A pipe still says `VM Disk Storage Path`. The list is file storage only. The line above it is `Not listed: local-zfs (ZFS), local-lvm (LVM-thin). Disk images go on file storage only.` A host with storages and none of them file storage ends with `No file storage accepts the VM disk.` When that VMID already exists, its disk's storage is first in the list. Enter keeps it. A disk on block storage is not offered there. The note says to pick where the new disk goes. The field is not prefilled. A number picks from the list. A storage id that is not in the list is asked again.
6. Format. `1` QEMU qcow2 (`.qcow2`), `2` raw (`.raw`). Enter selects qcow2. The section is **Disk Format**. A new template asks `Choose the volume format on <storage>.` and the answer is `qm importdisk --format`. A standalone image asks `Choose the image format in <storage> import/.` and the answer is the file extension. CIFS is a file share, including when the volume is raw. When pvesm cannot be read, the line above is `Could not read the type of <storage>. The run refuses it unless it is file storage.` An update uses the format that disk already has and does not ask. A qcow2 file on a raw disk, or the reverse, breaks the VM config.
7. **Defaults**. Guest prep is not a question. It is applied to every image this tool builds. The defaults are listed above the field, which is `Accept these defaults? (y/n) [Yes]:`. Enter accepts the defaults this path still asks: root password SSH, and on the create path the bridge, memory, cores, and `qm template`. The cloud-init user must be `root`. `n` asks each one. Those are three sections: **Root SSH Access** asks `Enable SSH login for 'root' with a password? (y/n) [Yes]:` under `Cloud-init user must be set to 'root'.`, **Hardware** asks `Keep this hardware? (y/n) [Yes]:`, and **Template Conversion** asks `Convert the new VM with qm template? (y/n) [Yes]:`. More than one new VM says `VMs`. A standalone file, or an update, asks only the root question. Hardware and `qm template` are the create path. Enter keeps `vmbr0`, 2048 MB, and 2 cores. Machine type is `q35`. CPU type is `x86-64-v2-AES`. SCSI type, serial console, cloud-init, and the guest agent flag are set on the VM and can be changed later with `qm set`. `n` on `qm template` leaves a normal VM. A template is cloned, not booted.
8. **Backup existing images**. One question, after the other options. The field is `Create a backup copy of existing images? (y/n) [Yes]:`. Enter copies every disk that will be replaced into the cache, and the next line is `Image backups will be created at /var/tmp/pve-template-prep/cache`. `n` does not, and the line is `No backup images will be created.` A VMID with no OS disk is named, and the question is still asked. A new VMID and a published file skip this. A published file that already exists is renamed aside. That path does not ask.
9. **Summary**, then **Confirm Selections**. The summary is a table, one row per release. A new template shows Release, VMID, Name, Storage, Format, and Existing. An update shows Release, VMID, Storage, Format, and Existing disk. A standalone image shows Release, Format, and File. Existing says `new`, `replace`, `back up, replace`, `delete VM`, or `back up, delete VM`. A cell that deletes with no backup is red. Under the table: `Guest prep: yes   Root SSH: yes`, plus `qm template` and the hardware on the create path. Confirm Selections says what happens in one line, then one gate:
   - Backup: `Your existing disk images will be backed up, then new images created.` On the create path it says templates. Type `YES`. `X` exits. There is no second confirm.
   - No backup, template mode: **`Your existing templates will now be DELETED and new templates created.`** Type `DELETE`. `X` exits.
   - No backup, disk image: **`Your existing disk images will now be DELETED and new images created.`** The template config stays. Type `DELETE`. `X` exits.
   - Nothing to replace: `Your new VM templates will now be created.`, or VMs, or disk images. Type `YES`.
   The gate is `Type 'YES' to continue or 'X' to exit now:` or `Type 'DELETE' to continue or 'X' to exit now without changes:`. Nothing on the host has been built until that word. A dry run says so above the line.

After the builds, the cache question is `Delete working files in /var/tmp/pve-template-prep/cache? (y/n) [No]:`. Enter keeps the working files. `y` deletes them. This is the prep cache, not the rest of `/var/tmp`. A disk backup in that directory is never deleted. A failed release says the download is still there. Enter keeps it. A dry-run asks and does not delete.

## The three paths

**Create new Proxmox PVE VM templates.** Customize the downloaded image while it is still a file, then `qm importdisk` into the storage you named. That storage is file storage that accepts `images`: directory, NFS, CIFS, btrfs, CephFS, or GlusterFS. When the VMID already exists and its disk is on file storage, Enter keeps that storage. Import passes `--format qcow2` or `--format raw`. The working copy in the cache stays qcow2 until that import, so a raw volume does not expand there first. The guest name is `<distro>-<release>-cloud`. Enter on the conversion question runs `qm template`. `n` leaves a normal VM with the same disk, cloud-init drive, and boot order. This path builds a new VM. It does not keep the old firewall and options. A template that already has linked clones is not destroyed. Needs `qemu-img`, `qm`, and `pvesm`. Guest prep also needs `virt-customize`.

**Update existing Proxmox PVE template VM disk images.** An existing template VMID is required. Its boot disk is replaced on the storage that disk is already on, in the format that disk already uses. Storage and format are not asked. A free VMID is refused. A normal VM is refused. A boot disk on block storage is refused. Firewall, options, and the rest of the config stay. The guest is still a template when it is done. **Linked clones block that.** A clone on another node counts when the disk is on shared storage. If the clone check cannot be read, the template is left alone. Needs `qemu-img`, `qm`, and `pvesm`.

**Create standalone Proxmox PVE VM disk images.** Download, customize, and write `<distro>-<release>-pve.qcow2` or `.raw` into a storage `import/` directory. The menu lists only storages whose content includes `import`. Then the format. The extension is the format. Proxmox lists `.qcow2` and `.raw` in `import/`, not `.img`. The target storage and volume format are chosen when that file is imported, and that target can be ZFS, LVM, or RBD. This is the route to a template on a zvol. Needs `qemu-img`. Does not need `qm`.

**File storage only.** No path writes to a zvol, an LVM volume, RBD, or iSCSI. `build.run_fetched` checks the storage type again before the download, so a job that reaches the build with block storage stops there. A type that cannot be read stops there too. A dry-run skips that check, because it may not be able to read pvesm.

One release failing does not cancel the rest, unless a template was destroyed and its replacement did not finish. In that case the run stops. The exit status is 0 only when every selected release succeeds.

## Replacing a VMID

Enter never lands on a VMID that is already in use. Replacing one means you typed that ID.

Template mode replaces a whole template. A normal VM is not destroyed. The guest has to be stopped. A running guest is left alone. The check, and the disk copy when you asked for one, run before the new image is downloaded. `qemu-img convert` writes the copy into the cache. On a terminal that command prints its own progress. The copy counts only when that command exits 0 and the file is not empty. The line is `VM disk copy completed, building new template images now...`. A failed copy leaves the template alone and does not build the new image. Skipping the backup requires typing `DELETE`. `X` exits. If that destroy succeeds and the replacement does not finish, the run stops. **The download is kept in that case**, even when you asked to delete it. The disk backup is kept either way. Cleanup does not remove it.

**A linked clone is not changed, on either action.** A template that still has linked clones is not destroyed, and its disk is not replaced. A linked clone is a disk that still depends on the template base volume. A full clone does not. Clones on other cluster nodes count. Shared storage is where those show up. The check reads every guest config under `/etc/pve/nodes` and the storage volume list. If either one cannot be read, the guest is left alone.

Disk-image mode does not destroy the template. The boot disk is replaced on its current storage and the rest of the config stays. Backup copies the old disk into the cache first. It is not left attached.

The summary lists every VMID. Its Existing column says which ones are deleted or replaced, in red when there is no backup. Read it.

## Guest prep

Always, on an image this tool builds. There is no skip, and there is no in-place prep of a disk it did not build. The family comes from the catalog row you picked, not from the disk. Image and template builds apply it to the downloaded file before publish or import. The command is `virt-customize --format qcow2 -a <file>`, or `--format raw` for a raw standalone file, so libguestfs does not probe the format.

- Serial console
- `net.ifnames=0`, so the guest NIC is `eth0`
- qemu-guest-agent
- growpart
- chrony
- Reset machine-id and SSH host keys
- Clear shell history, logs, DHCP leases, `/tmp`, the systemd random seed, and persistent NIC rules
- Root password SSH, when that question is accepted

Debian and Ubuntu: mask AppArmor, purge snapd.

Alma, CloudLinux, and Fedora: SELinux permissive, firewalld disabled. Same path for the whole EL family.

`virt-customize` uses `LIBGUESTFS_BACKEND=direct` when that variable is unset, so libguestfs starts qemu itself instead of asking libvirt for permission.

## Root password SSH

Guest prep always runs. Enter on the root question writes `/etc/ssh/sshd_config.d/00-enableroot.conf`:

```
PasswordAuthentication yes
PermitRootLogin yes
```

`n` on that question leaves the image default. The file does not set a password. The wipe of logs and host keys runs first, and this file is written after it.

Deleting that file is the off switch. OpenSSH keeps the first value it reads, and `Include` is at the top of `sshd_config`. Ubuntu 22.04, 24.04, and 26.04 already set `PasswordAuthentication no` in `60-cloudimg-settings.conf`, so a later file cannot turn password login back on. `00-` is read first. It also wins if cloud-init later writes `PasswordAuthentication no` into `50-cloud-init.conf`. `ssh_pwauth: false` does not undo this file.

Debian and Ubuntu name the unit `ssh`. Alma, CloudLinux, and Fedora name it `sshd`.

```sh
rm /etc/ssh/sshd_config.d/00-enableroot.conf
systemctl restart ssh || systemctl restart sshd
```

The cloud-init user has to be `root`. Proxmox applies the password to root only when that user is root. A blank user becomes `ubuntu`, `debian`, `almalinux`, `fedora`, or `cloudlinux`, and root stays locked.

## Catalog

Each distro is one file, `pve_prep/distros/<distro>.json`. A release is a row in that file: version, codename, aliases, and whether it is EOL. The image URL is a pattern on the same file. Fedora's directory index and CloudLinux's `catalog.json` picker stay in `catalog.py`. Guest prep is one path per family (`deb` or `el`) in `customize.py`.

| Distro | Releases | Notes |
| --- | --- | --- |
| debian | 12, 13 | |
| ubuntu | 22.04, 24.04, 26.04 | |
| alma | 8, 9, 10 | |
| cloudlinux | 8, 9, 10 | Minimal OpenStack qcow2. Filename and SHA256 come from `catalog.json` at download. Logs in as `cloudlinux`. Packages do not update until the guest is registered. |
| fedora | 43, 44 | The Generic qcow2 is chosen from the release index at download. |

No CentOS. Fedora 42 is not listed. CloudLinux 6 and 7 are not listed.

Not in this version: Rocky, openSUSE, Arch, RHEL, and UEFI images. PVE 9. One distro per run. Up to three releases.

## Installer, updates, and the cache

The installer creates `/opt/pve-template-prep`, pulls `pve-template-prep.py`, `pve_prep/`, and `vendor/` from GitHub, checks that those files are root-owned and not writable by anyone else, and links the command to `/usr/local/sbin/pve-template-prep`. `vendor/` is questionary, prompt_toolkit, and wcwidth, pinned in `vendor/VERSIONS`. No pip. No `python3-venv`. It also installs `libguestfs-tools` and `qemu-utils` if they are missing. Python 3, `qm`, and `pvesm` are already on PVE 9.

Somewhere else: `DEST=/somewhere bash install.sh`, or `sudo DEST=/somewhere bash install.sh`. A clone is for the unit tests. One downloaded script, with no `pve_prep/` directory beside it, will not start.

`sudo` only has to set the effective uid to 0. `qm`, `pvesm`, `qemu-img`, and `virt-customize` are children of that process and stay root. They do not need their own sudoers rules. A `NOEXEC` tag on the sudoers command blocks them, and then nothing interesting happens.

`pve-template-prep -V` or `--version` prints the release and exits. It does not look for a newer copy. A run prints that same line, then an installed copy checks `main`. The check reads the version string and sends nothing else. A newer version asks `Fetch it now? (y/n) [No]:`. Enter keeps this copy. `y` runs `install.sh` into this install directory and starts that version. That includes a patch bump: a release added, a release removed, or a bugfix. A git checkout does not ask. If GitHub cannot be read, the questions start anyway. `PVE_PREP_SKIP_UPDATE=1` skips the check. Each release then prints an accent-colored line naming what it is building. On a terminal, `Converting <name> image...` is followed by the qemu-img progress, then `Preparing <name> image for Proxmox PVE...` and one progress line. virt-customize's own bracketed numbers are elapsed seconds, so that command log stays hidden. `-v` or `--verbose` shows it. A pipe shows it too. A plain line names what finished.

The cache is `/var/tmp/pve-template-prep/cache`, mode `0700`, owned by root. After the builds finish, the question is `Delete working files in /var/tmp/pve-template-prep/cache? (y/n) [No]:`. Enter keeps them. `y` deletes them. **A disk backup in that directory is never deleted.** A failed release asks the same question and says the download is still there. Enter keeps it. A dry-run asks and does not delete. `virt-customize --install` fetches packages from inside the image. A live run needs outbound network.

## Environment

| Variable | Effect |
| --- | --- |
| `PVE_PREP_SKIP_UPDATE=1` | Skip the check for a newer version on `main`. The tool sets it for the one restart after an update. |
| `PVE_PREP_THEME` | `dark` or `light` picks that palette and skips the background query. Unset or anything else asks the terminal. |
| `COLORFGBG` | Read when the terminal does not answer the background query. The last field is the background color index: 7 and 9 to 15 are light, the rest dark. |
| `NO_COLOR` | Any value turns the menu colors off. No background query either. |
| `TERM` | `dumb` is plain. `linux` is the 16 ANSI colors. Anything else on a terminal is 256 colors, unless `COLORTERM` says more. |
| `COLORTERM` | `truecolor` or `24bit` uses 24-bit color. |
| `PROMPT_TOOLKIT_COLOR_DEPTH` | `DEPTH_1_BIT`, `DEPTH_4_BIT`, `DEPTH_8_BIT`, or `DEPTH_24_BIT` forces that depth over `TERM` and `COLORTERM`. |
| `LIBGUESTFS_BACKEND` | Passed to virt-customize. Unset means `direct`, so libguestfs starts qemu itself instead of going through libvirt. |

`install.sh` reads four more:

| Variable | Effect |
| --- | --- |
| `DEST` | Install directory. Default `/opt/pve-template-prep`. Absolute. A symlink is refused. |
| `BIN_LINK` | Command link. Default `/usr/local/sbin/pve-template-prep`. |
| `REPO` | GitHub `owner/name`. Default `hyper-focused/PVE-Template-Prep`. |
| `REF` | Branch to download. Default `main`. |

## Files

| Path | What it is |
| --- | --- |
| `/opt/pve-template-prep/` | The install. `pve-template-prep.py`, `pve_prep/`, `pve_prep/distros/*.json`, and `vendor/`. Root-owned and not writable by anyone else. |
| `/usr/local/sbin/pve-template-prep` | Link to the installed script. |
| `/var/tmp/pve-template-prep/cache/` | Downloads, `.work` files, and disk backups. Mode `0700`, owned by root. Cleanup removes the working files. A disk backup, `vm-<vmid>-<bus>.bak.<UTC>.qcow2`, is never deleted. |
| `pve_prep/distros/<distro>.json` | One file per distro. A release is a row. `name` is the menu label. |
| `pve_prep/palette.json` | Colors. For `dark` and `light`, each role has an `xterm` 256-color index from 16 to 255, an `ansi` fallback (30 to 37, 90 to 97), and optional `bold`. Roles: header, body, option, highlight, success, note, warning, danger, accent. `install.sh` checks it. A broken file turns color off, with one line on stderr. |
| `/dev/tty` | Asked for its background color once, before the first question, when stdin and stdout are both terminals. |
| `/etc/pve/storage.cfg` | Read for `images` and `import` content and each storage's `path`. Never written. |
| `/etc/pve/nodes/*/qemu-server/*.conf` | Read to find linked clones on every node. Never written. |
| `<storage path>/import/` | Where a standalone image is published. An existing file of the same name is renamed to `<name>.bak.<UTC>` first. |
| `/etc/ssh/sshd_config.d/00-enableroot.conf` | Inside the guest image, when root password SSH is accepted. |

## Exit status

| Code | Meaning |
| --- | --- |
| `0` | Every selected release succeeded. Also `--version` and `--help`. |
| `1` | A release failed, the interview was aborted (`X`, EOF, or no usable storage), the update did not finish, the run is not root, or a required host tool is missing. A failed release does not stop the others, unless a template was destroyed and its replacement did not finish. |
| `2` | Bad command-line option. |

## Design notes

Why it works the way it does. Each note is a decision that a future change has to argue with.

### File storage only

Every disk this tool writes is a file. `vm.is_file_storage` is the one rule: `dir`, `nfs`, `cifs`, `btrfs`, `cephfs`, `glusterfs`. Anything else, including an empty type, is not file storage. The interview applies it three times: the new-template storage menu leaves block storage out, the update VMID check refuses a boot disk on it, and a recommended storage on it is dropped. The build applies it once more before the download, on a live run. Before 1.3.0 a block storage was imported as raw. That path is gone. A standalone file imported in Proxmox is the way onto a zvol.

### No .img

`.img` is only a name. Ubuntu's cloud `.img` is qcow2 inside. Proxmox reads the format from the extension: `images` accepts `raw`, `qcow2`, `vmdk`, and `subvol`, and `import/` accepts `ova`, `ovf`, `qcow2`, `raw`, and `vmdk`. A `.img` in `import/` is not listed and its volume id does not parse. `published_name` writes `.qcow2` or `.raw`, so the extension is always the format. The download is not affected: `qemu-img convert` probes the source, whatever its name.

### A sparse publish

The cache is `/var/tmp`. An import directory is often NFS or another mount, so `os.replace` fails with `EXDEV` and the file is copied. That copy seeks over every all-zero 1 MiB chunk and truncates to the full length at the end. A 20 GB raw file with 2 GB of data stays about 2 GB on ext4, XFS, and NFS. The copy is written to `.<name>.<pid>.partial`, synced, then renamed.

### The first question is the job

Enter creates a template (`template`). `2` replaces a template boot disk (`update`). `3` writes a standalone image (`standalone`). Distro and releases come after that, because the VMID, storage, and format questions depend on the job.

The menu rows have no notes under them. The question carries its own instruction, such as `Highlight desired template or image type and press enter`. What a row does is the README.

`Job.mode` stays `template` or `image`. Standalone is image mode with an empty VMID list, so the file is published. Update is image mode with a required template VMID, so the boot disk is replaced. The interview does not fill `template_vmids`. There is no fourth job and no `mode="existing"`. The old in-place path applied the family you picked to a disk this tool did not download. Templates this tool had already built already had guest prep.

### Guest prep is one pass on the downloaded file

It always runs. Packages install first. The last shared command clears shell history, logs, `/tmp`, DHCP leases, the systemd random seed, and persistent NIC rules, after `cloud-init clean`, the machine-id reset, and `rm` of `ssh_host_*`. The root-SSH drop-in is appended after that wipe, so the wipe cannot remove it. virt-sysprep is not used. A second generalize pass could undo the drop-in and the other files this pass just wrote. The family comes from the catalog row. The tool does not read `/etc/os-release` off a disk it did not build.

### Storage and format follow the disk that stays

An update reads both from `qm config` and does not ask. A wrong storage, or qcow2 imported onto a raw disk, breaks the VM config. `format=` wins. Otherwise the filename suffix decides. A volume name with no dot is a block volume. Its storage is block storage, so that VMID is refused before the format matters. Any other `format=` value, including `vmdk`, comes back empty and that VMID is asked again. Unreadable storage or no OS disk asks again too. The tool does not guess qcow2.

A full template replace still asks for storage. The old VM is destroyed and a new one is created, so the storage is a real choice. The current disk's storage is moved to the first row. Enter keeps it. The field is empty. The menu widget ignores a default argument, so the recommendation is the row order, not a prefilled value. Unreadable storage on that path is a note. The VMID is not asked again.

A new template asks qcow2 or raw. Block storage is not in its menu.

A standalone file asks qcow2 or raw after the import storage. The cache copy is converted to that format, prepped, then published. Proxmox asks for the target storage and volume format when that file is imported later. The menu lists storages whose `storage.cfg` content includes the token `import` and that have a `path`. The file is `{path}/import/<distro>-<release>-pve.qcow2` or `.raw`. One storage covers every release in the run. `job.storage` stays empty. `job.dest_dir` is that import directory. When `pvesm status` returns storages, an id that is absent from that list is left out. An empty status keeps every import storage from the file. Import content with no path is skipped. No import storage ends the interview. There is no typed directory.

### The field stays empty

A prefilled VMID had to be backspaced before it could be replaced. On the create path, one note comes before the first release, then an empty field per release:

```
  Enter takes the first free VMID from 9001.
  Typing the VMID of an existing template replaces that template.

  Enter Debian 13 template VMID [9002]:
```

The label is the catalog name plus the release, so a real run says `Debian 13`, not the codename. Enter accepts the bracket: the next free ID from 9001, skipping IDs already in use, including a hole. When no free ID can be suggested, the field has no bracket and `No free VMID from 9001 in the next 256 IDs.` comes first. When the host check fails, the field adds `Could not check which VMIDs are free. An existing VM will not be replaced.` above it.

The update field is `Enter Debian 13 template VMID: ` with no brackets. Enter does not publish a file. A free VMID is refused. A normal VM, a linked clone, and a template that still has linked clones are refused, and the next prompt says which.

Every yes or no that does not require a specific word uses the same empty field. The hint is `(y/n)`. The bracket is `Yes` or `No`. Enter accepts it. `y`, `yes`, `n`, and `no` answer it. Anything else prints `answer y or n` and asks the same line again. Context stays on the lines above. The suffix is only on the last line.

| Question | Bracket |
| --- | --- |
| `Accept these defaults?` | Yes |
| `Enable SSH login for 'root' with a password?` | Yes |
| `Keep this hardware?` | Yes |
| `Convert the new VM with qm template?` | Yes |
| `Create a backup copy of existing images?` | Yes |
| `Delete working files in /var/tmp/pve-template-prep/cache?` | No |
| `Fetch it now?` | No |

Menus stay menus. `YES` and `DELETE` stay typed words. These yes/no questions use a text field on a terminal and on a pipe. A confirm widget would take Enter without showing the bracket.

`Keep this hardware?` and `Convert the new VM with qm template?` appear only after `n` on the defaults, and only on the create path. Update and standalone ask only the root question. Cache cleanup is after the builds, including a failed release and a dry-run. The dry-run asks and does not delete. A failed release keeps the lines about the download above the same `[No]` question.

### The summary is a table

The summary used to be `debian 12 -> insert VMID 910 storage local qcow2 disk=overwrite guest-prep=yes root-ssh=yes`, one line per release, then one line per replaced VMID, then the list again on the delete screen. Now it is one table. The columns follow the path, so a standalone image has no VMID column to leave blank. Existing is the only column that can delete something, so it is the only one that turns red. The VMID list does not repeat on the last screen. Confirm Selections says what happens in a sentence and asks for the word.

### Colors follow the background

One dark palette was unreadable on a light terminal. The tool now asks the terminal: OSC 11 for the background color, then DA1 (`CSI c`). Every terminal answers DA1, so a DA1 reply with no OSC 11 reply before it means no support, and the probe returns without waiting. Only a terminal that answers nothing at all waits out the one-second cap, long enough for an SSH round trip across an ocean. The terminal is restored with `TCSAFLUSH`, so a late reply is discarded instead of landing in the first prompt. Then `COLORFGBG`, then dark. A light background is a Rec. 709 luma of 0.5 or more.

`termbg.py` does this with the standard library. `term-background` on PyPI was considered. It does not send OSC 11, its `COLORFGBG` check reports `0;15` as dark, and it is GPL-3.0-or-later, while `vendor/` is MIT and BSD.

The palette is data, not code. Each role has one 256-color index and one ANSI fallback per theme. A 24-bit terminal gets that index's exact xterm RGB, so it matches the 256-color look. A 16-color terminal gets ANSI names, not the nearest hex. prompt_toolkit's own style reverses a selected row. The menus turn that off.

### What a terminal shows while it builds

The download prints nothing. A cache hit prints `reuse` and the path. On a terminal, after the accent-colored `Creating <name> …` line, the next heading is `Converting <name> image...` and qemu-img `-p`. Then `Preparing <name> image for Proxmox PVE...` and one line, `    (percent/100%)  <phase>`. That percent is an estimate. virt-customize's `[  15.6]` numbers are elapsed seconds, not percent. `-v` or `--verbose` shows the command log. A pipe shows it too. Failure prints a newline, the captured log, then the error. `-V` or `--version` prints the release. The disk-backup `qemu-img -p` still has no extra heading. On a terminal that copy prints its own progress before the new image is downloaded.

## History

Release 1.3.1 restyles the interview: sections, a summary table, Confirm Selections, and a palette for dark and light backgrounds. What gets built did not change.

Release 1.2.0 is commit `5da0213`. Release 1.3.0 carries the commits after it:

- `0577592` drops in-place guest prep and keeps a replaced boot disk on its storage.
- `3558574` asks what to build first, keeps that disk's format, and shows convert and prep progress.
- `8e70ae1` puts the default in brackets on the create-template VMID and on every yes/no.
- `854b0b8` limits every disk to file storage, asks qcow2 or raw for a standalone image, names raw `.raw`, keeps a cross-filesystem publish sparse, and passes `--format` to virt-customize.

### An installed copy sees a release bump

`Fetch it now? (y/n) [No]:` compares the `x.y.z` in `pve_prep/__init__.py` on `main` with the copy that is installed. Equal versions do not ask. A 1.3.0 install asks about 1.3.1. Enter on that question keeps the installed copy. `y` runs `install.sh`. That script downloads the `main` tarball and does not read the version string. `-V` prints the version and does not check. A git checkout is left alone.

## See also

[README](../README.md), `install.sh`, `qm(1)`, `pvesm(1)`, `qemu-img(1)`, `virt-customize(1)`, and the Proxmox VE admin guide on storage and `import` content.
