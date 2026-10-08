#!/bin/bash
# Prep Proxmox cloud templates.
# OS disk must be a file on NFS-SATA-SSD1. Cloud-init seed disks are ignored.
# Templates must be stopped. virt-customize needs outbound network for installs.
set -euo pipefail

STORAGE="NFS-SATA-SSD1"
export LIBGUESTFS_BACKEND="${LIBGUESTFS_BACKEND:-direct}"

declare -A DIST=(
  [901]=el
  [902]=el
  [903]=deb
  [904]=deb
  [905]=deb
  [907]=deb
  [908]=el
  [909]=deb
  [910]=el
)

need() { command -v "$1" >/dev/null || { echo "missing $1" >&2; exit 1; }; }
need qm
need pvesm
need virt-customize

disk_of() {
  local id="$1" line key vol rest pick=""
  while IFS= read -r line; do
    key="${line%%: *}"
    rest="${line#*: }"
    vol="${rest%%,*}"
    [[ "$key" =~ ^(scsi|virtio|sata|ide|nvme)[0-9]+$ ]] || continue
    [[ "$vol" == *cloudinit* || "$rest" == *media=cdrom* ]] && continue
    [[ "$vol" == ${STORAGE}:* ]] || continue
    pick="$vol"
    break
  done < <(qm config "$id")
  [[ -n "$pick" ]] || { echo "VM $id has no OS disk on $STORAGE" >&2; return 1; }
  pvesm path "$pick"
}

prep_one() {
  local id="$1" family="$2" disk status
  status="$(qm status "$id" 2>/dev/null | awk '{print $2}')"
  if [[ "$status" != "stopped" ]]; then
    echo "SKIP $id status=$status (stop it first)"
    return 0
  fi
  disk="$(disk_of "$id")" || return 0
  echo "=== $id ($family) $disk ==="

  qm set "$id" --agent enabled=1
  qm set "$id" --rng0 source=/dev/urandom
  qm set "$id" --serial0 socket --vga serial0
  # TRIM. This node's agent schema has no fstrim_clonedisks, so set it on the disk.
  local dkey dline drest dvol new
  dline="$(qm config "$id" | awk -F': ' '/^(scsi|virtio|sata|nvme)[0-9]+: / && $0 !~ /cloudinit/ && $0 !~ /media=cdrom/ {print; exit}')"
  if [[ -n "$dline" ]]; then
    dkey="${dline%%: *}"
    drest="${dline#*: }"
    dvol="${drest%%,*}"
    if [[ "$drest" == *,* ]]; then new="${dvol},${drest#*,}"; else new="$dvol"; fi
    [[ "$new" == *discard=on* ]] || new="${new},discard=on"
    [[ "$new" == *ssd=1* ]] || new="${new},ssd=1"
    qm set "$id" --"$dkey" "$new"
  fi

  local -a cmd=(virt-customize -a "$disk")
  if [[ "$family" == "deb" ]]; then
    cmd+=(
      --install qemu-guest-agent,cloud-init,cloud-guest-utils,openssh-server,ca-certificates,curl,sudo,chrony
      --run-command 'timedatectl set-timezone UTC || ln -sfn /usr/share/zoneinfo/UTC /etc/localtime'
      --run-command 'grep -q "^makestep" /etc/chrony/chrony.conf && sed -i "s/^makestep.*/makestep 1.0 3/" /etc/chrony/chrony.conf || echo "makestep 1.0 3" >> /etc/chrony/chrony.conf'
      --run-command 'mkdir -p /etc/default/grub.d'
      --run-command 'printf "%s\n" "GRUB_CMDLINE_LINUX=\"console=tty0 console=ttyS0,115200 net.ifnames=0 biosdevname=0\"" > /etc/default/grub.d/99-serial.cfg'
      --run-command 'update-grub || grub-mkconfig -o /boot/grub/grub.cfg'
      --run-command 'ln -sfn /dev/null /etc/systemd/system/apparmor.service'
      --run-command 'rm -f /etc/systemd/system/multi-user.target.wants/apparmor.service /etc/systemd/system/sysinit.target.wants/apparmor.service'
      --run-command 'apt-get clean'
      --run-command 'snap remove --purge snapd 2>/dev/null || apt-get purge -y snapd 2>/dev/null || true'
    )
  else
    cmd+=(
      --install qemu-guest-agent,cloud-init,cloud-utils-growpart,openssh-server,ca-certificates,curl,sudo,chrony
      --run-command 'ln -sfn /usr/share/zoneinfo/UTC /etc/localtime'
      --run-command 'grep -q "^makestep" /etc/chrony.conf && sed -i "s/^makestep.*/makestep 1.0 3/" /etc/chrony.conf || echo "makestep 1.0 3" >> /etc/chrony.conf'
      --run-command 'sed -i "s/^SELINUX=.*/SELINUX=permissive/" /etc/selinux/config'
      --run-command 'systemctl disable firewalld || ln -sfn /dev/null /etc/systemd/system/firewalld.service'
      --run-command 'grubby --update-kernel=ALL --args="console=tty0 console=ttyS0,115200 net.ifnames=0 biosdevname=0" || true'
      --run-command 'grep -q net.ifnames=0 /etc/default/grub || sed -i "s/\\(GRUB_CMDLINE_LINUX=\"[^\"]*\\)\"/\\1 console=tty0 console=ttyS0,115200 net.ifnames=0 biosdevname=0\"/" /etc/default/grub'
      --run-command 'dnf clean all || yum clean all || true'
    )
  fi
  cmd+=(
    --run-command 'mkdir -p /etc/cloud/cloud.cfg.d'
    --run-command 'printf "%s\n" "growpart:" "  mode: auto" "  devices: [\"/\"]" "resize_rootfs: true" > /etc/cloud/cloud.cfg.d/99-grow.cfg'
    --run-command 'mkdir -p /etc/systemd/system/getty.target.wants /etc/systemd/system/multi-user.target.wants'
    --run-command 'ln -sfn /usr/lib/systemd/system/serial-getty@.service /etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service || ln -sfn /lib/systemd/system/serial-getty@.service /etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service'
    --run-command 'ln -sfn /usr/lib/systemd/system/qemu-guest-agent.service /etc/systemd/system/multi-user.target.wants/qemu-guest-agent.service || ln -sfn /lib/systemd/system/qemu-guest-agent.service /etc/systemd/system/multi-user.target.wants/qemu-guest-agent.service'
    --run-command 'ln -sfn /usr/lib/systemd/system/chrony.service /etc/systemd/system/multi-user.target.wants/chrony.service || ln -sfn /lib/systemd/system/chrony.service /etc/systemd/system/multi-user.target.wants/chrony.service || ln -sfn /usr/lib/systemd/system/chronyd.service /etc/systemd/system/multi-user.target.wants/chronyd.service || ln -sfn /lib/systemd/system/chronyd.service /etc/systemd/system/multi-user.target.wants/chronyd.service'
    --run-command 'cloud-init clean --logs --seed --machine-id || true'
    --run-command 'rm -f /var/lib/dbus/machine-id /var/lib/cloud/instance'
    --run-command 'rm -rf /var/lib/cloud/instances'
    --run-command 'truncate -s 0 /etc/machine-id'
    --run-command 'rm -f /etc/ssh/ssh_host_*'
  )
  "${cmd[@]}"
  echo "OK $id"
}

for id in 901 902 903 904 905 907 908 909 910; do
  prep_one "$id" "${DIST[$id]}"
done
echo "done"
