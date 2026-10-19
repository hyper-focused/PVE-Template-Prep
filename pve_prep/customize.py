"""virt-customize guest prep.

The base command text matches prep-template.sh. Root password SSH is an
extra command, written only when the interview asked for it.
"""

import os
import shlex
import subprocess

_DEB_PACKAGES = (
    "qemu-guest-agent,cloud-init,cloud-guest-utils,openssh-server,"
    "ca-certificates,curl,sudo,chrony"
)
_EL_PACKAGES = (
    "qemu-guest-agent,cloud-init,cloud-utils-growpart,openssh-server,"
    "ca-certificates,curl,sudo,chrony"
)

# Guest shell text, not the host script's extra quote-escaping.
_DEB_COMMANDS = (
    "timedatectl set-timezone UTC || ln -sfn /usr/share/zoneinfo/UTC /etc/localtime",
    'grep -q "^makestep" /etc/chrony/chrony.conf && sed -i "s/^makestep.*/makestep 1.0 3/" /etc/chrony/chrony.conf || echo "makestep 1.0 3" >> /etc/chrony/chrony.conf',
    "mkdir -p /etc/default/grub.d",
    r'printf "%s\n" "GRUB_CMDLINE_LINUX=\"console=tty0 console=ttyS0,115200 net.ifnames=0 biosdevname=0\"" > /etc/default/grub.d/99-serial.cfg',
    "update-grub || grub-mkconfig -o /boot/grub/grub.cfg",
    "ln -sfn /dev/null /etc/systemd/system/apparmor.service",
    "rm -f /etc/systemd/system/multi-user.target.wants/apparmor.service /etc/systemd/system/sysinit.target.wants/apparmor.service",
    "apt-get clean",
    "snap remove --purge snapd 2>/dev/null || apt-get purge -y snapd 2>/dev/null || true",
)

_EL_COMMANDS = (
    "ln -sfn /usr/share/zoneinfo/UTC /etc/localtime",
    'grep -q "^makestep" /etc/chrony.conf && sed -i "s/^makestep.*/makestep 1.0 3/" /etc/chrony.conf || echo "makestep 1.0 3" >> /etc/chrony.conf',
    'sed -i "s/^SELINUX=.*/SELINUX=permissive/" /etc/selinux/config',
    "systemctl disable firewalld || ln -sfn /dev/null /etc/systemd/system/firewalld.service",
    'grubby --update-kernel=ALL --args="console=tty0 console=ttyS0,115200 net.ifnames=0 biosdevname=0" || true',
    # One backslash: guest sh -c leaves \( \) \1 for sed BRE. Do not double them.
    r'grep -q net.ifnames=0 /etc/default/grub || sed -i "s/\(GRUB_CMDLINE_LINUX=\"[^\"]*\)\"/\1 console=tty0 console=ttyS0,115200 net.ifnames=0 biosdevname=0\"/" /etc/default/grub',
    "dnf clean all || yum clean all || true",
)

# Last, after package install and the config writes. Not virt-sysprep:
# that second boot would also delete users, CA certs, and sshd config.
# This does not touch sshd_config.d, cloud.cfg.d, or account databases.
_CLEAN_IDENTITY = (
    "find /root /home -maxdepth 3 \\( "
    "-name .bash_history -o -name .zsh_history -o -name .ash_history -o "
    "-name .python_history -o -name .lesshst -o -name .viminfo -o "
    "-name .wget-hsts -o -name .mysql_history \\) -delete 2>/dev/null || true; "
    "find /var/log -type f -delete 2>/dev/null || true; "
    "find /tmp /var/tmp -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null || true; "
    "rm -rf /var/lib/dhclient /var/lib/dhcp /var/crash /var/spool/mail /var/mail "
    "/var/lib/yum/uuid /etc/smolt/hw-uuid /etc/sysconfig/rhn/hw-uuid "
    "/var/lib/systemd/random-seed /var/lib/rpm/__db* 2>/dev/null || true; "
    "rm -f /etc/udev/rules.d/70-persistent-net.rules "
    "/etc/udev/rules.d/70-persistent-cd.rules "
    "/etc/passwd- /etc/group- /etc/shadow- /etc/gshadow- /etc/subuid- /etc/subgid- "
    "/var/run/utmp || true; "
    "sed -i -e '/^HWADDR=/d' -e '/^MACADDR=/d' "
    "/etc/sysconfig/network-scripts/ifcfg-* 2>/dev/null || true"
)

_SHARED_COMMANDS = (
    "mkdir -p /etc/cloud/cloud.cfg.d",
    r'printf "%s\n" "growpart:" "  mode: auto" "  devices: [\"/\"]" "resize_rootfs: true" > /etc/cloud/cloud.cfg.d/99-grow.cfg',
    "mkdir -p /etc/systemd/system/getty.target.wants /etc/systemd/system/multi-user.target.wants",
    "ln -sfn /usr/lib/systemd/system/serial-getty@.service /etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service || ln -sfn /lib/systemd/system/serial-getty@.service /etc/systemd/system/getty.target.wants/serial-getty@ttyS0.service",
    "ln -sfn /usr/lib/systemd/system/qemu-guest-agent.service /etc/systemd/system/multi-user.target.wants/qemu-guest-agent.service || ln -sfn /lib/systemd/system/qemu-guest-agent.service /etc/systemd/system/multi-user.target.wants/qemu-guest-agent.service",
    "ln -sfn /usr/lib/systemd/system/chrony.service /etc/systemd/system/multi-user.target.wants/chrony.service || ln -sfn /lib/systemd/system/chrony.service /etc/systemd/system/multi-user.target.wants/chrony.service || ln -sfn /usr/lib/systemd/system/chronyd.service /etc/systemd/system/multi-user.target.wants/chronyd.service || ln -sfn /lib/systemd/system/chronyd.service /etc/systemd/system/multi-user.target.wants/chronyd.service",
    "cloud-init clean --logs --seed --machine-id || true",
    "rm -f /var/lib/dbus/machine-id /var/lib/cloud/instance",
    "rm -rf /var/lib/cloud/instances",
    "truncate -s 0 /etc/machine-id",
    "rm -f /etc/ssh/ssh_host_*",
    _CLEAN_IDENTITY,
)

# First match wins, so this has to sort before 50-cloud-init and 60-cloudimg.
# chcon is a no-op on Debian and Ubuntu. On EL it copies sshd_config's label.
_ROOT_SSH_COMMAND = (
    "mkdir -p /etc/ssh/sshd_config.d && "
    r"printf '%s\n' 'PasswordAuthentication yes' 'PermitRootLogin yes' "
    "> /etc/ssh/sshd_config.d/00-enableroot.conf && "
    "chmod 644 /etc/ssh/sshd_config.d/00-enableroot.conf && "
    "(chcon --reference=/etc/ssh/sshd_config "
    "/etc/ssh/sshd_config.d/00-enableroot.conf 2>/dev/null "
    "|| restorecon -F /etc/ssh/sshd_config.d/00-enableroot.conf 2>/dev/null "
    "|| true)"
)


def argv(image: str, family: str, *, permit_root: bool = False) -> list[str]:
    """Full argv starting with 'virt-customize', '-a', image, then --install and --run-command pairs.

    family is 'deb' or 'el'. ValueError on anything else.
    permit_root writes /etc/ssh/sshd_config.d/00-enableroot.conf.
    """
    packages, commands = _plan(family)
    if permit_root:
        commands = commands + (_ROOT_SSH_COMMAND,)
    command = ["virt-customize", "-a", image, "--install", packages]
    for guest_cmd in commands:
        command.extend(("--run-command", guest_cmd))
    return command


def apply(
    image: str,
    family: str,
    *,
    permit_root: bool = False,
    dry_run: bool = False,
    run=None,
) -> None:
    """Build argv, set env LIBGUESTFS_BACKEND from the current environment or 'direct' if unset.

    dry_run: print the argv as one line (shlex.join) and return without run.
    run(argv, env) -> object with returncode. Default: subprocess.run(..., check=False).
    Nonzero raises RuntimeError including the return code. Do not use shell=True.
    """
    command = argv(image, family, permit_root=permit_root)
    # Blank lines keep the green virt-customize block off the status lines.
    print()
    try:
        if dry_run:
            print(shlex.join(command))
            return
        env = os.environ.copy()
        env.setdefault("LIBGUESTFS_BACKEND", "direct")
        runner = _run_subprocess if run is None else run
        result = runner(command, env)
        if result.returncode != 0:
            raise RuntimeError(f"virt-customize failed with return code {result.returncode}")
    finally:
        print()


def _plan(family: str) -> tuple[str, tuple[str, ...]]:
    if family == "deb":
        return _DEB_PACKAGES, _DEB_COMMANDS + _SHARED_COMMANDS
    if family == "el":
        return _EL_PACKAGES, _EL_COMMANDS + _SHARED_COMMANDS
    raise ValueError(f"unknown family: {family!r}")


def _run_subprocess(command: list[str], env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, env=env, check=False)
