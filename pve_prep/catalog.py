"""Cloud-image catalog. One table, no network."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from html import unescape
from urllib.parse import unquote, urlsplit

class CatalogError(Exception):
    """Unknown distro/release, or an index with no usable image."""


@dataclass(frozen=True)
class ImageSpec:
    distro: str
    release: str
    label: str
    family: str
    filename: str
    url: str
    alt_url: str | None
    checksum_url: str
    checksum_alg: str
    eol: bool
    checksum: str = ""


DISTROS = ("debian", "ubuntu", "alma", "cloudlinux", "fedora")

_DEBIAN = (
    ("11", "bullseye", True),
    ("12", "bookworm", False),
    ("13", "trixie", False),
)
_UBUNTU = (
    ("22.04", "jammy"),
    ("24.04", "noble"),
    ("26.04", "resolute"),
)
_EL = ("8", "9", "10")
_FEDORA = (("42", True), ("43", False), ("44", False))

_ALIASES: dict[str, dict[str, str]] = {
    "debian": {
        "11": "11",
        "bullseye": "11",
        "12": "12",
        "bookworm": "12",
        "13": "13",
        "trixie": "13",
    },
    "ubuntu": {
        "22.04": "22.04",
        "22": "22.04",
        "jammy": "22.04",
        "24.04": "24.04",
        "24": "24.04",
        "noble": "24.04",
        "26.04": "26.04",
        "26": "26.04",
        "resolute": "26.04",
    },
    "alma": {ver: ver for ver in _EL},
    "cloudlinux": {ver: ver for ver in _EL},
    "fedora": {ver: ver for ver in ("42", "43", "44")},
}

_CL_ORIGIN = "https://images.cloudlinux.com"
_CL_CATALOG = _CL_ORIGIN + "/catalog.json"
_CL_NAME = re.compile(
    r"cloudlinux-[0-9][0-9.]*-x86_64-openstack-[0-9]{8}\.qcow2\Z"
)

_REJECT = ("UEFI", "EC2", "AMAZON", "AZURE", "GCE", "VAGRANT")
_HREF = re.compile(r"""href\s*=\s*(['"])([^'"]+)\1""", re.IGNORECASE)
_INDEX_TOKEN = re.compile(
    r"""href\s*=\s*(['"])([^'"]+)\1|>([^<]*Fedora-Cloud[^<]*)""",
    re.IGNORECASE,
)


def fedora_index_url(release: str) -> str:
    return (
        "https://download.fedoraproject.org/pub/fedora/linux/releases/"
        f"{release}/Cloud/x86_64/images/"
    )


def _label(name: str, release: str, eol: bool) -> str:
    suffix = " (EOL)" if eol else ""
    return f"{name} {release}{suffix}"


def _debian_specs() -> tuple[ImageSpec, ...]:
    specs = []
    for release, codename, eol in _DEBIAN:
        base = f"https://cloud.debian.org/images/cloud/{codename}/latest/"
        filename = f"debian-{release}-genericcloud-amd64.qcow2"
        specs.append(
            ImageSpec(
                distro="debian",
                release=release,
                label=_label("Debian", release, eol),
                family="deb",
                filename=filename,
                url=base + filename,
                alt_url=None,
                checksum_url=base + "SHA512SUMS",
                checksum_alg="512",
                eol=eol,
            )
        )
    return tuple(specs)


def _ubuntu_specs() -> tuple[ImageSpec, ...]:
    specs = []
    for release, codename in _UBUNTU:
        base = f"https://cloud-images.ubuntu.com/releases/{codename}/release/"
        filename = f"ubuntu-{release}-server-cloudimg-amd64.img"
        specs.append(
            ImageSpec(
                distro="ubuntu",
                release=release,
                label=_label("Ubuntu", release, False),
                family="deb",
                filename=filename,
                url=base + filename,
                alt_url=None,
                checksum_url=base + "SHA256SUMS",
                checksum_alg="256",
                eol=False,
            )
        )
    return tuple(specs)


def _alma_specs() -> tuple[ImageSpec, ...]:
    specs = []
    for release in _EL:
        base = f"https://repo.almalinux.org/almalinux/{release}/cloud/x86_64/images/"
        filename = f"AlmaLinux-{release}-GenericCloud-latest.x86_64.qcow2"
        specs.append(
            ImageSpec(
                distro="alma",
                release=release,
                label=_label("AlmaLinux", release, False),
                family="el",
                filename=filename,
                url=base + filename,
                alt_url=None,
                checksum_url=base + "CHECKSUM",
                checksum_alg="256",
                eol=False,
            )
        )
    return tuple(specs)


def _cloudlinux_specs() -> tuple[ImageSpec, ...]:
    """Majors only. The dated qcow2 is chosen from catalog.json at download."""
    specs = []
    for release in _EL:
        specs.append(
            ImageSpec(
                distro="cloudlinux",
                release=release,
                label=_label("CloudLinux", release, False),
                family="el",
                filename="",
                url=_CL_CATALOG,
                alt_url=None,
                checksum_url="",
                checksum_alg="256",
                eol=False,
            )
        )
    return tuple(specs)


def _fedora_specs() -> tuple[ImageSpec, ...]:
    specs = []
    for release, eol in _FEDORA:
        specs.append(
            ImageSpec(
                distro="fedora",
                release=release,
                label=_label("Fedora", release, eol),
                family="el",
                filename="",
                url=fedora_index_url(release),
                alt_url=None,
                checksum_url="",
                checksum_alg="256",
                eol=eol,
            )
        )
    return tuple(specs)


_CATALOG = {
    "debian": _debian_specs(),
    "ubuntu": _ubuntu_specs(),
    "alma": _alma_specs(),
    "cloudlinux": _cloudlinux_specs(),
    "fedora": _fedora_specs(),
}


def releases_for(distro: str) -> tuple[ImageSpec, ...]:
    """Three GA releases for a known distro."""
    key = distro.strip().casefold()
    try:
        return _CATALOG[key]
    except KeyError:
        raise CatalogError(f"unknown distro: {distro}") from None


def normalize_release(distro: str, raw: str) -> str:
    """Map a release alias to the canonical version string."""
    key = distro.strip().casefold()
    table = _ALIASES.get(key)
    if table is None:
        raise CatalogError(f"unknown distro: {distro}")
    token = raw.strip().casefold()
    try:
        return table[token]
    except KeyError:
        raise CatalogError(f"unknown {key} release: {raw}") from None


def _clean_name(raw: str) -> str:
    text = unescape(raw).strip()
    if not text or text.startswith(("?", "#")):
        return ""
    path = unquote(urlsplit(text).path)
    name = path.rsplit("/", 1)[-1].strip()
    if name in ("", ".", ".."):
        return ""
    return name


def _index_names(index_html: str) -> list[str]:
    names: list[str] = []
    for match in _INDEX_TOKEN.finditer(index_html):
        raw = match.group(2) if match.group(2) is not None else match.group(3)
        name = _clean_name(raw or "")
        if name:
            names.append(name)
    return names


def _is_generic_qcow2(name: str, release: str) -> bool:
    upper = name.upper()
    if any(token in upper for token in _REJECT):
        return False
    pattern = rf"Fedora-Cloud-Base-Generic-{re.escape(release)}-.+\.x86_64\.qcow2"
    return re.fullmatch(pattern, name) is not None


def _checksum_name(index_html: str, release: str) -> str:
    names: list[str] = []
    for match in _HREF.finditer(index_html):
        name = _clean_name(match.group(2))
        if name and "CHECKSUM" in name.upper():
            names.append(name)
    if not names:
        return ""
    preferred = [name for name in names if release in name]
    return (preferred or names)[-1]


def _directory(url: str) -> str:
    return url if url.endswith("/") else url + "/"


def _cloudlinux_url(href: str) -> str | None:
    if "\\" in href or href.startswith("//"):
        return None
    parts = urlsplit(href)
    if parts.scheme == "" and parts.netloc == "":
        if not href.startswith("/") or parts.query or parts.fragment:
            return None
        return _CL_ORIGIN + href
    if parts.scheme != "https" or parts.netloc.casefold() != "images.cloudlinux.com":
        return None
    if parts.query or parts.fragment:
        return None
    return href


def _cloudlinux_row(row: object, release: str) -> tuple[str, str, str] | None:
    """One minimal OpenStack qcow2, or None when the row is a different product."""
    if not isinstance(row, dict):
        return None
    if str(row.get("brand", "")).casefold() != "openstack":
        return None
    action = row.get("action")
    if not isinstance(action, dict):
        return None
    if str(action.get("kind", "")).casefold() != "download":
        return None
    href = action.get("href")
    if not isinstance(href, str):
        return None
    url = _cloudlinux_url(href)
    if url is None:
        return None
    path = unquote(urlsplit(url).path)
    raw_parts = path.split("/")
    if any(part in (".", "..") for part in raw_parts):
        return None
    parts = [part for part in raw_parts if part]
    needle = f"images/openstack/cloudlinux/nopanel/{release}-amd64"
    if len(parts) != 6 or parts[:5] != needle.split("/"):
        return None
    filename = parts[-1]
    if _CL_NAME.fullmatch(filename) is None:
        return None
    digest = row.get("checksum")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
        return None
    return filename, url, digest.lower()


def finalize_cloudlinux(spec: ImageSpec, catalog_text: str) -> ImageSpec:
    """Pin the minimal OpenStack qcow2 for this major from catalog.json text."""
    try:
        payload = json.loads(catalog_text)
    except json.JSONDecodeError as exc:
        raise CatalogError(f"cloudlinux catalog is not json: {exc}") from exc
    if not isinstance(payload, dict):
        raise CatalogError("cloudlinux catalog is not an object")
    versions = payload.get("productVersions")
    if not isinstance(versions, list):
        raise CatalogError("cloudlinux catalog has no productVersions")

    matches: list[tuple[str, str, str]] = []
    for version in versions:
        if not isinstance(version, dict):
            continue
        if str(version.get("systemName", "")).casefold() != "cloudlinux":
            continue
        if str(version.get("major", "")) != spec.release:
            continue
        panels = version.get("panels")
        if not isinstance(panels, list):
            continue
        for panel in panels:
            if not isinstance(panel, dict):
                continue
            if str(panel.get("name", "")).casefold() != "nopanel":
                continue
            rows = panel.get("rows")
            if not isinstance(rows, list):
                continue
            for row in rows:
                picked = _cloudlinux_row(row, spec.release)
                if picked is not None:
                    matches.append(picked)
    if len(matches) != 1:
        raise CatalogError(
            f"expected one CloudLinux {spec.release} minimal OpenStack qcow2, "
            f"found {len(matches)}"
        )
    filename, url, digest = matches[0]
    return replace(
        spec,
        filename=filename,
        url=url,
        checksum_url="",
        checksum=digest,
    )


def finalize_index(spec: ImageSpec, index_text: str) -> ImageSpec:
    """Resolve a catalog row whose filename is filled in at download time."""
    if spec.distro == "fedora":
        return finalize_fedora(spec, index_text)
    if spec.distro == "cloudlinux":
        return finalize_cloudlinux(spec, index_text)
    raise CatalogError(f"{spec.distro} {spec.release} has no image filename")


def finalize_fedora(spec: ImageSpec, index_html: str) -> ImageSpec:
    """Pin the Generic cloud qcow2 named by a release directory index."""
    directory = _directory(spec.url)
    matches = [
        name
        for name in _index_names(index_html)
        if _is_generic_qcow2(name, spec.release)
    ]
    if not matches:
        raise CatalogError(
            f"no Fedora-Cloud-Base-Generic-{spec.release} qcow2 in {directory}"
        )
    filename = matches[-1]
    checksum = _checksum_name(index_html, spec.release)
    if not checksum:
        raise CatalogError(f"no CHECKSUM link in {directory}")
    return replace(
        spec,
        filename=filename,
        url=directory + filename,
        checksum_url=directory + checksum,
    )
