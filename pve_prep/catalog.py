"""Cloud-image catalog. Release rows live in distros/*.json. No network."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from html import unescape
from pathlib import Path
from urllib.parse import unquote, urlsplit

class CatalogError(Exception):
    """Unknown distro/release, a bad distro file, or an index with no usable image."""


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
    source: str = "pattern"


_DISTRO_DIR = Path(__file__).resolve().parent / "distros"
_ID = re.compile(r"[a-z][a-z0-9]*\Z")
_TOKEN = re.compile(r"\{([A-Za-z0-9_]+)\}")
_FILE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*\Z")
_DISTRO_KEYS = {"id", "name", "family", "order", "checksum_alg", "source", "releases"}
_RELEASE_KEYS = {"version", "eol", "codename", "aliases"}
_SOURCE_KEYS = {
    "pattern": {"type", "base", "filename", "checksum"},
    "fedora-index": {"type", "base"},
    "cloudlinux-catalog": {"type", "url"},
}
_CL_NAME = re.compile(
    r"cloudlinux-[0-9][0-9.]*-x86_64-openstack-[0-9]{8}\.qcow2\Z"
)

_REJECT = ("UEFI", "EC2", "AMAZON", "AZURE", "GCE", "VAGRANT")
_HREF = re.compile(r"""href\s*=\s*(['"])([^'"]+)\1""", re.IGNORECASE)
_INDEX_TOKEN = re.compile(
    r"""href\s*=\s*(['"])([^'"]+)\1|>([^<]*Fedora-Cloud[^<]*)""",
    re.IGNORECASE,
)


def _bad(filename: str, message: str) -> CatalogError:
    return CatalogError(f"{filename}: {message}")


def _text(value: object, filename: str, where: str) -> str:
    if not isinstance(value, str) or value.strip() == "" or value != value.strip():
        raise _bad(filename, f"{where} must be a non-empty string")
    if any(char in value for char in "\r\n\t "):
        raise _bad(filename, f"{where} must be a single token")
    return value


def _keys(value: object, filename: str, where: str, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict):
        raise _bad(filename, f"{where} is not an object")
    found = set(value)
    missing = required - found
    extra = found - allowed
    if missing:
        raise _bad(filename, f"{where} is missing {', '.join(sorted(missing))}")
    if extra:
        raise _bad(filename, f"{where} has unknown {', '.join(sorted(extra))}")
    return value


def _https(url: str, filename: str, where: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or "\\" in url
    ):
        raise _bad(filename, f"{where} is not a plain https url")
    return url


def _fill(template: str, fields: dict[str, str | None], filename: str, where: str) -> str:
    def replace_token(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in fields:
            raise _bad(filename, f"{where} has an unknown placeholder {{{key}}}")
        value = fields[key]
        if not value:
            raise _bad(filename, f"{where} uses {{{key}}} but this release has no {key}")
        return value

    filled = _TOKEN.sub(replace_token, template)
    if "{" in filled or "}" in filled:
        raise _bad(filename, f"{where} has a broken placeholder")
    return filled


def _label(name: str, release: str, eol: bool) -> str:
    suffix = " (EOL)" if eol else ""
    return f"{name} {release}{suffix}"


def _release_row(row: object, filename: str) -> tuple[str, str, bool, tuple[str, ...]]:
    item = _keys(row, filename, "release", _RELEASE_KEYS, {"version", "eol"})
    version = _text(item["version"], filename, "release version")
    if _VERSION.fullmatch(version) is None:
        raise _bad(filename, f"release version {version!r} has illegal characters")
    eol = item["eol"]
    if not isinstance(eol, bool):
        raise _bad(filename, f"release {version} eol must be true or false")
    codename = ""
    if "codename" in item:
        codename = _text(item["codename"], filename, f"release {version} codename")
        if _VERSION.fullmatch(codename) is None:
            raise _bad(filename, f"release {version} codename has illegal characters")
    aliases: list[str] = []
    if "aliases" in item:
        raw = item["aliases"]
        if not isinstance(raw, list) or not raw:
            raise _bad(filename, f"release {version} aliases must be a non-empty list")
        for alias in raw:
            token = _text(alias, filename, f"release {version} alias")
            if _VERSION.fullmatch(token) is None:
                raise _bad(filename, f"release {version} alias {token!r} has illegal characters")
            aliases.append(token)
    return version, codename, eol, tuple(aliases)


def _pattern_spec(
    distro: str,
    name: str,
    family: str,
    alg: str,
    source: dict,
    version: str,
    codename: str,
    eol: bool,
    filename: str,
) -> ImageSpec:
    fields: dict[str, str | None] = {
        "release": version,
        "codename": codename or None,
    }
    base = _fill(_text(source["base"], filename, "source base"), fields, filename, "source base")
    if not base.endswith("/"):
        raise _bad(filename, f"source base for {version} must end with /")
    _https(base, filename, f"source base for {version}")
    image = _fill(
        _text(source["filename"], filename, "source filename"),
        fields,
        filename,
        "source filename",
    )
    if _FILE_NAME.fullmatch(image) is None:
        raise _bad(filename, f"image name for {version} is not a single file name")
    checksum = _text(source["checksum"], filename, "source checksum")
    if _FILE_NAME.fullmatch(checksum) is None:
        raise _bad(filename, "source checksum is not a single file name")
    return ImageSpec(
        distro=distro,
        release=version,
        label=_label(name, version, eol),
        family=family,
        filename=image,
        url=base + image,
        alt_url=None,
        checksum_url=base + checksum,
        checksum_alg=alg,
        eol=eol,
        source="pattern",
    )


def _index_spec(
    distro: str,
    name: str,
    family: str,
    alg: str,
    source: dict,
    version: str,
    codename: str,
    eol: bool,
    filename: str,
    source_type: str,
) -> ImageSpec:
    if source_type == "fedora-index":
        fields: dict[str, str | None] = {
            "release": version,
            "codename": codename or None,
        }
        base = _fill(
            _text(source["base"], filename, "source base"),
            fields,
            filename,
            "source base",
        )
        if not base.endswith("/"):
            raise _bad(filename, f"source base for {version} must end with /")
        url = _https(base, filename, f"source base for {version}")
    else:
        url = _https(_text(source["url"], filename, "source url"), filename, "source url")
    return ImageSpec(
        distro=distro,
        release=version,
        label=_label(name, version, eol),
        family=family,
        filename="",
        url=url,
        alt_url=None,
        checksum_url="",
        checksum_alg=alg,
        eol=eol,
        source=source_type,
    )


def compile_distro(payload: object, *, filename: str) -> tuple[tuple[ImageSpec, ...], dict[str, str]]:
    """One distro file. Specs stay in file order. The map is alias to version."""
    item = _keys(payload, filename, "distro", _DISTRO_KEYS, _DISTRO_KEYS)
    distro = _text(item["id"], filename, "id")
    if _ID.fullmatch(distro) is None:
        raise _bad(filename, "id must be a lowercase word")
    if Path(filename).name != f"{distro}.json":
        raise _bad(filename, f"file name must be {distro}.json")
    name = item["name"]
    if not isinstance(name, str) or name.strip() == "" or name != name.strip():
        raise _bad(filename, "name must be a non-empty string")
    if any(char in name for char in "\r\n\t"):
        raise _bad(filename, "name must be one line")
    family = item["family"]
    if family not in ("deb", "el"):
        raise _bad(filename, "family must be deb or el")
    order = item["order"]
    if isinstance(order, bool) or not isinstance(order, int) or order < 1:
        raise _bad(filename, "order must be a positive integer")
    alg = item["checksum_alg"]
    if alg not in ("256", "512"):
        raise _bad(filename, "checksum_alg must be 256 or 512")
    source = item["source"]
    if not isinstance(source, dict) or not isinstance(source.get("type"), str):
        raise _bad(filename, "source type is missing")
    source_type = source["type"]
    allowed = _SOURCE_KEYS.get(source_type)
    if allowed is None:
        raise _bad(filename, f"unknown source type {source_type}")
    _keys(source, filename, "source", allowed, allowed)
    rows = item["releases"]
    if not isinstance(rows, list) or not rows:
        raise _bad(filename, "releases must be a non-empty list")

    specs: list[ImageSpec] = []
    aliases: dict[str, str] = {}
    seen: set[str] = set()
    for row in rows:
        version, codename, eol, extra = _release_row(row, filename)
        if version in seen:
            raise _bad(filename, f"duplicate release {version}")
        seen.add(version)
        tokens = [version]
        if codename:
            tokens.append(codename)
        tokens.extend(extra)
        for token in tokens:
            key = token.casefold()
            if key in aliases:
                raise _bad(filename, f"duplicate alias {token}")
            aliases[key] = version
        if source_type == "pattern":
            specs.append(
                _pattern_spec(distro, name, family, alg, source, version, codename, eol, filename)
            )
        else:
            specs.append(
                _index_spec(
                    distro,
                    name,
                    family,
                    alg,
                    source,
                    version,
                    codename,
                    eol,
                    filename,
                    source_type,
                )
            )
    return tuple(specs), aliases


def load_distros(
    directory: Path,
) -> tuple[tuple[str, ...], dict[str, tuple[ImageSpec, ...]], dict[str, dict[str, str]]]:
    """Read every distro file. Menu order is the order field, not the file name."""
    if not directory.is_dir():
        raise CatalogError(f"distro config directory is missing: {directory}")
    paths = sorted(path for path in directory.glob("*.json") if path.is_file())
    if not paths:
        raise CatalogError(f"no distro config in {directory}")
    ranked: list[tuple[int, str, tuple[ImageSpec, ...], dict[str, str]]] = []
    seen_ids: set[str] = set()
    seen_order: dict[int, str] = {}
    for path in paths:
        if path.is_symlink():
            raise CatalogError(f"{path.name}: distro config is a symlink")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise CatalogError(f"{path.name}: {exc}") from exc
        specs, aliases = compile_distro(payload, filename=path.name)
        distro = specs[0].distro
        if distro in seen_ids:
            raise CatalogError(f"{path.name}: duplicate distro {distro}")
        seen_ids.add(distro)
        order = payload["order"]
        if order in seen_order:
            raise CatalogError(
                f"{path.name}: order {order} is already used by {seen_order[order]}"
            )
        seen_order[order] = path.name
        ranked.append((order, distro, specs, aliases))
    ranked.sort(key=lambda item: item[0])
    names = tuple(item[1] for item in ranked)
    catalog = {item[1]: item[2] for item in ranked}
    alias_map = {item[1]: item[3] for item in ranked}
    return names, catalog, alias_map


DISTROS, _CATALOG, _ALIASES = load_distros(_DISTRO_DIR)


def releases_for(distro: str) -> tuple[ImageSpec, ...]:
    """Releases from that distro's config file, in file order."""
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


def _catalog_origin(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}", parts.netloc.casefold()


def _cloudlinux_url(href: str, origin: str, host: str) -> str | None:
    if "\\" in href or href.startswith("//"):
        return None
    parts = urlsplit(href)
    if parts.scheme == "" and parts.netloc == "":
        if not href.startswith("/") or parts.query or parts.fragment:
            return None
        return origin + href
    if parts.scheme != "https" or parts.netloc.casefold() != host:
        return None
    if parts.query or parts.fragment:
        return None
    return href


def _cloudlinux_row(
    row: object, release: str, origin: str, host: str
) -> tuple[str, str, str] | None:
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
    url = _cloudlinux_url(href, origin, host)
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
    origin, host = _catalog_origin(spec.url)

    matches: list[tuple[str, str, str]] = []
    for version in versions:
        if not isinstance(version, dict):
            continue
        if str(version.get("systemName", "")).casefold() != spec.distro:
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
                picked = _cloudlinux_row(row, spec.release, origin, host)
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
    if spec.source == "fedora-index":
        return finalize_fedora(spec, index_text)
    if spec.source == "cloudlinux-catalog":
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
