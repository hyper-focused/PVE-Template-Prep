"""Download, verify, convert, and publish a cloud image. Stdlib only."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import subprocess
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from .catalog import ImageSpec, finalize_index

_TIMEOUT = 120
_CHUNK = 1024 * 1024
_HEADERS = {"User-Agent": "pve-prep/1.0"}
_ALG_LEN = {"256": 64, "512": 128}
_BSD = re.compile(
    r"^SHA(?:256|512)\s+\((?P<name>[^)]+)\)\s*=\s*(?P<hex>[0-9A-Fa-f]+)\s*$",
    re.IGNORECASE,
)
_CORE = re.compile(r"^(?P<hex>[0-9A-Fa-f]+)\s+\*?(?P<name>\S.*?)\s*$")
_BARE = re.compile(r"[0-9A-Fa-f]+")
_COLLISIONS = {"backup", "overwrite", "skip"}


class DownloadError(Exception):
    """Fetch, checksum, convert, or publish failed."""

    status: int | None = None

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _basename(name: str) -> str:
    text = name.strip()
    if text.startswith("*"):
        text = text[1:]
    return PurePosixPath(text).name


def _prefer(digests: list[str], alg: str) -> str | None:
    if not digests:
        return None
    want = _ALG_LEN.get(alg)
    if want is not None:
        sized = [item for item in digests if len(item) == want]
        if sized:
            return sized[-1]
    return digests[-1]


def _mentioned(text: str, filename: str) -> bool:
    return (
        re.search(rf"(?<![\w.+-])\*?{re.escape(filename)}(?![\w.+-])", text)
        is not None
    )


def _body_lines(text: str):
    in_signature = False
    for raw in text.splitlines():
        line = raw.strip()
        upper = line.upper()
        if upper.startswith("-----BEGIN PGP SIGNATURE"):
            in_signature = True
            continue
        if upper.startswith("-----END PGP SIGNATURE"):
            in_signature = False
            continue
        if in_signature or not line or line.startswith(("#", "-----")):
            continue
        if upper.startswith("HASH:"):
            continue
        yield line


def _parse_named(line: str) -> tuple[str, str] | None:
    matched = _BSD.match(line) or _CORE.match(line)
    if matched is None:
        return None
    return matched.group("hex"), _basename(matched.group("name"))


def parse_checksum_file(text: str, filename: str, alg: str) -> str | None:
    """Return the lowercase digest for filename, or None."""
    base = _basename(filename)
    named: list[str] = []
    bare: list[str] = []
    other_names = False
    for line in _body_lines(text):
        parsed = _parse_named(line)
        if parsed is None:
            if _BARE.fullmatch(line):
                bare.append(line.lower())
            continue
        digest, found = parsed
        if found == base:
            named.append(digest.lower())
        else:
            other_names = True
    chosen = _prefer(named, alg)
    if chosen:
        return chosen
    # A file that is only a digest has no foreign filenames, so it counts.
    stripped = text.strip()
    if _BARE.fullmatch(stripped):
        return _prefer([stripped.lower()], alg)
    if bare and (_mentioned(text, base) or not other_names):
        return _prefer(bare, alg)
    return None


def file_digest(path: Path, alg: str) -> str:
    """Streaming lowercase hex digest. alg is '256' or '512'."""
    if alg == "256":
        hasher = hashlib.sha256()
    elif alg == "512":
        hasher = hashlib.sha512()
    else:
        raise DownloadError(f"unsupported checksum alg: {alg}")
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _open(url: str):
    request = urllib.request.Request(url, headers=_HEADERS)
    try:
        return urllib.request.urlopen(request, timeout=_TIMEOUT)
    except urllib.error.HTTPError as exc:
        raise DownloadError(f"HTTP {exc.code} for {url}", status=exc.code) from exc
    except urllib.error.URLError as exc:
        raise DownloadError(f"fetch failed for {url}: {exc.reason}") from exc


def _read_text(url: str) -> str:
    with _open(url) as response:
        return response.read().decode("utf-8", "replace")


def _stream(url: str, dest: Path) -> None:
    with _open(url) as response, dest.open("wb") as handle:
        while True:
            chunk = response.read(_CHUNK)
            if not chunk:
                break
            handle.write(chunk)
        handle.flush()
        os.fsync(handle.fileno())


def _prepare_cache(cache_dir: Path) -> None:
    if cache_dir.is_symlink():
        raise DownloadError(f"cache dir is a symlink: {cache_dir}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    if cache_dir.is_symlink():
        raise DownloadError(f"cache dir is a symlink: {cache_dir}")
    os.chmod(cache_dir, 0o700)


def _refuse_symlink(path: Path) -> None:
    if path.is_symlink():
        raise DownloadError(f"refusing symlink: {path}")


def _checksum_for(text: str, filename: str, alg: str, url: str) -> str:
    published = parse_checksum_file(text, filename, alg)
    if not published:
        raise DownloadError(f"no checksum for {filename} in {url}")
    return published


def _dry_name(spec: ImageSpec) -> str:
    if spec.filename:
        return spec.filename
    if spec.distro == "cloudlinux":
        return f"cloudlinux-{spec.release}-openstack.qcow2"
    return f"Fedora-Cloud-Base-Generic-{spec.release}.qcow2"


def _expected_digest(spec: ImageSpec) -> tuple[str | None, str]:
    """Return the checksum-file text, when there is one, and the expected digest.

    CloudLinux puts the SHA256 on the catalog row. Other images publish a SUM file.
    An embedded digest is not reused for an alternate filename.
    """
    embedded = spec.checksum.strip().lower()
    if embedded:
        want = _ALG_LEN.get(spec.checksum_alg)
        if want is None or re.fullmatch(rf"[0-9a-f]{{{want}}}", embedded) is None:
            raise DownloadError(f"bad embedded checksum for {spec.filename or spec.url}")
        return None, embedded
    if not spec.checksum_url:
        raise DownloadError(f"no checksum URL for {spec.url}")
    text = _read_text(spec.checksum_url)
    digest = _checksum_for(text, spec.filename, spec.checksum_alg, spec.checksum_url)
    return text, digest


def fetch_verified(spec: ImageSpec, cache_dir: Path, *, dry_run: bool = False) -> Path:
    """Download spec into cache_dir and verify it. Reuse a matching cache hit."""
    cache_dir = Path(cache_dir)
    if dry_run:
        dest = cache_dir / _dry_name(spec)
        if not spec.filename and spec.distro == "cloudlinux":
            print(
                f"resolve {spec.url} -> cloudlinux {spec.release} "
                "minimal openstack qcow2"
            )
            print(f"download <resolved url> -> {dest}")
        else:
            print(f"download {spec.url} -> {dest}")
        return dest

    _prepare_cache(cache_dir)
    if not spec.filename:
        spec = finalize_index(spec, _read_text(spec.url))
        print(f"resolved {spec.distro} {spec.release} -> {spec.url}")
    filename = spec.filename
    source = spec.url
    checksum_text, published = _expected_digest(spec)
    final = cache_dir / filename
    _refuse_symlink(final)
    if final.is_file() and file_digest(final, spec.checksum_alg) == published:
        print(f"reuse {final}")
        return final

    partial = final.with_name(final.name + ".partial")
    _refuse_symlink(partial)
    try:
        try:
            _stream(source, partial)
        except DownloadError as exc:
            if exc.status != 404 or not spec.alt_url:
                raise
            if checksum_text is None:
                raise DownloadError(
                    f"no checksum file for alternate {spec.alt_url}"
                ) from exc
            filename = PurePosixPath(spec.alt_url).name
            published = _checksum_for(
                checksum_text,
                filename,
                spec.checksum_alg,
                spec.checksum_url,
            )
            partial.unlink(missing_ok=True)
            final = cache_dir / filename
            partial = final.with_name(final.name + ".partial")
            _refuse_symlink(final)
            _refuse_symlink(partial)
            _stream(spec.alt_url, partial)
        got = file_digest(partial, spec.checksum_alg)
        if got != published:
            raise DownloadError(
                f"checksum mismatch for {filename}: got {got} expected {published}"
            )
        os.replace(partial, final)
    except Exception:
        partial.unlink(missing_ok=True)
        raise
    return final


def convert(
    src: Path,
    dest: Path,
    disk_format: str,
    *,
    dry_run: bool = False,
    run=None,
) -> None:
    """qemu-img convert -O raw|qcow2. dry_run prints argv and does not call run."""
    if disk_format not in {"raw", "qcow2"}:
        raise DownloadError(f"unsupported disk format: {disk_format}")
    src = Path(src)
    dest = Path(dest)
    argv = ["qemu-img", "convert", "-O", disk_format, str(src), str(dest)]
    if dry_run:
        print(" ".join(argv))
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(argv, check=False) if run is None else run(argv)
    code = getattr(proc, "returncode", 1)
    if code != 0:
        raise DownloadError(f"qemu-img convert failed ({code})")


def _backup_path(dest: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = Path(f"{dest}.bak.{stamp}")
    counter = 1
    while candidate.exists():
        candidate = Path(f"{dest}.bak.{stamp}.{counter}")
        counter += 1
    return candidate


def _move_file(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(src, dest)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise DownloadError(f"publish failed: {exc}") from exc
    temporary = dest.parent / f".{dest.name}.{os.getpid()}.partial"
    try:
        with src.open("rb") as inp, temporary.open("wb") as out:
            while True:
                chunk = inp.read(_CHUNK)
                if not chunk:
                    break
                out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, dest)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    src.unlink()


def publish(src: Path, dest: Path, collision: str, *, dry_run: bool = False) -> str:
    """Place src at dest. collision is backup, overwrite, or skip."""
    if collision not in _COLLISIONS:
        raise DownloadError(f"unknown collision policy: {collision}")
    src = Path(src)
    dest = Path(dest)
    if collision == "skip" and dest.exists():
        print(f"skip {dest}")
        return "skipped"
    if dry_run:
        if collision == "backup" and dest.exists():
            print(f"backup {dest} -> {dest}.bak.<UTC>")
        print(f"move {src} -> {dest}")
        return "written"

    backup = None
    if collision == "backup" and dest.exists():
        backup = _backup_path(dest)
        os.replace(dest, backup)
        print(f"backup {dest} -> {backup}")
    try:
        _move_file(src, dest)
    except Exception:
        if backup is not None and not dest.exists():
            try:
                os.replace(backup, dest)
            except OSError:
                pass
        raise
    return "written"
