"""Checksum parsing, publish, convert, and dry-run fetch. No network."""

from __future__ import annotations

import hashlib
import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from pve_prep.catalog import releases_for
from pve_prep.download import (
    DownloadError,
    convert,
    fetch_verified,
    file_digest,
    parse_checksum_file,
    publish,
)


def _by_release(distro: str, release: str):
    return next(spec for spec in releases_for(distro) if spec.release == release)


class ParseChecksumTests(unittest.TestCase):
    def test_three_dialects_and_pgp(self) -> None:
        image = "image.qcow2"
        sha256 = "ab" * 32
        sha512 = "cd" * 64
        other = "ef" * 32

        debian = f"{sha512.upper()}  debian-12-genericcloud-amd64.qcow2\n"
        self.assertEqual(
            parse_checksum_file(debian, "debian-12-genericcloud-amd64.qcow2", "512"),
            sha512,
        )

        ubuntu = (
            f"{sha256} *ubuntu-22.04-server-cloudimg-amd64.img\n"
            f"{other} *ubuntu-22.04-server-cloudimg-amd64.manifest\n"
        )
        self.assertEqual(
            parse_checksum_file(
                ubuntu,
                "ubuntu-22.04-server-cloudimg-amd64.img",
                "256",
            ),
            sha256,
        )

        alma = (
            "# AlmaLinux-9-GenericCloud-latest.x86_64.qcow2: 12 bytes\n"
            f"SHA256 (AlmaLinux-9-GenericCloud-latest.x86_64.qcow2) = {sha256.upper()}\n"
        )
        self.assertEqual(
            parse_checksum_file(
                alma,
                "AlmaLinux-9-GenericCloud-latest.x86_64.qcow2",
                "256",
            ),
            sha256,
        )

        fake = "ff" * 32
        pgp = (
            "-----BEGIN PGP SIGNED MESSAGE-----\n"
            "Hash: SHA256\n"
            "\n"
            f"SHA256 ({image}) = {sha256}\n"
            "-----BEGIN PGP SIGNATURE-----\n"
            "\n"
            f"SHA256 ({image}) = {fake}\n"
            "-----END PGP SIGNATURE-----\n"
        )
        self.assertEqual(parse_checksum_file(pgp, image, "256"), sha256)
        self.assertIsNone(parse_checksum_file(alma, "missing.qcow2", "256"))

    def test_alg_length_preference(self) -> None:
        name = "AlmaLinux-9-GenericCloud-latest.x86_64.qcow2"
        sha256 = "11" * 32
        sha512 = "22" * 64
        text = f"SHA256 ({name}) = {sha256}\nSHA512 ({name}) = {sha512}\n"
        self.assertEqual(parse_checksum_file(text, name, "256"), sha256)
        self.assertEqual(parse_checksum_file(text, name, "512"), sha512)

    def test_bare_digest_only_without_foreign_names(self) -> None:
        digest = "a1" * 32
        self.assertEqual(parse_checksum_file(digest + "\n", "file.qcow2", "256"), digest)
        mixed = f"{'bb' * 32}  other.qcow2\n{digest}\n"
        self.assertIsNone(parse_checksum_file(mixed, "file.qcow2", "256"))


class FileDigestTests(unittest.TestCase):
    def test_file_digest_matches_hashlib(self) -> None:
        payload = b"cloud-image"
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "disk.img"
            path.write_bytes(payload)
            self.assertEqual(file_digest(path, "256"), hashlib.sha256(payload).hexdigest())
            self.assertEqual(file_digest(path, "512"), hashlib.sha512(payload).hexdigest())
            with self.assertRaises(DownloadError):
                file_digest(path, "md5")


class PublishTests(unittest.TestCase):
    def test_backup_skip_and_overwrite(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "disk.qcow2"
            dest.write_bytes(b"old")

            src = root / "incoming.qcow2"
            src.write_bytes(b"new")
            self.assertEqual(publish(src, dest, "backup"), "written")
            self.assertEqual(dest.read_bytes(), b"new")
            self.assertFalse(src.exists())
            backups = list(root.glob("disk.qcow2.bak.*"))
            self.assertEqual(len(backups), 1)
            self.assertRegex(backups[0].name, r"^disk\.qcow2\.bak\.\d{8}T\d{6}Z$")
            self.assertEqual(backups[0].read_bytes(), b"old")

            kept = root / "kept.qcow2"
            kept.write_bytes(b"keep-me")
            src = root / "ignored.qcow2"
            src.write_bytes(b"nope")
            self.assertEqual(publish(src, kept, "skip"), "skipped")
            self.assertEqual(kept.read_bytes(), b"keep-me")
            self.assertEqual(src.read_bytes(), b"nope")

            src = root / "replace.qcow2"
            src.write_bytes(b"replaced")
            self.assertEqual(publish(src, kept, "overwrite"), "written")
            self.assertEqual(kept.read_bytes(), b"replaced")
            self.assertFalse(src.exists())
            self.assertEqual(list(root.glob("kept.qcow2.bak.*")), [])

            fresh = root / "fresh.qcow2"
            src = root / "first.qcow2"
            src.write_bytes(b"first")
            self.assertEqual(publish(src, fresh, "skip"), "written")
            self.assertEqual(fresh.read_bytes(), b"first")

    def test_unknown_collision_and_dry_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            dest = root / "disk.qcow2"
            dest.write_bytes(b"old")
            src = root / "incoming.qcow2"
            src.write_bytes(b"new")
            with self.assertRaises(DownloadError):
                publish(src, dest, "merge")
            with redirect_stdout(StringIO()):
                self.assertEqual(publish(src, dest, "skip", dry_run=True), "skipped")
            self.assertEqual(dest.read_bytes(), b"old")
            self.assertTrue(src.exists())


class ConvertTests(unittest.TestCase):
    def test_dry_run_does_not_call_run(self) -> None:
        called: list[list[str]] = []

        def run(argv: list[str]):
            called.append(argv)
            raise AssertionError("dry_run must not call run")

        with TemporaryDirectory() as tmp:
            dest = Path(tmp) / "nested" / "disk.raw"
            with redirect_stdout(StringIO()) as buffer:
                convert(Path("in.qcow2"), dest, "raw", dry_run=True, run=run)
            self.assertEqual(called, [])
            self.assertFalse(dest.parent.exists())
            self.assertIn("qemu-img convert -O raw", buffer.getvalue())

    def test_nonzero_and_bad_format(self) -> None:
        def run(argv: list[str]):
            return subprocess_result(argv, 7)

        with self.assertRaises(DownloadError):
            convert(Path("in.img"), Path("out.qcow2"), "qcow2", run=run)
        with self.assertRaises(DownloadError):
            convert(Path("in.img"), Path("out.raw"), "vmdk", dry_run=True, run=run)


class FetchDryRunTests(unittest.TestCase):
    def test_fetch_verified_dry_run_skips_network(self) -> None:
        debian = _by_release("debian", "12")
        fedora = _by_release("fedora", "44")
        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            with redirect_stdout(StringIO()) as buffer:
                dest = fetch_verified(debian, cache, dry_run=True)
            self.assertEqual(dest, cache / debian.filename)
            self.assertFalse(cache.exists())
            self.assertIn(f"download {debian.url} -> {dest}", buffer.getvalue())

            with redirect_stdout(StringIO()) as buffer:
                fed_dest = fetch_verified(fedora, cache, dry_run=True)
            self.assertEqual(fed_dest.name, "Fedora-Cloud-Base-Generic-44.qcow2")
            self.assertIn(fedora.url, buffer.getvalue())
            self.assertFalse(cache.exists())
            self.assertFalse(fed_dest.exists())

            cloudlinux = _by_release("cloudlinux", "9")
            with redirect_stdout(StringIO()) as buffer:
                cl_dest = fetch_verified(cloudlinux, cache, dry_run=True)
            text = buffer.getvalue()
            self.assertEqual(cl_dest.name, "cloudlinux-9-openstack.qcow2")
            self.assertIn(f"resolve {cloudlinux.url}", text)
            self.assertIn("download <resolved url> ->", text)
            self.assertFalse(cache.exists())
            self.assertFalse(cl_dest.exists())


class CloudLinuxFetchTests(unittest.TestCase):
    def test_catalog_digest_is_checked_and_no_sum_file_is_fetched(self) -> None:
        payload = b"qcow-bytes"
        digest = hashlib.sha256(payload).hexdigest()
        filename = "cloudlinux-9.8-x86_64-openstack-20261007.qcow2"
        href = f"/images/openstack/cloudlinux/nopanel/9-amd64/{filename}"
        catalog = json.dumps(
            {
                "productVersions": [
                    {
                        "systemName": "CloudLinux",
                        "major": 9,
                        "panels": [
                            {
                                "name": "nopanel",
                                "rows": [
                                    {
                                        "brand": "openstack",
                                        "checksum": digest,
                                        "action": {"kind": "download", "href": href},
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        )
        spec = _by_release("cloudlinux", "9")
        reads: list[str] = []
        streams: list[str] = []

        def read_text(url: str) -> str:
            reads.append(url)
            return catalog

        def stream(url: str, dest: Path) -> None:
            streams.append(url)
            dest.write_bytes(payload)

        with TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache"
            with (
                mock.patch("pve_prep.download._read_text", read_text),
                mock.patch("pve_prep.download._stream", stream),
                redirect_stdout(StringIO()),
            ):
                dest = fetch_verified(spec, cache)
            self.assertEqual(reads, [spec.url])
            self.assertEqual(streams, ["https://images.cloudlinux.com" + href])
            self.assertEqual(dest.name, filename)
            self.assertEqual(dest.read_bytes(), payload)

            streams.clear()
            with (
                mock.patch("pve_prep.download._read_text", read_text),
                mock.patch("pve_prep.download._stream", stream),
                redirect_stdout(StringIO()),
            ):
                again = fetch_verified(spec, cache)
            self.assertEqual(again, dest)
            self.assertEqual(streams, [])

            dest.unlink()

            def bad_stream(url: str, dest: Path) -> None:
                dest.write_bytes(b"nope")

            with (
                mock.patch("pve_prep.download._read_text", read_text),
                mock.patch("pve_prep.download._stream", bad_stream),
                redirect_stdout(StringIO()),
            ):
                with self.assertRaises(DownloadError):
                    fetch_verified(spec, cache)
            self.assertEqual(list(cache.glob("*.partial")), [])
            self.assertFalse(dest.exists())


def subprocess_result(argv: list[str], code: int):
    class _Result:
        returncode = code

        def __init__(self) -> None:
            self.args = argv

    return _Result()


if __name__ == "__main__":
    unittest.main()
