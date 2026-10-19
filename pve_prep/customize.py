"""virt-customize guest prep.

The base command text matches prep-template.sh. Root password SSH is an
extra command, written only when the interview asked for it.
"""

import math
import os
import re
import shlex
import subprocess
import sys
import threading
import time

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


# virt-customize prints "[  15.6] ..." as elapsed seconds, not a fraction
# done. The slow parts are the appliance boot and the package install, and
# neither reports a real percentage. This creeps toward the next milestone
# and never claims the milestone until that line actually arrives.
_TAU = 12.0
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

# floor, cap, label. Caps are where the next real line takes over.
_MILESTONES = {
    "examine": (0.0, 30.0, "Examining the guest"),
    "seed": (30.0, 34.0, "Preparing the guest"),
    "machine": (34.0, 38.0, "Preparing the guest"),
    "install": (38.0, 88.0, "Installing packages"),
    "selinux": (98.0, 99.0, "SELinux relabel"),
    "finish": (100.0, 100.0, "Finishing"),
}
# Run-commands share the slice after package install. They are fast.
_COMMAND_START = 88.0
_COMMAND_END = 97.0


def _stdout_is_tty() -> bool:
    try:
        return bool(sys.stdout.isatty())
    except Exception:
        return False


def _phase_of(line: str) -> str | None:
    """Map one virt-customize status line to a phase. The shell text is dropped."""
    text = _ANSI.sub("", line).replace("\r", " ").strip()
    marker = "] "
    if marker in text[:32]:
        text = text.split(marker, 1)[1].strip()
    if text.startswith("Examining"):
        return "examine"
    if text.startswith("Setting a random seed"):
        return "seed"
    if text.startswith("Setting the machine ID"):
        return "machine"
    if text.startswith("Installing packages"):
        return "install"
    if text.startswith("Running:"):
        return "command"
    if text.startswith("SELinux"):
        return "selinux"
    if text.startswith("Finishing"):
        return "finish"
    return None


class _PrepMeter:
    """One prep bar. Percent moves on a real phase, then creeps until the next."""

    def __init__(self, commands: int) -> None:
        self.commands = max(commands, 1)
        self.seen = 0
        self.kind = "examine"
        self.floor = 0.0
        self.cap = 30.0
        self.phase = "Examining the guest"
        self.phase_started = 0.0

    def begin(self, now: float) -> None:
        self.phase_started = now

    def note(self, kind: str | None, now: float) -> None:
        if kind is None:
            return
        if kind == "command":
            self.kind = "command"
            self.seen += 1
            span = (_COMMAND_END - _COMMAND_START) / self.commands
            self.floor = _COMMAND_START + span * (self.seen - 1)
            self.cap = min(_COMMAND_END, _COMMAND_START + span * self.seen)
            self.phase = "Configuring the guest"
            self.phase_started = now
            return
        if kind == self.kind or kind not in _MILESTONES:
            return
        self.kind = kind
        self.floor, self.cap, self.phase = _MILESTONES[kind]
        self.phase_started = now

    def snapshot(self, now: float) -> tuple[float, str]:
        if self.floor >= 100.0:
            return 100.0, self.phase
        if self.cap <= self.floor:
            return self.floor, self.phase
        elapsed = max(0.0, now - self.phase_started)
        crept = self.floor + (self.cap - self.floor) * (1.0 - math.exp(-elapsed / _TAU))
        return min(self.cap - 0.01, crept), self.phase


def _format_bar(percent: float, phase: str) -> str:
    """Same shape as qemu-img -p, plus the phase. The shell command is not here."""
    return f"    ({percent:6.2f}/100%)  {phase}"


def _show_bar(*, verbose: bool, progress: bool | None) -> bool:
    if verbose:
        return False
    if progress is None:
        return _stdout_is_tty()
    return progress


def _run_with_bar(command: list[str], env: dict[str, str], commands: int) -> tuple[int, str]:
    """Run virt-customize. One updating line. Captured log is returned, not printed."""
    proc = subprocess.Popen(
        command,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    captured: list[str] = []
    meter = _PrepMeter(commands)
    meter.begin(time.monotonic())
    lock = threading.Lock()
    done = threading.Event()

    def _read() -> None:
        try:
            stream = proc.stdout
            if stream is None:
                return
            for line in stream:
                captured.append(line)
                kind = _phase_of(line)
                with lock:
                    meter.note(kind, time.monotonic())
        finally:
            done.set()

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    last = ""
    try:
        while True:
            with lock:
                percent, phase = meter.snapshot(time.monotonic())
            rendered = _format_bar(percent, phase)
            if rendered != last:
                sys.stdout.write("\r\033[K" + rendered)
                sys.stdout.flush()
                last = rendered
            if proc.poll() is not None and done.is_set():
                break
            time.sleep(0.15)
    except KeyboardInterrupt:
        proc.terminate()
        raise
    reader.join(timeout=2)
    code = proc.returncode
    if code is None:
        code = proc.wait()
    if code == 0:
        sys.stdout.write("\r\033[K" + _format_bar(100.0, "Finishing") + "\n")
    else:
        sys.stdout.write("\n")
    sys.stdout.flush()
    return code, "".join(captured)


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
    verbose: bool = False,
    label: str = "",
    progress: bool | None = None,
) -> None:
    """Build argv, set env LIBGUESTFS_BACKEND from the current environment or 'direct' if unset.

    dry_run: print the argv as one line (shlex.join) and return without run.
    On a terminal, and unless verbose, one progress line replaces the command log.
    verbose or a pipe lets virt-customize print its own lines.
    run(argv, env) -> object with returncode. Default: subprocess.run(..., check=False).
    The progress bar does not use run. Nonzero raises RuntimeError including the
    return code, and the captured log is printed first. Do not use shell=True.
    """
    command = argv(image, family, permit_root=permit_root)
    # Blank lines keep the prep block off the status lines.
    print()
    try:
        if dry_run:
            print(shlex.join(command))
            return
        env = os.environ.copy()
        env.setdefault("LIBGUESTFS_BACKEND", "direct")
        if _show_bar(verbose=verbose, progress=progress):
            if label:
                print(f"Preparing {label} image for Proxmox PVE...")
            commands = sum(1 for part in command if part == "--run-command")
            code, log = _run_with_bar(command, env, commands)
            if code != 0:
                if log.strip():
                    sys.stdout.write(log if log.endswith("\n") else log + "\n")
                    sys.stdout.flush()
                raise RuntimeError(f"virt-customize failed with return code {code}")
            return
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
