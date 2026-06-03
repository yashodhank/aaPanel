# coding: utf-8
import re
from typing import List, Optional, Tuple


class VersionNormalizer:
    """
    Normalizes and parses version strings for Tomcat, Java, PostgreSQL, and PHP.
    Provides consistent version comparison across all runtime adapters.
    """

    @staticmethod
    def parse_major(version_str: str) -> str:
        """
        Parse the major.minor version from a full version string.
        Examples:
            "10.1.34" -> "10.1"
            "9.0.89" -> "9"
            "8.5.100" -> "8.5"
            "11.0.25" -> "11"
        """
        v_res = re.search(r"(?P<major>\d+)\.(?P<minor>\d{1,2})", version_str)
        if not v_res:
            return ""
        major = v_res.group("major")
        minor = v_res.group("minor")
        if int(minor) == 0:
            return major
        return "{}.{}".format(major, minor)

    @staticmethod
    def parse_java_major(version_str: str) -> str:
        """
        Parse the Java major version from a full JDK version string.
        Examples:
            "21.0.5" -> "21"
            "17.0.13" -> "17"
            "1.8.0_412" -> "8"
            "11.0.25" -> "11"
        """
        v_res = re.search(r"(?P<major>\d+)\.(?P<minor>\d+)", version_str)
        if not v_res:
            return ""
        major = v_res.group("major")
        minor = v_res.group("minor")
        if major == "1" and int(minor) >= 4:
            return minor
        return major

    @staticmethod
    def parse_pgsql_major(version_str: str) -> str:
        """
        Parse the PostgreSQL major version from a full version string.
        Examples:
            "16.3" -> "16"
            "15.8" -> "15"
            "17.0" -> "17"
        """
        v_res = re.search(r"(?P<major>\d+)\.(?P<minor>\d+)", version_str)
        if not v_res:
            return ""
        return v_res.group("major")

    @staticmethod
    def get_latest_patch(major: str, installed_versions: List[str]) -> Optional[str]:
        """
        Given a major version and a list of installed full versions,
        return the full version string with the highest patch level.
        Example:
            major="9", installed=["9.0.87", "9.0.89", "9.0.85"] -> "9.0.89"
        Returns None if no matching versions found.
        """
        matching = [v for v in installed_versions if VersionNormalizer.parse_major(v) == major]
        if not matching:
            return None
        matching.sort(key=VersionNormalizer._sort_key)
        return matching[-1]

    @staticmethod
    def compare_versions(version_a: str, version_b: str) -> int:
        """
        Compare two dot-separated version strings.
        Returns:
            -1 if version_a < version_b
             0 if version_a == version_b
             1 if version_a > version_b
        Examples:
            compare_versions("10.1.34", "10.1.30") -> 1
            compare_versions("9.0.89", "10.1.0") -> -1
        """
        parts_a = [int(p) for p in version_a.split(".")]
        parts_b = [int(p) for p in version_b.split(".")]

        max_len = max(len(parts_a), len(parts_b))
        parts_a += [0] * (max_len - len(parts_a))
        parts_b += [0] * (max_len - len(parts_b))

        for a, b in zip(parts_a, parts_b):
            if a < b:
                return -1
            if a > b:
                return 1
        return 0

    @staticmethod
    def is_version_in_range(version: str, min_version: str, max_version: str = None) -> bool:
        """
        Check if a version falls within a range (inclusive).
        If max_version is None, only checks min_version.
        Uses parse_major() to extract major.minor before comparing.
        """
        v_major = VersionNormalizer.parse_major(version)
        min_major = VersionNormalizer.parse_major(min_version)
        if VersionNormalizer.compare_versions(v_major, min_major) < 0:
            return False
        if max_version is not None:
            max_major = VersionNormalizer.parse_major(max_version)
            if VersionNormalizer.compare_versions(v_major, max_major) > 0:
                return False
        return True

    @staticmethod
    def normalize_php_version(version_str: str) -> str:
        """
        Normalize PHP version to x.y format.
        Examples:
            "74" -> "7.4"
            "8.1" -> "8.1"
            "php74" -> "7.4"
        """
        cleaned = re.sub(r"(?i)php", "", version_str.strip()).strip()
        cleaned = cleaned.replace("_", ".").replace("-", ".")

        if re.match(r"^\d+$", cleaned):
            if len(cleaned) >= 2:
                major = cleaned[:-1]
                minor = cleaned[-1:]
                return "{}.{}".format(major, minor)
            return cleaned

        return cleaned

    @staticmethod
    def _sort_key(version_str: str) -> Tuple[int, ...]:
        return tuple(int(p) for p in version_str.split("."))
