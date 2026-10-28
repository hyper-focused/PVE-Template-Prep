# Changelog

All notable changes to pve-template-prep. Newest first. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

An installed copy offers a new release when the version string on `main` changes. Changes merged without a version bump are listed under **Unreleased** and arrive only through a freshly downloaded `install.sh`, which upgrades in place.

## Unreleased

### Added
- **A template this tool just created can be enrolled for a weekly or monthly disk refresh.** The question comes after the builds finish and before the cache question, on a run that created the guest and converted it with `qm template`. The default is no (Enter installs nothing), and that answer applies to every template created in the run. The update path, standalone images, and a create left as a normal VM are not asked and are not enrolled. The record is a root-only JSON file per VMID under `/var/lib/pve-template-prep/`, not a Proxmox tag. A template with no file is never selected. The cron file is `/etc/cron.weekly/pve-template-prep` or `/etc/cron.monthly/pve-template-prep`, mode `0755`, and its body only execs `pve-template-prep --unattended` for that schedule. It does not download or call `qm`. Different text already at that path is left unchanged; later enrollments only add record files. `--unattended` skips the self-update prompt and the interview. A matching published checksum prints that the VMID is current. A different one backs up the live boot disk, attaches a newly prepped image, then deletes the old volume and runs `qm template`. The stored digest is rewritten only after that swap succeeds.

## 1.3.6 - 2026-10-27

### Changed
- `install.sh` installs `guestfs-tools` and `libguestfs-xfs` with `--no-install-recommends`, each only when dpkg says it is not installed. On PVE 9 (trixie) `libguestfs-tools` is a metapackage, and `guestfs-tools` Recommends it along with `virt-p2v`; skipping Recommends drops `libguestfs-tools`, `guestfish`, `guestmount`, and `virt-p2v` (20 packages down to 13 on a test node), none of which this tool calls. `libguestfs-xfs` is named explicitly so XFS root filesystems (RHEL-family guests) still mount. It no longer asks apt for `qemu-utils`: `qemu-img` already ships with `pve-qemu-kvm`, and `qemu-utils` is a real Debian package that could conflict with it. The "missing tool" error message gives the same command.
- README rewritten as a friendly overview: the problem, requirements, install, auto-update, a walkthrough, and troubleshooting. Technical detail moved into the new `docs/MANPAGE.md`.

### Security
- **The download cache can no longer be redirected by a local user.** Only the cache directory itself was checked for a symlink. A user who created `/var/tmp/pve-template-prep` first could swap `cache` for a symlink during a download and replace the verified image before import. Every directory on the path, as written and as resolved, must now belong to root and be writable by nobody else unless sticky. The backup directory and cache cleanup use the same check.
- **The swap tag no longer counts as a template.** A guest tagged `pve-prep-swap-<vmid>` without `template: 1` counted as a template, so the create path could `qm destroy` it and an update could replace its boot disk. Anyone who can edit tags could put that tag on a normal VM. Only `template: 1` counts now. A guest with only the tag is refused on both paths.

### Fixed
- **A template update that only fails to delete the old disk says so.** Once the new disk is the boot disk, deleting the previous volume is cleanup. That failure used to be reported as "the template flag was restored," which described a rollback. The guest is a template on the new image, the swap marker is removed, and the message says the template was updated and old disk cleanup failed. The previous volume stays unused. A failure before the new disk is attached still restores the template flag.
- **A template update attaches the image it imported.** It took the first `unusedN` line, so a template that already had an unused disk got that disk as its boot disk, and the old boot disk was deleted. The import is now the unused volume that is new since the import began. Snapshot sections are ignored.
- **A failed template update says how to finish.** The disk swap tags the template `pve-prep-swap-<vmid>` while its template flag is cleared. If the flag cannot be restored, the tag stays and the tool refuses the guest with `its disk update did not finish`: run `qm template <vmid>`, then update again. A failed attach deletes the volume it imported. The template's own tags are put back.
- **XFS support is installed on hosts that already have `virt-customize`.** `libguestfs-xfs` is only a Recommends of `libguestfs0`, and the installer skipped apt entirely when `virt-customize` was present. It now checks each package. Preflight stops with the install command when `libguestfs-xfs` is missing, or when `dpkg-query` cannot answer, instead of failing at mount time after the download.
- **Fedora picks the newest respin.** The release index is sorted as text, so `1.9` came after `1.10` and won. Composes are now compared as numbers, and the CHECKSUM file is the one for the chosen compose.
- **A container ID is no longer offered as free** when `/etc/pve/.vmlist` cannot be read. The fallback asked only `qm`, which reports a container's ID as missing; it now asks `pct` too.
- **VMID fields appear straight away.** Free VMIDs are read once from `/etc/pve/.vmlist` instead of one `qm status` per candidate. Each suggestion used to take 15 seconds or more. A container's ID now counts as taken too. The old per-ID probe remains as a fallback if the file cannot be read.
- **The installer checks modules by name** and removes any module or distro file the release does not ship. Running an older `install.sh` over a newer install used to stop with `pve_prep is missing modules`.

### Documentation
- Added `docs/MANPAGE.md` (technical reference), `docs/DESIGN.md` (decisions and reasoning), and this changelog. They replace `docs/reference.md`.

## 1.3.5 - 2026-10-20

### Added
- Licensed under Apache 2.0. `LICENSE` and `NOTICE` are installed next to the entry script, and every source file carries an SPDX identifier.

### Changed
- The update path asks for template VMIDs under the same **Template VMID(s)** section as the create path.

## 1.3.4 - 2026-10-20

### Changed
- The create path has a **Template VMID(s)** section with a two-line note, and every VMID field reads `<release> VM Template ID`.
- Defaults are shown as a `Setting` / `Default` table, drawn like the summary.
- The hardware question reads `Use this VM template config?`.
- The backup directory is named once, as the last line of the run, and only when a backup was written. The cache question no longer mentions backups.

### Fixed
- Keys typed ahead during a slow host lookup were printed twice. Terminal echo is now off while the interview runs.

## 1.3.3 - 2026-10-20

### Changed
- SSH is two questions: **SSH Password Authentication** and **Root SSH Access**. Each yes writes its own line into `00-enableroot.conf`. Two yeses write the same file as before. The summary shows `Password SSH` and `Root SSH`.

## 1.3.2 - 2026-10-20

### Changed
- **Disk backups moved** to `/var/backups/pve-template-prep` (mode `0700`, root). They used to share the download cache under `/var/tmp`, which is not promised to survive a reboot. Cache cleanup never opens the new directory and still skips a backup an older release left in the cache.
- Text fields show their parts in different styles: the question, the `(y/n) [Yes]` hint, and what you type. On the delete gate, `DELETE` is red.

## 1.3.1 - 2026-10-20

### Changed
- Every interview step is a section: a heading, a rule, then the question. A menu is erased once answered, leaving one line that names the choice.
- The summary is a table with an `Existing` column that says `new`, `replace`, or `delete VM` (with `back up,` in front when there is a backup). A disk that goes without a backup is red. The last screen is **Confirm Selections**.
- Dark and light palettes live in `pve_prep/palette.json`. The theme follows the terminal background, or `PVE_PREP_THEME`.
- Menu labels come from each distro file (AlmaLinux, not `alma`).

## 1.3.0 - 2026-10-20

### Added
- **What to build is the first question:** create templates, update a template's disk, or write standalone images.
- Standalone images can be qcow2 or raw.
- A raw file stays sparse when published across filesystems, such as the cache onto NFS.

### Changed
- **File storage only.** Every disk is a `.qcow2` or `.raw` file on directory, NFS, CIFS, btrfs, CephFS, or GlusterFS storage. ZFS, LVM, LVM-thin, RBD, and iSCSI are left out of the storage menu, and a template whose boot disk is on one is refused. Standalone images are the route onto block storage.
- A replaced boot disk stays on its current storage in its current format; those questions are not asked.
- Defaults appear in brackets on the VMID and yes/no questions. The field itself stays empty.
- `virt-customize` is given `--format` instead of probing the file.
- A terminal shows `Converting...` and `Preparing...` progress. `-v` shows the full `virt-customize` log.

### Removed
- In-place guest prep of a disk the tool did not download.
- Block-storage import as a raw volume.
- The `.img` extension for standalone files. Proxmox does not list `.img` in `import/`.

## 1.2.0 - 2026-10-17

### Changed
- Shorter questions, terminal color, and `qemu-img` convert progress.

## 1.1.3 - 2026-10-17

### Changed
- Replacing a template or its boot disk asks about a backup once, after the other options. Yes copies every disk first and confirms with `YES`. No deletes without a copy and confirms with `DELETE`.

### Fixed
- A normal VM is refused, and a linked clone is refused on template replace, disk replace, and stopped-VM prep.

## 1.1.2 - 2026-10-17

### Added
- Optional root password SSH, written to a `00-` drop-in so it wins over the image defaults.

## 1.1.1 - 2026-10-10

### Added
- An installed copy checks `main` for a newer version before the first question and offers to update.

### Fixed
- Volumes are listed even when `pvesm` rejects `--output-format` (PVE 9.2 prints the usage line last). The retry falls back to the text table, so a template with no linked clones can be replaced.

## 1.1.0 - 2026-10-10

### Added
- **Replace a template's boot disk** while keeping the template's config. Linked clones on any cluster node block both that swap and a full template replace.
- Each release asks for its own VMID. Enter assigns the next free ID from 9001.
- The volume format (qcow2 or raw) is chosen after the storage.

## 1.0.1 - 2026-10-09

### Changed
- Cache cleanup deletes working files but leaves disk backups in place, and the interview says so.

### Removed
- Fedora 42, which stopped building.

## 1.0.0 - 2026-10-09

First numbered release.

### Added
- Releases are loaded from one JSON file per distribution.
- `--version`, the startup line, and the download User-Agent share one version string. Each release being built is named on its own line.

### Removed
- Debian 11, whose security archive no longer ships `qemu-guest-agent`.

## Before 1.0.0

Initial development as `pve-cloud-prep`, 2026-10-08: Proxmox VE 9 cloud-image prep with an installer that fetches the runtime into `/opt`, prompts that explain themselves, menus on a terminal, a 16-color palette, new VMs set to `q35` and `x86-64-v2-AES`, and confirmation before converting a VM or deleting the prep cache. The entry script was renamed `pve-template-prep.py`.
