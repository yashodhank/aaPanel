import unittest
import sys
import os

if "/www/server/panel" not in sys.path:
    sys.path.insert(0, "/www/server/panel")

os.environ.setdefault("BT_PANEL", "/www/server/panel")


class TestVersionNormalizer(unittest.TestCase):
    """Verify that registry versions map correctly to legacy major versions."""

    @classmethod
    def setUpClass(cls):
        from class_v2.version_normalizer import VersionNormalizer
        cls.normalizer = VersionNormalizer

    def test_registry_to_legacy_mapping(self):
        self.assertEqual(self.normalizer.normalize_tomcat_version("8.5"), "8")
        self.assertEqual(self.normalizer.normalize_tomcat_version("10.1"), "10")
        self.assertEqual(self.normalizer.normalize_tomcat_version("9"), "9")
        self.assertEqual(self.normalizer.normalize_tomcat_version("11"), "11")

    def test_parse_major_strips_minor_zero(self):
        self.assertEqual(self.normalizer.parse_major("9.0.89"), "9")
        self.assertEqual(self.normalizer.parse_major("10.1.34"), "10.1")
        self.assertEqual(self.normalizer.parse_major("8.5.100"), "8.5")

    def test_parse_java_major(self):
        self.assertEqual(self.normalizer.parse_java_major("21.0.5"), "21")
        self.assertEqual(self.normalizer.parse_java_major("1.8.0_412"), "8")


class TestSSRFProtection(unittest.TestCase):
    """Validate URL deployment security guards."""

    @classmethod
    def setUpClass(cls):
        from class_v2.adapters.deployment_adapter import DeploymentAdapter
        cls.adapter = DeploymentAdapter()

    def test_private_ipv4_rejected(self):
        self.assertTrue(self.adapter._is_private_ipv4("127.0.0.1"))
        self.assertTrue(self.adapter._is_private_ipv4("10.0.0.1"))
        self.assertTrue(self.adapter._is_private_ipv4("192.168.1.1"))
        self.assertTrue(self.adapter._is_private_ipv4("172.16.0.1"))
        self.assertTrue(self.adapter._is_private_ipv4("169.254.1.1"))
        self.assertTrue(self.adapter._is_private_ipv4("0.0.0.0"))
        self.assertTrue(self.adapter._is_private_ipv4("100.64.0.1"))
        self.assertTrue(self.adapter._is_private_ipv4("224.0.0.1"))
        self.assertTrue(self.adapter._is_private_ipv4("240.0.0.1"))

    def test_public_ipv4_accepted(self):
        self.assertFalse(self.adapter._is_private_ipv4("8.8.8.8"))
        self.assertFalse(self.adapter._is_private_ipv4("1.1.1.1"))
        self.assertFalse(self.adapter._is_private_ipv4("93.184.216.34"))

    def test_private_ipv6_rejected(self):
        self.assertTrue(self.adapter._is_private_ipv6("::1"))
        self.assertTrue(self.adapter._is_private_ipv6("fe80::1"))
        self.assertTrue(self.adapter._is_private_ipv6("fc00::1"))
        self.assertTrue(self.adapter._is_private_ipv6("ff02::1"))

    def test_public_ipv6_accepted(self):
        self.assertFalse(self.adapter._is_private_ipv6("2001:4860:4860::8888"))
        self.assertFalse(self.adapter._is_private_ipv6("2606:2800:220:1:248:1893:25c8:1946"))


class TestCapabilityRegistry(unittest.TestCase):
    """Validate registry matches documented support."""

    @classmethod
    def setUpClass(cls):
        from class_v2.capability_registry import CapabilityRegistry
        cls.reg = CapabilityRegistry

    def test_supported_tomcat_versions(self):
        versions = self.reg.get_supported_tomcat_versions()
        self.assertIn("8.5", versions)
        self.assertIn("9", versions)
        self.assertIn("10.1", versions)
        self.assertIn("11", versions)

    def test_java_compat_for_each_tomcat(self):
        for tc in self.reg.get_supported_tomcat_versions():
            java_vers = self.reg.get_valid_java_versions(tc)
            self.assertIsNotNone(java_vers, "No Java compat for Tomcat {}".format(tc))
            self.assertGreater(len(java_vers), 0)

    def test_runtime_tomcat_is_supported(self):
        self.assertTrue(self.reg.is_runtime_supported("tomcat"))

    def test_validate_valid_combo(self):
        valid, _ = self.reg.validate_runtime_combo("tomcat", tomcat_version="9", java_version="17", database_engine="pgsql")
        self.assertTrue(valid)

    def test_validate_invalid_combo(self):
        valid, err = self.reg.validate_runtime_combo("tomcat", tomcat_version="9", java_version="8", database_engine="redis")
        self.assertFalse(valid)


class TestV2Envelope(unittest.TestCase):
    """Validate that success_v2 / fail_v2 produce the expected wire format."""

    @classmethod
    def setUpClass(cls):
        import public
        cls.public = public

    def test_success_v2_wraps_dict_in_message(self):
        result = self.public.success_v2({"siteStatus": True, "siteId": 42})
        self.assertEqual(result["status"], 0)
        self.assertTrue("message" in result)
        self.assertTrue(result["message"]["siteStatus"])
        self.assertEqual(result["message"]["siteId"], 42)

    def test_fail_v2_returns_negative_status(self):
        result = self.public.fail_v2("Something went wrong")
        self.assertEqual(result["status"], -1)


if __name__ == "__main__":
    unittest.main()
