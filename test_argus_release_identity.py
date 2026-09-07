import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

import argus_release_identity as identity
import argus_remote_durability as durability

_moomoo = types.ModuleType("moomoo")
_moomoo.OpenQuoteContext = lambda *args, **kwargs: None
_moomoo.OpenSecTradeContext = lambda *args, **kwargs: None
_moomoo.RET_OK = 0
sys.modules.setdefault("moomoo", _moomoo)

import scanner


class ReleaseIdentityTests(unittest.TestCase):
    def test_version_sources_are_independent_and_current(self):
        self.assertEqual("v13.5.60", identity.product_version())
        self.assertEqual("13.5.60", identity.backend_version())
        self.assertEqual("13.5.60", identity.frontend_version())
        self.assertEqual(identity.backend_version(),
                         scanner._semantic_app_version())
        self.assertEqual(identity.frontend_version(),
                         scanner._frontend_semantic_version())

    def test_product_and_four_component_coordinates_are_never_inferred(self):
        value = identity.release_identity(
            backend_sha="backend1", frontend_sha="frontend1")
        self.assertEqual({
            "productVersion": "v13.5.60",
            "backendVersion": "13.5.60",
            "backendBuildSha": "backend1",
            "frontendVersion": "13.5.60",
            "frontendBuildSha": "frontend1",
        }, value)
        unknown = identity.release_identity(backend_sha=None)
        self.assertEqual("v13.5.60", unknown["productVersion"])
        self.assertEqual("unknown", unknown["backendBuildSha"])
        self.assertEqual("unknown", unknown["frontendBuildSha"])

    def test_public_build_identity_retains_compatibility_fields(self):
        value = durability.build_identity(
            app_version="13.3.0", backend_sha="abc1234",
            frontend_version="13.3.0", frontend_sha="def5678")
        self.assertEqual("13.3.0", value["appVersion"])
        self.assertEqual("13.3.0", value["backendVersion"])
        self.assertEqual("abc1234", value["backendBuildSha"])
        self.assertEqual("13.3.0", value["frontendVersion"])
        self.assertEqual("def5678", value["frontendBuildSha"])

    def test_version_files_are_plain_public_metadata(self):
        product = json.loads(identity.PRODUCT_VERSION_FILE.read_text())
        backend = json.loads(identity.BACKEND_VERSION_FILE.read_text())
        frontend = json.loads(identity.FRONTEND_VERSION_FILE.read_text())
        self.assertEqual({
            "schemaVersion": "argus-product-version-v1",
            "productVersion": "v13.5.60",
        }, product)
        self.assertEqual({"version": "13.5.60"}, backend)
        self.assertEqual("13.5.60", frontend["version"])

    def test_product_version_never_falls_back_to_component_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "product-version.json"
            for content in (
                None,
                "not-json",
                '{"schemaVersion":"wrong","productVersion":"v13.5.60"}',
                '{"schemaVersion":"argus-product-version-v1",'
                '"productVersion":"13.5.60"}',
                '{"schemaVersion":"argus-product-version-v1",'
                '"productVersion":"v13.5.60","frontendVersion":"13.5.60"}',
            ):
                if content is None:
                    source.unlink(missing_ok=True)
                else:
                    source.write_text(content, encoding="utf-8")
                with mock.patch.object(identity, "PRODUCT_VERSION_FILE", source):
                    self.assertEqual("", identity.product_version())
                    self.assertEqual(
                        "unknown",
                        identity.release_identity(backend_sha=None)["productVersion"],
                    )


if __name__ == "__main__":
    unittest.main()
