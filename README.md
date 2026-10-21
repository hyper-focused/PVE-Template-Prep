# pve-template-prep

One interview. Up to three releases of one distro. A new template, an updated template disk, or a standalone qcow2 or raw image.

Proxmox will let you click the cloud-image ritual until the mouse files a complaint. This asks once.

The full manual, with every question, file, exit status, and design decision, is [docs/reference.md](docs/reference.md).

| Build | What lands | Enter does this |
| --- | --- | --- |
| Create new Proxmox PVE VM templates | A new VM named `<distro>-<release>-cloud` | Next free VMID from 9001, then `qm template` |
| Update existing Proxmox PVE template VM disk images | The boot disk of a template that already exists | That template's VMID. Storage and format stay |
| Create standalone Proxmox PVE VM disk images | `<distro>-<release>-pve.qcow2` or `.raw` in that storage's `import/` directory | The first storage whose content includes `import`, then qcow2 |

> [!IMPORTANT]
> Nothing is built until the last screen. A backup ends when you type `YES`. A delete with no backup ends when you type `DELETE`. Red text is that delete.

> [!CAUTION]
> Replacing a template, or replacing its boot disk, fails when the guest is a linked clone, or when a template still has linked clones. A clone on another node counts. If that check cannot be read, the guest is left alone. A normal VM cannot take the place of a template.

> [!NOTE]
> **File storage only.** Every disk this tool writes is a `.qcow2` or `.raw` file on directory, NFS, CIFS, btrfs, CephFS, or GlusterFS storage. ZFS, LVM, LVM-thin, RBD, and iSCSI are not listed for a new template. A template whose boot disk is on one is refused. No zvol, no zpool, no thin volume. See [Templates on ZFS](#templates-on-zfs) for the way around it.

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

Downloads land in `/var/tmp/pve-template-prep/cache`. Disk backups go to `/var/backups/pve-template-prep`. **Cleanup never deletes a disk backup.**

<details>
<summary>Installer, updates, and the cache</summary>

The installer creates `/opt/pve-template-prep`, pulls `pve-template-prep.py`, `pve_prep/`, and `vendor/` from GitHub, checks that those files are root-owned and not writable by anyone else, and links the command to `/usr/local/sbin/pve-template-prep`. `vendor/` is questionary, prompt_toolkit, and wcwidth, pinned in `vendor/VERSIONS`. No pip. No `python3-venv`. It also installs `libguestfs-tools` and `qemu-utils` if they are missing. Python 3, `qm`, and `pvesm` are already on PVE 9.

Somewhere else: `DEST=/somewhere bash install.sh`, or `sudo DEST=/somewhere bash install.sh`. A clone is for the unit tests. One downloaded script, with no `pve_prep/` directory beside it, will not start.

`sudo` only has to set the effective uid to 0. `qm`, `pvesm`, `qemu-img`, and `virt-customize` are children of that process and stay root. They do not need their own sudoers rules. A `NOEXEC` tag on the sudoers command blocks them, and then nothing interesting happens.

`pve-template-prep -V` or `--version` prints the release and exits. It does not look for a newer copy. A run prints that same line, then an installed copy checks `main`. The check reads the version string and sends nothing else. A newer version asks `Fetch it now? (y/n) [No]:`. Enter keeps this copy. `y` runs `install.sh` into this install directory and starts that version. That includes a patch bump: a release added, a release removed, or a bugfix. A git checkout does not ask. If GitHub cannot be read, the questions start anyway. `PVE_PREP_SKIP_UPDATE=1` skips the check. Each release then prints an accent-colored line naming what it is building. On a terminal, `Converting <name> image...` is followed by the qemu-img progress, then `Preparing <name> image for Proxmox PVE...` and one progress line. virt-customize's own bracketed numbers are elapsed seconds, so that command log stays hidden. `-v` or `--verbose` shows it. A pipe shows it too. A plain line names what finished.

There are no distro or release flags. The questions are the interface. Run the dry-run before the real one.

The cache is mode `0700`, owned by root. Disk backups are not in it: they go to `/var/backups/pve-template-prep`, also `0700` and root, where a reboot or a cache cleanup cannot reach them. After the builds finish, the question is `Delete working files in /var/tmp/pve-template-prep/cache? (y/n) [No]:`. Enter keeps them. `y` deletes them. The backup directory is never touched. A failed release asks the same question and says the download is still there. Enter keeps it. A dry-run asks and does not delete. `virt-customize --install` fetches packages from inside the image. A live run needs outbound network.

</details>

## SSH access

Guest prep always runs. Two questions shape SSH, and Enter on the defaults says yes to both. `n` on the defaults asks each one. Each yes writes one line into `/etc/ssh/sshd_config.d/00-enableroot.conf` on every family:

| Question | Line |
| --- | --- |
| `Allow password authentication for SSH?` | `PasswordAuthentication yes` |
| `Enable direct SSH login for 'root' user?` | `PermitRootLogin yes` |

Two yeses give the line pair below. Two `n` answers write no file and leave the image default. Password alone leaves root at the image's own `PermitRootLogin`, which on stock OpenSSH refuses a root password. Root alone leaves password login at the image default, so root logs in with a key.

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
| 1 | What to build. `1` create a template, `2` update a template disk, `3` standalone image | Create a template |
| 2 | Distro | |
| 3 | One to three releases. The number is the menu, not the version. Debian `1, 2` is 12 and 13. Fedora `1, 2` is 43 and 44. | |
| 4 | One VMID per release, for a new template or an update. Standalone skips this. A list or a range is rejected. | Next free ID from 9001, on the create-template path |
| 5 | Import storage for a standalone image. A new template asks `VM Disk Storage Path`, and lists file storage only. An update does not ask. | The first import storage. On a replace, the current disk's storage |
| 6 | Format, qcow2 or raw, for a new template or a standalone image. An update keeps that disk's format. | qcow2 |
| 7 | Accept the defaults that apply: root SSH, and on the create path the bridge, memory, cores, and `qm template`. Guest prep always runs. The field is `(y/n) [Yes]:`. | Yes. `n` asks each one. |
| 8 | Back up the disks that will be replaced. One answer for the whole list. Skipped when nothing is being replaced. The field is `(y/n) [Yes]:`. | Yes. `n` deletes them with no copy. |
| 9 | Read the summary. A backup ends on `YES`. No backup ends on `DELETE`. | |

The summary is a table with one row per release, and says what happens to every VMID that already exists. Under it: `Guest prep: yes   Password SSH: yes   Root SSH: yes`. After the builds, one question deletes the working files in the prep cache: `Delete working files in /var/tmp/pve-template-prep/cache? (y/n) [No]:`. Enter keeps them. `y` deletes them. Disk backups are in `/var/backups/pve-template-prep` and stay. A failed release asks too, and Enter keeps the download.

<details>
<summary>The long version of those questions</summary>

A backup, or a run that replaces nothing, starts when you type `YES` or `yes`. A delete with no backup starts when you type `DELETE`. `X` or `no` exits. Nothing on the host has been built until that word. The update question, when it appears, is earlier, and yes replaces this install before these questions.

Each step is a section: a heading, a grey rule, then the question. A menu is erased once it is answered, and one line names the choice: `Ubuntu selected.`

On a terminal the colors come from `pve_prep/palette.json`, which has a dark theme and a light theme. The theme follows the terminal background. Nothing readable means dark. `PVE_PREP_THEME=dark` or `light` skips the question. A pipe, `NO_COLOR`, or `TERM=dumb` stays plain. `TERM=linux` stays on the 16 ANSI colors. `COLORTERM=truecolor` or `24bit` uses 24-bit. Anything else on a terminal, including `xterm-256color` after SSH, uses 256 colors. `PROMPT_TOOLKIT_COLOR_DEPTH` can force one of those. Headings and questions are bold cyan, or deep blue on a light background. Choices are light grey. The current row is a bold blue `»` and row, not a filled bar. A ticked release turns teal, and so does the line that names a choice. Notes and rules are grey. Red is for what deletes: the `DELETE` line, and a summary cell that replaces a disk with no backup. Arrow keys move. A number highlights that row. Space or Enter selects it. The Enter default is the first row. Releases are the one list where Space marks and Enter accepts.

Releases are a checkbox: one to three. A codename such as `bookworm` works on the numbered path. A `vendor/` tree that will not import keeps that numbered path too.

1. What to build, under **PVE Template and Disk Image Builder**. The question is `Highlight desired template or image type and press enter`. Enter selects **Create new Proxmox PVE VM templates**. `2` is **Update existing Proxmox PVE template VM disk images**. `3` is **Create standalone Proxmox PVE VM disk images**. The menu does not describe the rows. The line after it is `New VM templates selected.`, `Update existing VM disk images selected.`, or `Standalone VM disk images selected.`
2. **Distribution**. The question is `Please choose a distro. Highlight a selection and press enter`. The rows are the `name` in each distro file: Debian, Ubuntu, AlmaLinux, CloudLinux, Fedora.
3. Releases, by number, comma-separated. **The numbers are the menu, not the version.** A codename still works (`bookworm`, `noble`). The section is **Releases** and the question is `Please choose release(s). Highlight selection(s) and press enter`. Space still marks a release. The line after it is `Ubuntu 22.04, 24.04, 26.04 selected.`
4. VMIDs, one question per release, on the create and update paths. Standalone skips this. On create, one note comes first: `Enter takes the first free VMID from 9001.` and `Typing the VMID of an existing template replaces that template.` The field is `Enter Debian 13 template VMID [9002]: ` and it is empty. The default is only in the brackets. Typing replaces it. One number. A value that cannot be used is asked again, and the next prompt says why.
   - Create: Enter assigns the next free ID from 9001. IDs already in use are skipped, quietly, including a hole such as a taken 9002. Two releases that both take the default become 9001 and 9002, or 9003 and 9004 when those first two are taken. A typed ID that is already in use has to be a template. The template is deleted and replaced. A normal VM is refused. A linked clone is refused. A template that still has linked clones is refused, including a clone on another node.
   - Update: the field is `Enter Ubuntu 24.04 template VMID: `, with no default. A new boot disk is created for that template. Every release needs a VMID. A free VMID is refused. A normal VM is refused, and the next prompt says so. A linked clone is refused the same way.
5. Where it goes. A standalone image lists storages whose `storage.cfg` content includes `import`, and writes `<distro>-<release>-pve.qcow2` or `.raw` into that storage's `import/` directory. Updating a template boot disk uses the storage that disk is already on, and the format that disk already has. Those questions are not asked. A boot disk on ZFS, LVM, LVM-thin, RBD, or iSCSI is refused at the VMID question. A new template asks under **VM Disk Storage**: `Choose the storage for the new VM disk`. A pipe still says `VM Disk Storage Path`. The list is file storage only. A line above it names the storages left out. A host with no file storage ends the interview. When that VMID already exists, its disk's storage is first in the list. Enter keeps it. A disk on block storage is not offered, because the new disk goes on file storage. The field is not prefilled. A number picks from the list. A storage id that is not in the list is asked again.
6. Format. `1` QEMU qcow2 (`.qcow2`), `2` raw (`.raw`). Enter selects qcow2. The section is **Disk Format**. A new template asks `Choose the volume format on <storage>.` That answer is `qm importdisk --format`. A standalone image asks `Choose the image format in <storage> import/.` That answer is the file extension. An update uses the format that disk already has, raw or qcow2, and does not ask. Importing the other format breaks the VM config. There is no `.img`. Proxmox lists `.qcow2` and `.raw` in `import/`, and ignores `.img` there whatever is inside it.
7. Accept these defaults. Guest prep is not a question. It is applied on every image this tool builds. Enter accepts the defaults this path still asks: SSH password authentication, root SSH login, and on the create path the bridge, memory, cores, and `qm template`. The cloud-init user must be `root`. `n` asks each one. A standalone file, or an update, asks only the two SSH questions. **SSH Password Authentication** asks `Allow password authentication for SSH? (y/n) [Yes]:`. **Root SSH Access** says `Cloud-init user must be set to 'root'.`, then asks `Enable direct SSH login for 'root' user? (y/n) [Yes]:`. Neither repeats the revert command. That stays in [SSH access](#ssh-access). Hardware and `qm template` are the create path. Bridge, memory, and cores stay `vmbr0`, 2048 MB, and 2 cores unless you change them. Machine type is `q35`, the SCSI controller is virtio-scsi, the display is a serial console, and the guest agent flag is on. The rest can be changed later with `qm set`. `n` on `qm template` leaves a normal VM. A template is cloned, not booted.
8. **Backup existing images**. One question, after the other options: `Create a backup copy of existing images? (y/n) [Yes]:`. Enter copies every disk that will be replaced into the backup directory, and the next line is `Image backups will be created at /var/backups/pve-template-prep`. `n` does not, and the line is `No backup images will be created.` A VMID with no OS disk is named, and the question is still asked. A new VMID and a published file skip this. A published file that already exists is renamed aside. That path does not ask.
9. **Summary**, then **Confirm Selections**. The summary is a table, one row per release. A new template shows Release, VMID, Name, Storage, Format, and Existing. An update shows Release, VMID, Storage, Format, and Existing disk. A standalone image shows Release, Format, and File. Existing says `new`, `replace`, `back up, replace`, `delete VM`, or `back up, delete VM`. A cell that deletes with no backup is red. Under the table: `Guest prep: yes   Password SSH: yes   Root SSH: yes`, plus `qm template` and the hardware on the create path. Confirm Selections says what happens in one line, then one gate:
   - Backup: `Your existing disk images will be backed up, then new images created.` On the create path it says templates. Type `YES`. `X` exits. There is no second confirm.
   - No backup, template mode: **`Your existing templates will now be DELETED and new templates created.`** Type `DELETE`. `X` exits.
   - No backup, disk image: **`Your existing disk images will now be DELETED and new images created.`** The template config stays. Type `DELETE`. `X` exits.
   - Nothing to replace: `Your new VM templates will now be created.`, or VMs, or disk images. Type `YES`.
   The gate is `Type 'YES' to continue or 'X' to exit now:` or `Type 'DELETE' to continue or 'X' to exit now without changes:`. Nothing on the host has been built until that word. A dry run says so above the line.

After the builds, the cache question. Enter keeps the working files in `/var/tmp/pve-template-prep/cache`. `y` deletes them. This is the prep cache, not the rest of `/var/tmp`. Disk backups are in `/var/backups/pve-template-prep` and are never deleted. A failed release says the download is still there. Enter keeps it. A dry-run asks and does not delete.

</details>

## The three paths

**Create new Proxmox PVE VM templates.** The image is customized as a file, then imported as qcow2 or raw onto file storage. Enter runs `qm template`. `n` leaves a normal VM with the same disk, cloud-init drive, and boot order. This path builds a new VM. It does not keep the old firewall and options. You are asked for the storage. When the VMID already exists and its disk is on file storage, Enter keeps that storage. Needs `qemu-img`, `qm`, and `pvesm`.

**Update existing Proxmox PVE template VM disk images.** An existing template VMID is required. Its boot disk is replaced on the storage that disk is already on, in the format that disk already uses, qcow2 or raw. Storage and format are read from `qm config`, not asked. A free VMID is refused. A normal VM is refused. A boot disk on ZFS, LVM, or other block storage is refused. Needs `qemu-img`, `qm`, and `pvesm`.

**Create standalone Proxmox PVE VM disk images.** A qcow2 or raw file, `<distro>-<release>-pve.qcow2` or `.raw`, written into a storage `import/` directory. The menu lists only storages whose content includes `import`. Then the format. When that file is imported in Proxmox, the target storage and volume format are chosen there. That target can be ZFS. Needs `qemu-img`. Does not need `qm`.

One release failing does not cancel the rest, unless a template was destroyed and its replacement did not finish. Then the run stops. Exit status is 0 only when every selected release succeeds.

<details>
<summary>Import format, backups, and linked clones</summary>

A new template needs file storage that accepts `images`: directory, NFS, CIFS, btrfs, CephFS, or GlusterFS. Import passes `--format qcow2` or `--format raw`. The working copy in the cache stays qcow2 until that import, so a raw volume does not expand there first. A standalone raw file is raw in the cache. When the cache and the import directory are on different filesystems, the copy skips zero blocks, so the raw file stays sparse on NFS. A storage type that cannot be read is refused when the build starts. A dry-run notes it and goes on. The guest name is `<distro>-<release>-cloud`. Guest prep also needs `virt-customize`, and is told the format with `--format`.

An update refreshes a template without rebuilding it. Firewall, options, and the rest of the config stay. The boot disk is replaced, and the guest is still a template when it is done. **Linked clones block that.** A clone on another node counts when the disk is on shared storage. A normal VM is refused.

Enter never lands on a VMID that is already in use. Replacing one means you typed that ID.

Template mode replaces a whole template. A normal VM is not destroyed. The guest has to be stopped. A running guest is left alone. The check, and the disk copy when you asked for one, run before the new image is downloaded. `qemu-img convert` writes the copy into `/var/backups/pve-template-prep`. On a terminal that command prints its own progress. The copy counts only when that command exits 0 and the file is not empty. The line is `VM disk copy completed, building new template images now...`. A failed copy leaves the template alone and does not build the new image. Skipping the backup requires typing `DELETE`. `X` exits. If that destroy succeeds and the replacement does not finish, the run stops. **The download is kept in that case**, even when you asked to delete it. The disk backup is kept either way. Cleanup does not remove it.

**A linked clone is not changed, on either action.** A template that still has linked clones is not destroyed, and its disk is not replaced. A linked clone is a disk that still depends on the template base volume. A full clone does not. Clones on other cluster nodes count. Shared storage is where those show up. The check reads every guest config under `/etc/pve/nodes` and the storage volume list. If either one cannot be read, the guest is left alone.

Disk-image mode does not destroy the template. The boot disk is replaced on its current storage and the rest of the config stays. Backup copies the old disk into the cache first. It is not left attached.

The summary lists every VMID. Its Existing column says which ones are deleted or replaced, in red when there is no backup. Read it.

</details>

## Templates on ZFS

This tool does not write to a zvol. Build the image as a file, then let Proxmox import it onto the pool.

1. Run `3`, **Create standalone Proxmox PVE VM disk images**. Pick an import storage and qcow2 or raw. The file lands in `import/`, for example `/var/lib/vz/import/debian-13-pve.qcow2`.
2. Create the VM and import that file with ZFS as the target. In the GUI, **Hardware → Add → Import Hard Disk** lists it. On the CLI:

```sh
qm create 9001 --name debian-13-cloud --memory 2048 --cores 2 --machine q35 \
  --net0 virtio,bridge=vmbr0 --scsihw virtio-scsi-single --serial0 socket --vga serial0 --agent enabled=1
qm set 9001 --scsi0 local-zfs:0,import-from=local:import/debian-13-pve.qcow2,discard=on,ssd=1
qm set 9001 --ide2 local-zfs:cloudinit --boot order=scsi0
qm template 9001
```

Proxmox converts the file to a raw zvol on the way in. Guest prep is already in the file. The same route works for LVM, LVM-thin, and RBD.

## Guest prep

Always, on an image this tool builds. There is no skip, and there is no in-place prep of a disk it did not build. Image and template builds apply it to the downloaded file before publish or import.

- Serial console
- `net.ifnames=0`, so the guest gets a stable `eth0` and not whatever `enp0s18` the kernel felt like that day
- qemu-guest-agent
- growpart
- chrony
- Reset machine-id and SSH host keys
- Clear shell history, logs, DHCP leases, `/tmp`, the systemd random seed, and persistent NIC rules
- The SSH lines you said yes to. The file and how to remove it are under [SSH access](#ssh-access).

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

## What changed in 1.3.3

A 1.3.2 install offers 1.3.3 at its next run. The SSH question is two questions. **SSH Password Authentication** asks `Allow password authentication for SSH? (y/n) [Yes]:`, and **Root SSH Access** asks `Enable direct SSH login for 'root' user? (y/n) [Yes]:`. Each yes writes its own line into `00-enableroot.conf`. Two yeses write the same file 1.3.2 wrote. The summary says `Password SSH` and `Root SSH`. See [SSH access](#ssh-access).

## What changed in 1.3.2

A 1.3.1 install offers 1.3.2 at its next run. Two things moved.

**Disk backups have their own directory.** A copy of a disk that is about to be replaced goes to `/var/backups/pve-template-prep`, mode `0700`, owned by root. It used to share `/var/tmp/pve-template-prep/cache` with the downloads, and `/var/tmp` is not promised to survive a reboot. The cache question only ever touches the cache. A copy that an older release left in the cache is still skipped by that cleanup.

**Text fields show their parts.** The question is bold cyan, the `(y/n) [Yes]` or `[9002]` part is bold blue, and what you type is teal. On the delete gate the word `DELETE` is red in the question and red as you type it.

## What changed in 1.3.1

A 1.3.0 install offers 1.3.1 at its next run. Nothing about what gets built changed. The interview looks different.

**Every step is a section.** A heading, a grey rule, then the question. A menu is erased once answered and one line names the choice. The VMID field is `Enter Ubuntu 24.04 template VMID:`. The guest-prep line is gone. Guest prep still always runs, and the summary says so.

**The summary is a table.** One row per release, and an Existing column that says `new`, `replace`, or `delete VM`, with `back up,` in front when there is a backup. Red when there is not. The last screen is **Confirm Selections**: one line saying what happens, then `YES` or `DELETE`.

**Colors follow the background.** Dark and light palettes live in `pve_prep/palette.json`. The terminal is asked for its background color. Nothing readable means dark. `PVE_PREP_THEME=dark` or `light` skips the question. The current row is a bold blue `»`, not a filled bar.

**Distro names come from the config.** The menu says AlmaLinux, not `alma`.

## What changed in 1.3.0

A 1.2.0 install offers 1.3.0 at its next run.

**File storage only.** A new template, a replaced boot disk, and a standalone image are files on directory, NFS, CIFS, btrfs, CephFS, or GlusterFS. ZFS, LVM, LVM-thin, RBD, and iSCSI are left out of the storage menu, and a template whose boot disk is on one is refused. A storage type that cannot be read is refused when the build starts. [Templates on ZFS](#templates-on-zfs) is the way around it.

**qcow2 or raw, named for what it is.** A new template and a standalone image both ask `1` qcow2 or `2` raw. Enter is qcow2. A raw standalone file is `.raw`, not `.img`, because Proxmox does not list `.img` in `import/`. An update keeps that disk's format.

**A raw file stays sparse.** Publishing across filesystems, such as the cache onto NFS, skips zero blocks instead of writing all of them. Guest prep passes `--format` to virt-customize instead of letting it guess.

**What to build is the first question.** Enter creates a template. `2` replaces a template boot disk. `3` writes a standalone image. Guest prep runs on every image this tool downloads. A disk already on the host is left alone.

**A replaced boot disk stays where it is.** Storage and format are read from that disk. Those questions are not asked. A free VMID is refused. A new template still asks for storage, and an existing disk's storage is the first row when it is file storage.

**The field stays empty. The default is in the brackets.** A create-template VMID looks like `Debian 13 VMID [9002]: `. Enter accepts the bracket. A yes or no that does not need a specific word looks like `Accept these defaults? (y/n) [Yes]: `. `y` and `n` answer it. Menus still need a selection. `YES` and `DELETE` are still typed in full. On a terminal the question is bold cyan, the `(y/n) [Yes]` or `[9002]` part is bold blue, and what you type is teal. On the delete gate the word `DELETE` is red in the question and red as you type it.

**A terminal shows two progress headings.** `Converting <name> image...` is qemu-img. `Preparing <name> image for Proxmox PVE...` is one estimated line for guest prep. `-v` shows the virt-customize log. `-V` prints the version.

## Not in this version

Rocky, openSUSE, Arch, RHEL, and UEFI images. Writing to ZFS, LVM, RBD, or iSCSI directly. Absent, not quietly half-built.

PVE 9. One distro per run. Up to three releases. The last prompt still wants the word `YES`.
