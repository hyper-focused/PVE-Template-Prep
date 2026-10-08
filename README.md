# pve-cloud-prep

Interactive prep for Proxmox VE 9 cloud images. One distro per run, up to three of its releases, as a published image, a new template, or an in-place prep of an existing VM.

Downloads are cached at `/var/tmp/pve-cloud-prep/cache`. Guest prep is sequential. One release failing does not cancel the rest. The exit status is 0 only when every selected release succeeds.

Template mode can destroy a **stopped** VM at a chosen VMID only if you type that VMID again at the confirm. Anything else is left alone. Existing mode never destroys a VM.

## Requirements

- Run the real job as root on the PVE node. `--dry-run` does not need root.
- Python 3, already on PVE 9. Stdlib only.
- `apt-get install libguestfs-tools qemu-utils`
- Outbound network. `virt-customize --install` fetches packages from inside the image.
- Template mode runs `virt-customize` on the downloaded image, then `qm importdisk` into any storage that accepts images, including ZFS and LVM. Those volumes are raw. The format prompt chooses the local file that gets customized. `qcow2` stays sparse until import. `raw` expands on the cache disk first.
- Image mode writes a file into a directory. That path is not a ZFS or LVM storage id.
- Existing mode customizes the OS disk in place, so `pvesm path` has to be a regular file. A zvol or an LVM volume is refused.

`qm` and `pvesm` come with PVE. Image mode does not need them. Existing mode does not need `qemu-img`.

## Run

```sh
python3 pve-cloud-prep.py
python3 pve-cloud-prep.py --dry-run
```

There are no distro or release flags. The prompts are the interface. The last question is `Type yes to run:`. Only the exact answer `yes` starts work. Anything else aborts.

Leave the VMID question empty to start at 9001. Each extra release takes the next ID: 9002, 9003, and so on.

`--dry-run` walks the same prompts, prints what would run, and does not change the host. A real run has to be on the PVE node. The unit tests in this repo do not boot a guest.

Replacing a VMID that is already a template destroys that template, and only after the new image is downloaded and prepped. If the replacement then fails, the run stops. A stopped VM that is not a template is kept: the new disk is imported and `scsi0` is swapped, and the previous disk is removed only after that swap. Existing-mode prep refuses a template. The confirm screen lists every VMID that may be destroyed, plus bridge, memory, and cores.

## Catalog

| Distro | Releases | Notes |
| --- | --- | --- |
| debian | 11, 12, 13 | 11 is EOL |
| ubuntu | 22.04, 24.04, 26.04 | |
| alma | 8, 9, 10 | |
| cloudlinux | 8, 9, 10 | Minimal OpenStack qcow2. The dated filename and SHA256 come from `https://images.cloudlinux.com/catalog.json` at download. |
| fedora | 42, 43, 44 | 42 is EOL |

No CentOS. Do not point this at a CentOS image and hope.

CloudLinux uses the same EL guest prep as Alma. The image logs in as `cloudlinux`. Packages do not update until that guest is registered with a CloudLinux license. CloudLinux 6 and 7 are older than the three current majors, so they are not listed.

## Guest prep

Optional, default yes. Applied to the image file before it is published or turned into a template, and to the OS disk of a stopped existing VM.

- Serial console
- `net.ifnames=0` (stable `eth0` names)
- qemu-guest-agent
- growpart
- chrony
- Reset machine-id and SSH host keys

Debian and Ubuntu: mask AppArmor, purge snapd.

Alma, CloudLinux, and Fedora: SELinux permissive, firewalld disabled.

`virt-customize` is run with `LIBGUESTFS_BACKEND=direct` (set only when the variable is unset). That makes libguestfs start qemu itself instead of going through libvirt, which is the wrong backend on a PVE host.

## Not in this version

Rocky, openSUSE, Arch, RHEL, and UEFI images. Later.
