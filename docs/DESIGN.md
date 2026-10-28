# Design notes

Why pve-template-prep works the way it does. This is for people changing the code: each note is a decision that a future change has to argue with. What the program does, prompt by prompt, is in [MANPAGE.md](MANPAGE.md). What changed and when is in [CHANGELOG.md](CHANGELOG.md).

## Layout

```
pve-template-prep.py      entry point: arguments, preflight, running the job
pve_prep/
  job.py                  Job: everything one run will do. No I/O.
  prompts.py              the interview; fills a Job
  ui.py                   terminal widgets for the interview
  catalog.py              image catalog; reads distros/*.json. No network.
  distros/<distro>.json   one file per distribution; a release is a row
  download.py             download, verify, convert, publish. Stdlib only.
  safedir.py              root-only directories no other user can redirect
  customize.py            guest prep through virt-customize
  vm.py                   qm command builders and config parsers. No live qm calls.
  build.py                one function per build action
  update.py               version comparison and the install command
  enroll.py               one JSON enrollment file per VMID. No qm.
  cronscript.py           the cron wrapper; execs this program and nothing else
  termbg.py               light or dark terminal background
  palette.json            colors, as data
vendor/                   prompt_toolkit, questionary, wcwidth
```

The interview produces a `Job`; the build consumes it. Nothing the interview decides is recomputed later, and nothing in the build asks a question. `vm.py` builds and parses `qm` text but never runs it, so the parsing can be tested without a Proxmox host.

Adding a distribution or release is a data change in `distros/*.json`. Guest prep has one path per package family (`deb` and `el`), not one per distribution.

## Dependencies are vendored

`vendor/` holds questionary, prompt_toolkit, and wcwidth, pinned in `vendor/VERSIONS`. The install needs no pip and no `python3-venv`, and the target is a hypervisor that should not grow a toolchain. The wcwidth C extension is deliberately left out, and `install.sh` fails if it is present, so the tree is pure Python and identical on every node.

## File storage only

Every disk the tool writes is a file. `vm.is_file_storage` is the single rule: `dir`, `nfs`, `cifs`, `btrfs`, `cephfs`, `glusterfs`. Anything else, including an unreadable or empty type, is not file storage.

The rule is applied four times, because each is a different failure: the new-template storage menu leaves block storage out; the update VMID check refuses a boot disk on it; a recommended storage on it is dropped; and the build checks again before the download on a live run, in case the job was built some other way.

Before 1.3.0 a block storage was imported as raw. That path was removed rather than kept as an option: one rule (files only) is easier to hold than a rule with a block-storage branch. A standalone image imported in Proxmox is the supported way onto block storage.

## Extensions are the format

`.img` is only a name; Ubuntu's cloud `.img` is qcow2 inside. Proxmox reads the format from the extension: `images` accepts `raw`, `qcow2`, `vmdk`, and `subvol`, and `import/` accepts `ova`, `ovf`, `qcow2`, `raw`, and `vmdk`. A `.img` in `import/` is not listed and its volume ID does not parse. `published_name` therefore always writes `.qcow2` or `.raw`, so the extension never disagrees with the contents. Downloads are unaffected: `qemu-img convert` probes the source regardless of its name.

## A sparse publish

The cache is under `/var/tmp`; an import directory is often NFS or another mount. `os.replace` then fails with `EXDEV` and the file is copied. The copy seeks over every all-zero 1 MiB chunk and truncates to the full length at the end, so a 20 GB raw image holding 2 GB of data stays about 2 GB on ext4, XFS, and NFS. It writes to `.<name>.<pid>.partial`, syncs, then renames, so a reader never sees a half-written image.

## The first question is the job

The first question is what to build, because the VMID, storage, and format questions all depend on it.

`Job.mode` stays `template` or `image`. A standalone image is image mode with an empty VMID list, so the file is published. An update is image mode with a required template VMID, so the boot disk is replaced. There is no third mode and no `mode="existing"`. The interview does not fill `template_vmids` for an update; they are separate fields.

An earlier version prepped a disk in place, applying the family you picked to a disk the tool never downloaded. That was removed. Templates this tool built already carry guest prep, and the family now comes only from the catalog row; the tool does not read `/etc/os-release` off a disk it did not build.

## Guest prep is one pass on the downloaded file

It always runs, on the file, before import or publish. Packages install first. The last shared command clears history, logs, `/tmp`, leases, the random seed, and persistent NIC rules, after `cloud-init clean`, the machine-id reset, and removal of `ssh_host_*`. The SSH drop-in is appended after that cleanup so the cleanup cannot remove it.

`virt-sysprep` is not used. A second generalize pass runs another boot and can delete users, CA certificates, and sshd configuration, undoing the drop-in and the other files the first pass just wrote. The cleanup list is explicit and does not touch `sshd_config.d`, `cloud.cfg.d`, or account databases.

`LIBGUESTFS_BACKEND=direct` is the default so libguestfs launches qemu itself instead of asking libvirt for permission.

## Storage and format follow the disk that stays

An update reads both from `qm config` and does not ask. A wrong storage, or qcow2 imported onto a raw disk, breaks the VM configuration, and a question invites exactly that mistake. `format=` wins; otherwise the filename suffix decides. A volume name with no dot is a block volume, so that VMID is refused before the format matters. Any other `format=` value, including `vmdk`, comes back empty and the VMID is asked again, as does unreadable storage or a missing OS disk. The tool does not guess qcow2.

A full template replace still asks for storage, because the old VM is destroyed and a new one created, so the storage is a real choice. The current disk's storage is moved to the first row and Enter keeps it. The menu widget ignores a default argument, so the recommendation is row order, not a prefilled value. Unreadable storage on that path is a note, not a re-ask.

A standalone image takes its storage from `storage.cfg` entries whose content includes `import` and that have a `path`. When `pvesm status` returns storages, an ID absent from it is dropped; an empty status keeps every import storage from the file. No import storage ends the interview rather than falling back to a typed directory, which would invite pointing a template at an arbitrary path.

## Fields stay empty; the default is in the brackets

A prefilled VMID had to be backspaced before it could be replaced. Fields are empty, the default shows in brackets, and Enter accepts it. This applies to the create-path VMID and to every yes/no.

For free IDs, "in use" means a VM or container on any node, read from `/etc/pve/.vmlist`. The file lists the whole cluster in one instant read. Probing with `qm status` cost about a second per ID, and each suggestion probed 32 candidates, which is where a 15-second wait before every field came from. The probe survives only as a fallback when the file cannot be read. It asks `qm status` and then `pct status`, because `qm` has never heard of a container and would call its ID free; an ID is free only when both say the config does not exist.

Yes/no questions are text fields on a terminal and on a pipe. A confirm widget would accept Enter without showing the bracket. Menus stay menus, and `YES` and `DELETE` stay typed words.

## The summary is a table

The summary was once a line per release (`debian 12 -> insert VMID 910 storage local qcow2 disk=overwrite ...`), then a line per replaced VMID, then the list again on the delete screen. It is now one table whose columns follow the job, so a standalone image has no VMID column to leave blank. `Existing` is the only column that can delete something, so it is the only one that turns red. The VMID list is not repeated on the last screen, which states the consequence in a sentence and asks for the word.

## Backups live outside the cache

Backups used to sit in the download cache. `/var/tmp` is not promised to survive a reboot, and cache cleanup is a routine yes/no, so a backup there could vanish with the downloads. Backups go to `/var/backups/pve-template-prep` (`0700`, root), which cleanup never opens. The copy runs before the new image is downloaded and counts only if `qemu-img convert` exits 0 and the file is non-empty, so a failed copy cannot be mistaken for protection.

## The whole cache path is checked

The cache lives under `/var/tmp`, which every user can write. Checking only the cache directory for a symlink left its parent open: a local user who created `/var/tmp/pve-template-prep` first owned it, so they could swap `cache` for a symlink after root had checked it, during the download, and replace the verified image before it was imported. `pve_prep/safedir.py` now walks every directory from `/` to the cache, both as written and as resolved, and requires each to belong to root and to be writable by nobody else unless sticky. Once that holds, no other user can rename anything on the path, so the check stays true for the rest of the run. A parent someone else owns is refused with its uid rather than repaired: deleting another user's files as root is the operator's call. The backup directory uses the same check.

## Imports are found by difference

`qm importdisk` puts the new volume in the next free `unusedN` slot. Taking the first `unusedN` line attached whatever was already unused and deleted the old boot disk, leaving the new image unused. The import is now the one unused volume that was not in the config read before the import. None, or more than one, is a failed import. Only the live config counts; `[snapshot]` sections are skipped by every parser that reads keys.

## A disk swap leaves a marker until it is done

Swapping a template's boot disk clears the template flag for the edit. If putting it back fails, the guest is a normal VM. The `qm set` that clears the flag also adds the tag `pve-prep-swap-<vmid>`, in one config write, so a half-swapped guest always says what happened. The tag does not make the guest count as a template. Anyone who can edit tags can set it, and treating it as a template let the create path `qm destroy` a guest that was not one, or let an update replace a normal VM's boot disk. A guest with its own tag and no template flag is refused on both paths with a message to run `qm template <vmid>` and then update again. That costs one command in a rare case; the tool keeps its rule of never touching a normal VM. The guest's own tags are written back once it is a template again, which also clears a tag an earlier run left. A failed attach also deletes the volume it just imported, if that volume is still unused, so retries do not pile up orphans. Deleting the previous volume happens after the new disk is the boot disk. If that delete fails, the swap is already done: `qm template` still runs, the marker comes off, and the result says the template was updated and old disk cleanup failed. The previous volume stays unused. The run still counts as an updated template, because the guest is on the new image.

## The enrollment record is a file, not a tag

An unattended refresh has to know which guests this tool created and converted, which digest was applied, and which SSH answers were used. A Proxmox tag cannot be that record. Anyone who can edit tags can set one. That is why `pve-prep-swap-<vmid>` does not make a guest count as a template: a tag is guest configuration, and treating it as authority let the tool destroy or rewrite a guest it did not create. The proof is a root-only JSON file, `/var/lib/pve-template-prep/<vmid>.json`, written only after this run created the guest and `qm template` succeeded. The update path, a standalone image, and a create left as a normal VM get no file. A template with no file is never selected, so a guest this tool did not create cannot be enrolled by tagging it. The file holds `vmid`, `distro`, `release`, `digest`, `checksum_alg`, `password_auth`, `permit_root`, and `schedule`. Newer means the catalog's current published checksum for that distro and release differs from the stored digest. The digest is rewritten only after the disk swap succeeds, so a failed run leaves the old digest in place and tries again next time.

## The cron script is a wrapper

Cron must not grow a second copy of the download, the guest prep, or the `qm` commands. `/etc/cron.weekly/pve-template-prep` or `/etc/cron.monthly/pve-template-prep` (mode `0755`) is only a wrapper. Its whole body is:

```sh
#!/bin/sh
exec /usr/local/sbin/pve-template-prep --unattended weekly
```

The monthly file says `monthly`. The script does not download and it does not call `qm`. `--unattended` runs this program, so the swap stays the update path's swap. If that path already exists with different text, it is left unchanged. The tool does not overwrite a script it did not write. Later enrollments only add record files. A dry run prints the script and the record and writes neither.

## The new disk is attached before the old one is deleted

The unattended swap does not detach the boot disk first. Detach, then attach, leaves the template with no boot disk when the attach fails. The order is the one the interactive update already uses. The current boot disk is backed up to `/var/backups/pve-template-prep`. The new image is downloaded and prepped with the SSH answers stored on the record. It is attached to the boot bus. Only then is the old volume deleted and `qm template` run. A failed attach still has the old disk. A failed delete is cleanup: the guest is already on the new image, which is the same outcome as the marker section above. Storage and format are read from that live boot disk, so a move onto another file storage still updates, and block storage is still refused. Memory, cores, bridge, name, and firewall are not rewritten.

The run assumes the boot disk was not customized after creation. It does not look for those edits. A newer published checksum replaces the disk, and the backup is what remains of the previous one.

## Linked clones

A linked clone depends on the template's base volume, so replacing that volume under it corrupts the clone. The check reads every guest configuration under `/etc/pve/nodes`, not just the local node, plus the storage volume list, because clones on shared storage may live anywhere in the cluster. If either source cannot be read the guest is left alone: refusing a safe replacement costs a retry; allowing an unsafe one costs data.

## Colors follow the background

One dark palette was unreadable on a light terminal, so the tool asks the terminal. It sends OSC 11 (background color) and then DA1 (`CSI c`). Every terminal answers DA1, so a DA1 reply with no OSC 11 reply before it means "unsupported" and the probe returns without waiting. Only a terminal that answers nothing at all waits out the one-second cap, which is sized for an SSH round trip across an ocean. The terminal is restored with `TCSAFLUSH` so a late reply is discarded instead of landing in the first prompt. The fallbacks are `COLORFGBG`, then dark.

`termbg.py` does this with the standard library. `term-background` on PyPI was considered and rejected: it does not send OSC 11, its `COLORFGBG` check reports `0;15` as dark, and it is GPL-3.0-or-later while `vendor/` is MIT and BSD.

The palette is data. Each role has one 256-color index and one ANSI fallback per theme. A 24-bit terminal gets that index's exact xterm RGB so it matches the 256-color look; a 16-color terminal gets ANSI names, not the nearest hex. prompt_toolkit's own style reverses a selected row; the menus turn that off in favor of a marker.

## Progress output

`virt-customize` prints `[  15.6]` as elapsed seconds, not percent, so showing its log as a progress bar would lie. On a terminal the tool shows one estimated line instead and hides the log, which `-v` restores. A pipe gets the log, since there is no line to rewrite. On failure the captured log is printed before the error so the cause is not lost with the progress line.

## Updates compare the version string, not the commit

The update check reads `__version__` from `main` and compares it with the installed copy. That keeps the check to a single small request that sends nothing, and it lets a release be a deliberate act: commits that do not bump the version are not offered. The cost is that a fix merged without a bump reaches users only when they rerun `install.sh`, which is why the installer upgrades in place and removes files a release no longer ships. A git checkout is never updated, since replacing a working tree under a developer is rude.

`install.sh` compares module names rather than a count so that running an older script over a newer install reports the file that differs instead of a bare mismatch.

## Installer ownership checks

The install runs as root and the tool later runs as root, so the code it loads must not be writable by anyone else. `install.sh` checks that the files are root-owned and not group- or world-writable, refuses a symlinked destination, and refuses system directories such as `/usr` as `DEST`. It treats `pve_prep/*.py` and `distros/*.json` as owned by the installer, because the catalog loads every JSON it finds.
