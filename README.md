# pve-template-prep

Interactive prep for Proxmox VE 9 cloud images. One distro per run, up to three releases. It can write a disk image, create a template VM, or prep a stopped VM that already exists.

## Install and run

A live run has to be root on the PVE node. Use the block that matches the prompt you already have. Read `install.sh` before you run it.

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

The installer creates `/opt/pve-template-prep`, pulls `pve-template-prep.py` and `pve_prep/` from GitHub, checks that those files are root-owned and not writable by anyone else, and links the command to `/usr/local/sbin/pve-template-prep`. It also installs `libguestfs-tools` and `qemu-utils` if they are missing. Python 3, `qm`, and `pvesm` are already on PVE 9. To install somewhere else, use `DEST=/somewhere bash install.sh`, or `sudo DEST=/somewhere bash install.sh`. A clone is only useful if you want the unit tests. One downloaded script, without the `pve_prep/` directory beside it, will not start.

`sudo` only has to set the effective uid to 0. `qm`, `pvesm`, `qemu-img`, and `virt-customize` are started by that process and stay root. They do not need their own sudoers rules. A `NOEXEC` tag on the sudoers command would block them.

`pve-template-prep --dry-run` asks the same questions and prints the commands. It does not need root, and it does not download or change the host. There are no distro or release flags. Downloads land in `/var/tmp/pve-template-prep/cache`, mode `0700`, owned by root. `virt-customize --install` also fetches packages from inside the image. The node needs outbound network for a live run.

## What it asks

Empty input accepts the default when the question shows one. The last question is `Type yes to run:`. Only the exact answer `yes` starts work.

1. Distro, by number.
2. Releases, comma-separated, one to three. A codename works where the catalog has one (`bookworm`, `noble`).
3. Product. `1` image, `2` template, `3` existing. Enter selects template.
4. Disk format, unless the product is existing. `1` raw, `2` qcow2. Enter selects raw.
5. Where it goes. Image mode asks for a directory. Template and existing modes ask for a PVE storage id. A number picks from the detected list.
6. Image mode only: if that file already exists. `1` backup, `2` overwrite, `3` skip. Enter selects backup.
7. Template and existing modes: VMIDs, one per release.
   - Enter starts at 9001 and counts up. Two releases become 9001 and 9002.
   - One number counts up from there. `910` with two releases becomes 910 and 911.
   - A comma-separated list is used as written. `9001, 9050` stays those two IDs. The list length has to match the number of releases.
   - An inclusive range works when its length matches. `910-912` is three IDs.
8. Template mode only: type a VMID again to allow replacing it. Enter refuses every replace. A range is limited to VMIDs you already chose.
9. Guest prep. Enter means yes. `n` skips `virt-customize`. Template hardware is still applied.
10. Template mode only: hardware. Enter accepts bridge `vmbr0`, memory 2048 MB, cores 2. `n` asks for each value.
11. Read the summary. Type `yes`.

## What the three products do

**Image.** Download, optionally customize, and write `<distro>-<release>-pve.img` or `.qcow2` into the directory you named. Needs `qemu-img`. Does not need `qm`. The directory is a filesystem path, not a ZFS or LVM storage id.

**Template.** Customize the downloaded image while it is still a file, then `qm importdisk` into the storage you named. Any storage that accepts `images` works, including ZFS and LVM. Those volumes are raw. `qcow2` stays sparse on the cache disk until import. `raw` expands there first, which is a second full copy before the import. The guest name is `<distro>-<release>-cloud`. Needs `qemu-img`, `qm`, and `pvesm`. Guest prep also needs `virt-customize`.

**Existing.** Do not download. The VM must be stopped, and it must not already be a template. Prep runs on the OS disk in place, so `pvesm path` has to be a regular file. A zvol or an LVM volume is refused. Needs `qm`, `pvesm`, and, if prep is on, `virt-customize`. Does not need `qemu-img`.

One release failing does not cancel the rest, unless a template was destroyed and its replacement did not finish. In that case the run stops. The exit status is 0 only when every selected release succeeds.

## Replacing a VMID

Template mode destroys a VM only when all of these are true: you typed that VMID again, the VM exists, it is stopped, it is already a template, and the new image has been downloaded and prepped. If the replacement then fails, the run stops.

A stopped VM that is not a template is kept. The new disk is imported, `scsi0` is swapped, and the previous disk is removed only after that swap. Existing mode never destroys a VM. The confirm screen lists every VMID that may be destroyed, plus bridge, memory, and cores.

## Catalog

| Distro | Releases | Notes |
| --- | --- | --- |
| debian | 11, 12, 13 | 11 is EOL |
| ubuntu | 22.04, 24.04, 26.04 | |
| alma | 8, 9, 10 | |
| cloudlinux | 8, 9, 10 | Minimal OpenStack qcow2. Filename and SHA256 come from `catalog.json` at download. |
| fedora | 42, 43, 44 | 42 is EOL. The Generic qcow2 is chosen from the release index at download. |

No CentOS. CloudLinux logs in as `cloudlinux`. Its packages do not update until the guest is registered with a license. CloudLinux 6 and 7 are not listed.

## Guest prep

Default yes. Image and template mode apply it to the downloaded file before publish or import. Existing mode applies it to the OS disk after the hardware settings.

- Serial console
- `net.ifnames=0` (stable `eth0` names)
- qemu-guest-agent
- growpart
- chrony
- Reset machine-id and SSH host keys

Debian and Ubuntu: mask AppArmor, purge snapd.

Alma, CloudLinux, and Fedora: SELinux permissive, firewalld disabled. Same EL path.

`virt-customize` uses `LIBGUESTFS_BACKEND=direct` when that variable is unset, so libguestfs starts qemu itself instead of libvirt.

## Not in this version

Rocky, openSUSE, Arch, RHEL, and UEFI images.
