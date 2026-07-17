"""Tests for the privacy/release gate in scripts/validate_release.py.

Every fixture here is synthetic. The owner's real BLE addresses and
CoreBluetooth UUID must never appear in this file; the hash-detection path is
exercised with a made-up token whose hash is injected at runtime.
"""

from __future__ import annotations

import hashlib
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import scripts.validate_release as v


class MacExtractionTests(unittest.TestCase):
    def test_six_octet_token_is_a_mac(self) -> None:
        self.assertEqual(
            v._iter_mac_addresses("prefix AA:BB:CC:DD:EE:FF suffix"),
            ["aa:bb:cc:dd:ee:ff"],
        )

    def test_companion_id_is_not_a_mac(self) -> None:
        companion = "00:11:22:33:44:55:66:77:88:99:AA:BB:CC:DD:EE:FF"
        self.assertEqual(v._iter_mac_addresses(companion), [])

    def test_hash_of_hex_digest_is_not_mistaken_for_mac(self) -> None:
        # sha256:<hex> style lines from a hash-locked requirements file.
        line = "--hash=sha256:02cb7ff33ded4f1532476731f89ede53e2e488a8e6205515a82144246ffa7dcc"
        self.assertEqual(v._iter_mac_addresses(line), [])


class Ipv4ExtractionTests(unittest.TestCase):
    def test_documentation_and_loopback_are_allowed(self) -> None:
        allowed = "192.0.2.10 198.51.100.7 203.0.113.9 127.0.0.1 0.0.0.0"
        for address in v._iter_ipv4_addresses(allowed):
            self.assertTrue(
                any(address in network for network in v.ALLOWED_IP_NETWORKS),
                msg=f"{address} should be inside a documentation range",
            )

    def test_public_address_is_not_allowed(self) -> None:
        public = ".".join(["8", "8", "8", "8"])
        addresses = v._iter_ipv4_addresses(public)
        flagged = [
            str(a)
            for a in addresses
            if not any(a in network for network in v.ALLOWED_IP_NETWORKS)
        ]
        self.assertEqual(flagged, [public])

    def test_invalid_octets_are_ignored(self) -> None:
        self.assertEqual(v._iter_ipv4_addresses("999.1.1.1"), [])


class ForbiddenIdentifierHashTests(unittest.TestCase):
    def test_three_identifiers_are_registered(self) -> None:
        self.assertEqual(len(v.FORBIDDEN_IDENTIFIER_HASHES), 3)

    def test_hashes_are_lowercase_hex_digests(self) -> None:
        for digest in v.FORBIDDEN_IDENTIFIER_HASHES:
            self.assertRegex(digest, r"^[0-9a-f]{64}$")


class ScanSelfInclusionTests(unittest.TestCase):
    def test_validator_scans_itself(self) -> None:
        self.assertTrue(v.should_scan(Path(v.__file__)))

    def test_caches_are_skipped(self) -> None:
        self.assertFalse(v.should_scan(Path("/repo/.venv/lib/thing.py")))
        self.assertFalse(v.should_scan(Path("/repo/.git/config")))


class TreeScanTests(unittest.TestCase):
    def _scan(self, contents: str, name: str = "sample.md"):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / name).write_text(contents, encoding="utf-8")
            with mock.patch.object(v, "ROOT", root):
                v.check_no_private_material()

    def test_clean_synthetic_tree_passes(self) -> None:
        self._scan("Device AA:BB:CC:DD:EE:FF at 192.0.2.5 on 127.0.0.1.")

    def test_non_documentation_mac_is_flagged(self) -> None:
        # Built dynamically so this rogue MAC is not a literal token that the
        # gate would (correctly) flag inside this very test file.
        rogue = ":".join(["12", "34", "56", "78", "9a", "bc"])
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with self.assertRaises(SystemExit):
                self._scan(f"Rogue device {rogue} appeared.")
        self.assertIn("non-documentation MAC address found", stderr.getvalue())
        self.assertNotIn(rogue, stderr.getvalue())

    def test_public_ipv4_is_flagged(self) -> None:
        public = ".".join(["8", "8", "8", "8"])
        with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
            with self.assertRaises(SystemExit):
                self._scan(f"Beacon phoned home to {public} today.")
        self.assertIn("non-documentation IPv4 address found", stderr.getvalue())
        self.assertNotIn(public, stderr.getvalue())

    def test_known_identifier_hash_is_flagged(self) -> None:
        synthetic_mac = ":".join(["de", "ad", "be", "ef", "00", "11"])
        digest = hashlib.sha256(synthetic_mac.encode("utf-8")).hexdigest()
        injected = dict(v.FORBIDDEN_IDENTIFIER_HASHES)
        injected[digest] = "synthetic private address"
        # Allow the synthetic MAC through the general gate so only the hash
        # path can trip; then assert it trips.
        allowed = v.ALLOWED_MAC_ADDRESSES | {synthetic_mac}
        with (
            mock.patch.object(v, "FORBIDDEN_IDENTIFIER_HASHES", injected),
            mock.patch.object(v, "ALLOWED_MAC_ADDRESSES", allowed),
        ):
            with mock.patch("sys.stderr", new_callable=io.StringIO) as stderr:
                with self.assertRaises(SystemExit):
                    self._scan(f"Contains {synthetic_mac} somewhere.")
        self.assertIn("forbidden private identifier found", stderr.getvalue())
        self.assertNotIn(synthetic_mac, stderr.getvalue())
        self.assertNotIn("synthetic private address", stderr.getvalue())

    def test_private_runtime_directory_is_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "captures").mkdir()
            with mock.patch.object(v, "ROOT", root):
                with self.assertRaises(SystemExit):
                    v.check_no_private_material()


if __name__ == "__main__":
    unittest.main()
