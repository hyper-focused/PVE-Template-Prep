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
  customize.py            guest prep through virt-customize
  vm.py                   qm command builders and config parsers. No live qm calls.
  build.py                one function per build action
  update.py               version comparison and the install command
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

For free IDs, "in use" means a VM or container on any node, read from `/etc/pve/.vmlist`. The file lists the whole cluster in one instant read. Probing with `qm status` cost about a second per ID, and each suggestion probed 32 candidates, which is where a 15-second wait before every field came from. The probe survives only as a fallback when the file cannot be read.

Yes/no questions are text fields on a terminal and on a pipe. A confirm widget would accept Enter without showing the bracket. Menus stay menus, and `YES` and `DELETE` stay typed words.

## The summary is a table

The summary was once a line per release (`debian 12 -> insert VMID 910 storage local qcow2 disk=overwrite ...`), then a line per replaced VMID, then the list again on the delete screen. It is now one table whose columns follow the job, so a standalone image has no VMID column to leave blank. `Existing` is the only column that can delete something, so it is the only one that turns red. The VMID list is not repeated on the last screen, which states the consequence in a sentence and asks for the word.

## Backups live outside the cache

Backups used to sit in the download cache. `/var/tmp` is not promised to survive a reboot, and cache cleanup is a routine yes/no, so a backup there could vanish with the downloads. Backups go to `/var/backups/pve-template-prep` (`0700`, root), which cleanup never opens. The copy runs before the new image is downloaded and counts only if `qemu-img convert` exits 0 and the file is non-empty, so a failed copy cannot be mistaken for protection.

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
