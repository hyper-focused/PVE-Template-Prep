# pve-template-prep

1. Pick a distro: Debian, Ubuntu, AlmaLinux, CloudLinux, or Fedora.
2. Pick the releases you need.
3. Answer a few simple questions.
4. ????
5. Profit! Enjoy your shiny new Proxmox PVE KVM templates.

If you've been a Proxmox PVE admin for any length of time, you're certainly familiar with the tedious process of creating templates. You download a generic image, hope it imports correctly, then fight with the generic cloud image when it won't boot (I'm pointing at you, Ubuntu). Finally, you say to yourself "To heck with it, I'll just download the stupid ISO and call it a day" - for the 42nd time this year.

This app was developed to solve that problem, and turn template creation into a task you can complete in a few minutes instead of a few hours.

## What it does

It asks you a handful of questions, then does the clicking for you. It downloads the official cloud image, verifies its checksum, preps it so it behaves on Proxmox (serial console, guest agent, clean machine-id, sane NIC names, and friends), and hands it to Proxmox. Three flavors:

- **Create new templates.** A brand new VM template, named `<distro>-<release>-cloud`, ready to clone.
- **Update an existing template.** Swap in a fresh boot disk and leave the rest of the template's config alone.
- **Create standalone disk images.** A `.qcow2` or `.raw` file you can import from the web UI or the CLI.

## What you need

- A working **Proxmox VE 9** node (x86_64) and root access to it. If your node can already run a VM and reach the internet, you're 95% there.
- **`guestfs-tools`** and **`libguestfs-xfs`** (XFS support, for RHEL-family guests). These are the only things a stock PVE install doesn't ship. The install steps below take care of them.
- Working **apt** sources. Without a subscription, that means the no-subscription repo, not the enterprise one that answers `401`.
- Outbound **HTTPS** to GitHub and the distro mirrors.
- **File-based storage** (directory, NFS, CIFS, btrfs, CephFS, or GlusterFS) for anything the tool writes. ZFS, LVM, RBD, and iSCSI are not supported as a destination, but there is an easy way around it (see [When things go sideways](#when-things-go-sideways)).
- Some free space in `/var/tmp` for downloads and working copies, and in `/var/backups` if you keep disk backups.

Already on the node: Python 3, `qm`, `pvesm`, and `qemu-img`. Nothing to pip, nothing to venv.

## Install

Become root (`sudo -i` works), then:

```sh
apt update && apt -y install --no-install-recommends guestfs-tools libguestfs-xfs
curl -fsSLO https://raw.githubusercontent.com/hyper-focused/PVE-Template-Prep/main/install.sh
bash install.sh
pve-template-prep
```

**What to expect:** the installer takes a few seconds. It downloads the tool from GitHub into `/opt/pve-template-prep`, makes sure the files are root-owned and locked down, and links the command at `/usr/local/sbin/pve-template-prep`. It installs both packages itself if you skipped that line. It touches nothing else on the host. Run it again later and it upgrades in place.

Want a look first? `pve-template-prep --dry-run` asks the same questions and prints what it *would* do. No root, no downloads, no changes.

## Staying up to date

Every run, the tool peeks at the version number on GitHub's `main` branch. That's the only thing it sends or reads. If a newer release exists, it asks:

```
Fetch it now? (y/n) [No]:
```

Enter keeps the current copy. `y` installs the new one and restarts into it. If GitHub can't be reached, it shrugs and carries on. Set `PVE_PREP_SKIP_UPDATE=1` to skip the check. `pve-template-prep -V` prints the version without checking anything.

## A quick walkthrough

Run `pve-template-prep` and answer in order. Enter takes the default almost everywhere, and nothing happens until the very last screen.

1. **What to build.** New template (default), update an existing template's disk, or a standalone image.
2. **Distro**, then **one to three releases**. Space ticks a release, Enter accepts the list.
3. **VMIDs** (new template and update only). Enter picks the next free ID from 9001. Typing an ID that's in use means "replace it", and only a template can be replaced.
4. **Storage and format.** qcow2 or raw. A new template asks where its disk goes. A standalone image asks which storage's `import/` folder. An update reuses the storage and format the old disk had.
5. **Defaults.** SSH password login: yes. Root SSH login: yes. Bridge `vmbr0`, 2048 MB, 2 cores, convert to a template when done. Answer `n` to be asked about each.
6. **Backup** (only if something is being replaced). Enter copies the old disk to `/var/backups/pve-template-prep` first.
7. **Summary.** A table of every VMID and exactly what happens to it. Read it. Type `YES` to go, or `X` to bail. If you declined a backup and something would be deleted, the word is `DELETE`, in red.

Then it downloads, verifies, converts, preps, and imports each release. At the end it offers to clear the working files.

| You picked | You get |
| --- | --- |
| Create new templates | A new VM named `<distro>-<release>-cloud`, converted with `qm template` unless you said no |
| Update existing templates | A fresh boot disk on a template you already have. Firewall, options, and the rest stay put |
| Standalone images | `<distro>-<release>-pve.qcow2` or `.raw` in that storage's `import/` directory |

## When things go sideways

| What you see | What's going on | What to do |
| --- | --- | --- |
| `missing virt-customize` or `missing libguestfs-xfs` | `guestfs-tools`, or its XFS support for RHEL-family guests, isn't installed | `apt update && apt -y install --no-install-recommends guestfs-tools libguestfs-xfs` |
| `unsafe cache dir ... owned by uid N` | Someone other than root created `/var/tmp/pve-template-prep`, so they could swap your image mid-download | Check who and why, then `rm -rf /var/tmp/pve-template-prep` and run again |
| An update failed and the template is now a normal VM | The template flag couldn't be put back. The VM carries the tag `pve-prep-swap-<vmid>`, so the tool still treats it as its template | Run the update again to finish it, or `qm template <vmid>` |
| A VMID is refused | It belongs to a normal VM or a container. The tool only replaces *templates*, and never touches a normal VM | Pick another ID. Enter always avoids IDs in use |
| Update says the VMID is free | Updating needs a template that already exists | Use "create new template" instead |
| "Linked clones" refusal | Something is cloned from that template and still depends on its disk. Clones on other cluster nodes count too. If the check can't be read, the tool plays it safe | Remove or detach the clones, or use a different VMID. The template is never changed while clones exist |
| Storage not offered, or a template refused | Your storage is ZFS, LVM, LVM-thin, RBD, or iSCSI. This tool only writes `.qcow2` and `.raw` files | See the ZFS/LVM note below |
| Can't SSH in as root | The tool opens the SSH door but doesn't set a password. Proxmox only applies the password to the cloud-init user | Set the template's cloud-init user to `root`, plus a password or key |
| CloudLinux can't install updates | It needs a license, and unlicensed it's a very calm brick | Register the guest |
| A build died partway | The download is kept in the cache | Run it again. It reuses the download |
| Where did my old disk go? | You said yes to the backup | `/var/backups/pve-template-prep`, as `vm-<vmid>-<bus>.bak.<UTC>.qcow2`. Cleanup never touches it |

**About ZFS, LVM, and other block storage.** The tool builds disks as files, so it won't write to a zvol, a logical volume, RBD, or iSCSI. Those storages are left out of the menus, and updating a template whose boot disk lives on one is refused. The workaround is painless: build a **standalone image** (it lands in a file storage's `import/` folder), then in Proxmox use **Hardware, Add, Import Hard Disk** and pick your ZFS or LVM storage as the target. Proxmox converts it on the way in, and the guest prep rides along. The CLI version is in the [reference](docs/MANPAGE.md#examples).

---

# Under the hood

## Where the images come from

All images are the distro's official 64-bit cloud images, checked against the checksum the vendor publishes. A bad checksum stops the build.

| Distro | Releases | Source |
| --- | --- | --- |
| Debian | 12, 13 | `cloud.debian.org` genericcloud, `SHA512SUMS` |
| Ubuntu | 22.04, 24.04, 26.04 | `cloud-images.ubuntu.com` server cloudimg, `SHA256SUMS` |
| AlmaLinux | 8, 9, 10 | `repo.almalinux.org` GenericCloud, `CHECKSUM` |
| CloudLinux | 8, 9, 10 | `images.cloudlinux.com/catalog.json` supplies the file and its SHA256 |
| Fedora | 43, 44 | `download.fedoraproject.org` Generic qcow2, picked from the release index |

Each distro is one file in `pve_prep/distros/`, and a release is one row in it. No CentOS, no Rocky, no UEFI images, in this version.

## What guest prep does

Every image this tool builds gets the same treatment before Proxmox sees it:

- Serial console, stable `eth0` naming, qemu-guest-agent, growpart, and chrony.
- A fresh start: machine-id and SSH host keys reset, shell history, logs, DHCP leases, and temp files cleared.
- Debian and Ubuntu: AppArmor masked, snapd purged. Alma, CloudLinux, and Fedora: SELinux permissive, firewalld off.
- The SSH settings you said yes to (below).

## SSH access

Answering yes to the two SSH questions writes `/etc/ssh/sshd_config.d/00-enableroot.conf` **inside the image**:

```
PasswordAuthentication yes
PermitRootLogin yes
```

That's convenient for a template and not great for production, so tighten SSH after your first login. To undo it on a guest:

```sh
rm /etc/ssh/sshd_config.d/00-enableroot.conf
systemctl restart ssh || systemctl restart sshd
```

## Output types and requirements

| Path | Writes | Needs |
| --- | --- | --- |
| New template | A new VM, with the disk imported by `qm importdisk` onto file storage you pick | `qm`, `pvesm`, `qemu-img`, `virt-customize` |
| Update | A replacement boot disk, on the storage and in the format the old one used | `qm`, `pvesm`, `qemu-img`, `virt-customize` |
| Standalone image | A `.qcow2` or `.raw` in a storage's `import/` directory (storage must list `import` content) | `qemu-img`, `virt-customize` |

A raw file stays sparse, even across to NFS. There's no `.img`: Proxmox ignores `.img` in `import/`.

## Files and locations

| Path | What |
| --- | --- |
| `/opt/pve-template-prep/` | The install |
| `/usr/local/sbin/pve-template-prep` | The command |
| `/var/tmp/pve-template-prep/cache/` | Downloads and working copies. The end-of-run question can clear it |
| `/var/backups/pve-template-prep/` | Disk backups. Never auto-deleted |

## Need every detail?

- [docs/MANPAGE.md](docs/MANPAGE.md): the technical reference. Every prompt, option, file, environment variable, and exit code.
- [docs/CHANGELOG.md](docs/CHANGELOG.md): what changed in each release.
- [docs/DESIGN.md](docs/DESIGN.md): why it works the way it does, for anyone changing the code.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). Copyright 2026 Robert Tyree and hyper-focused. Run it at home, at work, or on a thousand nodes. If you ship it inside something you hand to others, keep `NOTICE` with it. `vendor/` holds prompt_toolkit (BSD-3-Clause), questionary (MIT), and wcwidth (MIT), under their own licenses in `vendor/LICENSES`.
