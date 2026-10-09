"""Catalog table, Fedora index selection, CloudLinux catalog selection. No network."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from pve_prep.catalog import (
    DISTROS,
    CatalogError,
    compile_distro,
    finalize_cloudlinux,
    finalize_fedora,
    normalize_release,
    releases_for,
)
from pve_prep.prompts import DISTROS as PROMPT_DISTROS

_FIXTURE = Path(__file__).parent / "fixtures" / "fedora-index.html"
_FORBIDDEN = ("serverforge", "centos", "stream")


class CatalogTests(unittest.TestCase):
    def test_ga_release_counts(self) -> None:
        self.assertEqual(
            DISTROS, ("debian", "ubuntu", "alma", "cloudlinux", "fedora")
        )
        self.assertEqual(PROMPT_DISTROS, DISTROS)
        self.assertNotIn("rocky", DISTROS)
        for distro in DISTROS:
            specs = releases_for(distro)
            expected = 2 if distro in {"debian", "fedora"} else 3
            self.assertEqual(len(specs), expected)
            self.assertEqual(len({spec.release for spec in specs}), expected)
            for spec in specs:
                self.assertEqual(spec.distro, distro)
                self.assertEqual(spec.checksum, "")

    def test_urls_stay_on_official_mirrors(self) -> None:
        for distro in DISTROS:
            for spec in releases_for(distro):
                fields = (spec.url, spec.alt_url or "", spec.checksum_url)
                blob = " ".join(fields).lower()
                for banned in _FORBIDDEN:
                    self.assertNotIn(banned, blob, spec.url)
                self.assertNotIn("/daily/", spec.url)
                self.assertNotIn("rockylinux", blob)
                self.assertNotIn("download.rockylinux.org", blob)

    def test_eol_flags_and_known_urls(self) -> None:
        debian = {spec.release: spec for spec in releases_for("debian")}
        self.assertEqual(set(debian), {"12", "13"})
        self.assertFalse(debian["12"].eol)
        self.assertFalse(debian["13"].eol)
        self.assertEqual(
            debian["12"].url,
            "https://cloud.debian.org/images/cloud/bookworm/latest/"
            "debian-12-genericcloud-amd64.qcow2",
        )
        self.assertTrue(debian["12"].checksum_url.endswith("/SHA512SUMS"))
        self.assertEqual(debian["12"].checksum_alg, "512")
        self.assertEqual(debian["12"].family, "deb")

        ubuntu = {spec.release: spec for spec in releases_for("ubuntu")}
        self.assertEqual(
            ubuntu["22.04"].url,
            "https://cloud-images.ubuntu.com/releases/jammy/release/"
            "ubuntu-22.04-server-cloudimg-amd64.img",
        )
        self.assertIn("/releases/resolute/release/", ubuntu["26.04"].url)
        self.assertFalse(any(spec.eol for spec in ubuntu.values()))

        alma = {spec.release: spec for spec in releases_for("alma")}
        self.assertIsNone(alma["9"].alt_url)
        self.assertIn("repo.almalinux.org/almalinux/10/", alma["10"].url)
        self.assertTrue(alma["8"].checksum_url.endswith("/CHECKSUM"))

        cloudlinux = {spec.release: spec for spec in releases_for("cloudlinux")}
        self.assertEqual(set(cloudlinux), {"8", "9", "10"})
        for spec in cloudlinux.values():
            self.assertEqual(spec.url, "https://images.cloudlinux.com/catalog.json")
            self.assertEqual(spec.filename, "")
            self.assertEqual(spec.checksum_url, "")
            self.assertIsNone(spec.alt_url)
            self.assertFalse(spec.eol)
            self.assertEqual(spec.family, "el")
            self.assertEqual(spec.label, f"CloudLinux {spec.release}")

        fedora = {spec.release: spec for spec in releases_for("fedora")}
        self.assertEqual(set(fedora), {"43", "44"})
        self.assertFalse(fedora["43"].eol)
        self.assertEqual(fedora["43"].label, "Fedora 43")
        self.assertFalse(fedora["44"].eol)
        self.assertEqual(fedora["44"].filename, "")
        self.assertEqual(fedora["44"].checksum_url, "")
        self.assertTrue(fedora["44"].url.endswith("/"))
        self.assertIsNone(fedora["44"].alt_url)

    def test_normalize_aliases(self) -> None:
        cases = (
            ("debian", "bookworm", "12"),
            ("Debian", "TRIXIE", "13"),
            ("ubuntu", "jammy", "22.04"),
            ("ubuntu", "22", "22.04"),
            ("ubuntu", "22.04", "22.04"),
            ("ubuntu", "Noble", "24.04"),
            ("ubuntu", "24", "24.04"),
            ("ubuntu", "resolute", "26.04"),
            ("ubuntu", "26", "26.04"),
            ("alma", " 9 ", "9"),
            ("cloudlinux", "10", "10"),
            ("CloudLinux", "8", "8"),
            ("fedora", "43", "43"),
            ("FEDORA", "44", "44"),
        )
        for distro, raw, expected in cases:
            self.assertEqual(normalize_release(distro, raw), expected)

    def test_unknown_distro_and_release(self) -> None:
        with self.assertRaises(CatalogError):
            releases_for("rocky")
        with self.assertRaises(CatalogError):
            normalize_release("rocky", "9")
        with self.assertRaises(CatalogError):
            releases_for("centos")
        with self.assertRaises(CatalogError):
            releases_for("stream")
        with self.assertRaises(CatalogError):
            normalize_release("debian", "11")
        with self.assertRaises(CatalogError):
            normalize_release("debian", "bullseye")
        with self.assertRaises(CatalogError):
            normalize_release("debian", "sid")
        with self.assertRaises(CatalogError):
            normalize_release("ubuntu", "25.04")
        with self.assertRaises(CatalogError):
            normalize_release("arch", "1")
        with self.assertRaises(CatalogError):
            normalize_release("fedora", "41")
        with self.assertRaises(CatalogError):
            normalize_release("fedora", "42")

    def test_shipped_files_are_the_release_list(self) -> None:
        directory = Path(__file__).resolve().parents[1] / "pve_prep" / "distros"
        seen = []
        for path in sorted(directory.glob("*.json")):
            payload = json.loads(path.read_text(encoding="utf-8"))
            specs = releases_for(payload["id"])
            versions = [row["version"] for row in payload["releases"]]
            self.assertEqual([spec.release for spec in specs], versions)
            self.assertEqual(specs[0].source, payload["source"]["type"])
            seen.append(payload["id"])
        self.assertEqual(set(seen), set(DISTROS))

    def test_bad_distro_file_names_the_file(self) -> None:
        payload = {
            "id": "example",
            "name": "Example",
            "family": "deb",
            "order": 1,
            "checksum_alg": "256",
            "source": {
                "type": "pattern",
                "base": "https://example.com/{release}/",
                "filename": "img-{release}.qcow2",
                "checksum": "SHA256SUMS",
            },
            "releases": [
                {"version": "1", "aliases": ["old"], "eol": False},
                {"version": "2", "aliases": ["old"], "eol": False},
            ],
        }
        with self.assertRaises(CatalogError) as caught:
            compile_distro(payload, filename="example.json")
        self.assertIn("example.json", str(caught.exception))
        self.assertIn("duplicate alias", str(caught.exception))
        payload["source"]["type"] = "mirror"
        payload["releases"] = [{"version": "1", "eol": False}]
        with self.assertRaises(CatalogError) as caught:
            compile_distro(payload, filename="example.json")
        self.assertIn("unknown source type", str(caught.exception))

    def test_finalize_fedora_picks_generic_not_uefi(self) -> None:
        spec = next(item for item in releases_for("fedora") if item.release == "44")
        html = _FIXTURE.read_text(encoding="utf-8")
        pinned = finalize_fedora(spec, html)
        self.assertEqual(
            pinned.filename,
            "Fedora-Cloud-Base-Generic-44-1.6.x86_64.qcow2",
        )
        self.assertNotIn("UEFI", pinned.filename.upper())
        self.assertNotIn("EC2", pinned.url.upper())
        self.assertNotIn("AMAZON", pinned.url.upper())
        self.assertEqual(pinned.url, spec.url + pinned.filename)
        self.assertTrue(
            pinned.checksum_url.endswith("Fedora-Cloud-44-1.6-x86_64-CHECKSUM")
        )
        self.assertFalse(pinned.checksum_url.endswith("/CHECKSUM"))
        self.assertEqual(pinned.checksum_alg, "256")
        self.assertEqual(pinned.release, "44")

    def test_finalize_fedora_missing_image(self) -> None:
        spec = next(item for item in releases_for("fedora") if item.release == "43")
        with self.assertRaises(CatalogError) as caught:
            finalize_fedora(spec, "<html><a href='nope.txt'>nope.txt</a></html>")
        self.assertIn(spec.url, str(caught.exception))


_CL_SHA = "476e521c7072f38674600853470592a1a3b53dce776d52e0e5b3a0b550b90269"
_CL_HREF = (
    "/images/openstack/cloudlinux/nopanel/9-amd64/"
    "cloudlinux-9.8-x86_64-openstack-20261007.qcow2"
)


def _cl_row(brand: str, href: str, checksum: str = _CL_SHA, kind: str = "download") -> dict:
    return {
        "brand": brand,
        "checksum": checksum,
        "action": {"kind": kind, "href": href},
    }


def _cl_catalog(extra_versions: list | None = None, extra_rows: list | None = None) -> str:
    rows = [
        _cl_row(
            "aws",
            "https://aws.amazon.com/marketplace/pp/prodview-avrphempmgonu",
            checksum="",
            kind="marketplace",
        ),
        _cl_row(
            "google",
            "/images/google/cloudlinux/nopanel/9-amd64/"
            "cloudlinux-9.8-x86_64-google-20261007.tar.gz",
        ),
        _cl_row(
            "digitalocean",
            "/images/digitalocean/cloudlinux/nopanel/9-amd64/"
            "cloudlinux-9.8-x86_64-digitalocean-20240710.qcow2",
        ),
        _cl_row(
            "openstack",
            "/images/openstack/cloudlinux/cpanel/9-amd64/"
            "cloudlinux-9.8-x86_64-cpanel-openstack-20261007.qcow2",
            checksum="cdb9e90d31a86bac339553070016a1df0b5423942592a37d257c5816a29d302a",
        ),
        _cl_row("openstack", _CL_HREF),
        _cl_row(
            "vmware",
            "/images/vmware/cloudlinux/nopanel/9-amd64/"
            "cloudlinux-9.8-x86_64-vmware-20261007.ova",
        ),
    ]
    if extra_rows:
        rows.extend(extra_rows)
    versions = [
        {
            "systemName": "CloudLinux",
            "major": 7,
            "panels": [
                {
                    "name": "nopanel",
                    "rows": [
                        _cl_row(
                            "openstack",
                            "/images/openstack/cloudlinux/nopanel/7-amd64/"
                            "cloudlinux-7.9-x86_64-openstack-20200101.qcow2",
                        )
                    ],
                }
            ],
        },
        {
            "systemName": "CloudLinux",
            "major": 9,
            "panels": [
                {"name": "cPanel", "rows": [rows[3]]},
                {"name": "nopanel", "rows": rows},
                {
                    "name": "Plesk",
                    "rows": [
                        _cl_row(
                            "openstack",
                            "/images/openstack/cloudlinux/plesk/9-amd64/"
                            "cloudlinux-9.8-x86_64-plesk-openstack-20261007.qcow2",
                        )
                    ],
                },
            ],
        },
        {
            "systemName": "CloudLinux",
            "major": 10,
            "panels": [
                {
                    "name": "nopanel",
                    "rows": [
                        _cl_row(
                            "openstack",
                            "/images/openstack/cloudlinux/nopanel/10-amd64/"
                            "cloudlinux-10.2-x86_64-openstack-20261007.qcow2",
                            checksum="2886c04caa95fd28f64bc6107bdf9d957a12081756cd580f3eeb402b0d2a9fe9",
                        )
                    ],
                }
            ],
        },
    ]
    if extra_versions:
        versions.extend(extra_versions)
    return json.dumps({"productVersions": versions})


class CloudLinuxCatalogTests(unittest.TestCase):
    def test_picks_minimal_openstack_qcow2_for_the_major(self) -> None:
        spec = next(item for item in releases_for("cloudlinux") if item.release == "9")
        pinned = finalize_cloudlinux(spec, _cl_catalog())
        self.assertEqual(
            pinned.filename, "cloudlinux-9.8-x86_64-openstack-20261007.qcow2"
        )
        self.assertEqual(
            pinned.url, "https://images.cloudlinux.com" + _CL_HREF
        )
        self.assertEqual(pinned.checksum, _CL_SHA)
        self.assertEqual(pinned.checksum_url, "")
        self.assertEqual(pinned.checksum_alg, "256")
        self.assertEqual(pinned.family, "el")
        self.assertNotIn("cpanel", pinned.url)
        self.assertNotIn("plesk", pinned.url)
        self.assertNotIn("vmware", pinned.url)
        self.assertNotIn("digitalocean", pinned.url)

    def test_rejects_panels_other_hosts_and_ambiguous_rows(self) -> None:
        spec = next(item for item in releases_for("cloudlinux") if item.release == "9")
        with self.assertRaises(CatalogError):
            finalize_cloudlinux(spec, "{")
        with self.assertRaises(CatalogError):
            finalize_cloudlinux(spec, json.dumps({"productVersions": []}))
        off_host = _cl_row(
            "openstack",
            "https://example.com/images/openstack/cloudlinux/nopanel/9-amd64/"
            "cloudlinux-9.8-x86_64-openstack-20261007.qcow2",
        )
        pinned = finalize_cloudlinux(spec, _cl_catalog(extra_rows=[off_host]))
        self.assertEqual(pinned.url, "https://images.cloudlinux.com" + _CL_HREF)
        only_off = json.dumps(
            {
                "productVersions": [
                    {
                        "systemName": "CloudLinux",
                        "major": 9,
                        "panels": [{"name": "nopanel", "rows": [off_host]}],
                    }
                ]
            }
        )
        with self.assertRaises(CatalogError) as caught:
            finalize_cloudlinux(spec, only_off)
        self.assertIn("found 0", str(caught.exception))
        second = _cl_row(
            "openstack",
            "/images/openstack/cloudlinux/nopanel/9-amd64/"
            "cloudlinux-9.8-x86_64-openstack-20261008.qcow2",
            checksum="ab" * 32,
        )
        with self.assertRaises(CatalogError) as caught:
            finalize_cloudlinux(spec, _cl_catalog(extra_rows=[second]))
        self.assertIn("found 2", str(caught.exception))
        short = _cl_row("openstack", _CL_HREF, checksum="abcd")
        only_short = json.dumps(
            {
                "productVersions": [
                    {
                        "systemName": "CloudLinux",
                        "major": 9,
                        "panels": [{"name": "nopanel", "rows": [short]}],
                    }
                ]
            }
        )
        with self.assertRaises(CatalogError) as caught:
            finalize_cloudlinux(spec, only_short)
        self.assertIn("found 0", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
