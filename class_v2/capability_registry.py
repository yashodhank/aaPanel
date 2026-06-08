# coding: utf-8
from typing import Dict, List, Optional, Tuple


class CapabilityRegistry:
    """
    Central registry that answers which versions and combinations are supported.
    Replaces scattered hardcoded version checks across the codebase.
    """

    SUPPORTED_TOMCAT_VERSIONS = ["8.5", "9", "10.1", "11"]

    TOMCAT_JAVA_COMPAT = {
        "8.5": ["8", "11", "17", "21"],
        "9":   ["8", "11", "17", "21"],
        "10.1": ["11", "17", "21"],
        "11":  ["17", "21"],
    }

    SUPPORTED_PGSQL_VERSIONS = ["13", "14", "15", "16", "17"]

    DEPLOYMENT_MODES = {
        "php": ["shared"],
        "tomcat": ["shared", "isolated"],
    }

    DATABASE_ENGINES = {
        "php": ["mysql", "pgsql", "none"],
        "tomcat": ["mysql", "pgsql", "none"],
    }

    AVAILABILITY = {
        "tomcat": "panel_managed",
        "java": "panel_managed",
        "pgsql": "distro",
    }

    @classmethod
    def get_supported_tomcat_versions(cls) -> List[str]:
        """Return list of supported Tomcat major versions."""
        return list(cls.SUPPORTED_TOMCAT_VERSIONS)

    @classmethod
    def get_valid_java_versions(cls, tomcat_major: str) -> Optional[List[str]]:
        """Return list of valid Java versions for a given Tomcat major. Returns None if Tomcat major is not supported."""
        if tomcat_major not in cls.TOMCAT_JAVA_COMPAT:
            return None
        return list(cls.TOMCAT_JAVA_COMPAT[tomcat_major])

    @classmethod
    def get_supported_pgsql_versions(cls) -> List[str]:
        """Return list of supported PostgreSQL major versions."""
        return list(cls.SUPPORTED_PGSQL_VERSIONS)

    @classmethod
    def get_deployment_modes(cls, runtime: str) -> Optional[List[str]]:
        """Return list of valid deployment modes for a runtime. Returns None if runtime is not supported."""
        if runtime not in cls.DEPLOYMENT_MODES:
            return None
        return list(cls.DEPLOYMENT_MODES[runtime])

    @classmethod
    def get_database_engines(cls, runtime: str) -> Optional[List[str]]:
        """Return list of valid database engines for a runtime. Returns None if runtime is not supported."""
        if runtime not in cls.DATABASE_ENGINES:
            return None
        return list(cls.DATABASE_ENGINES[runtime])

    @classmethod
    def validate_runtime_combo(
        cls,
        runtime: str,
        tomcat_version: str = None,
        java_version: str = None,
        database_engine: str = None,
    ) -> Tuple[bool, str]:
        """
        Validate a runtime configuration combination.
        Returns (True, "") if valid, (False, error_message) if invalid.
        """
        if runtime not in cls.DEPLOYMENT_MODES:
            return False, "Unsupported runtime: {}".format(runtime)

        if database_engine is not None:
            valid_engines = cls.DATABASE_ENGINES.get(runtime, [])
            if database_engine not in valid_engines:
                return (
                    False,
                    "Database engine '{}' is not valid for runtime '{}'. Allowed: {}".format(
                        database_engine, runtime, ", ".join(valid_engines)
                    ),
                )

        if runtime == "tomcat":
            if tomcat_version is not None:
                if tomcat_version not in cls.SUPPORTED_TOMCAT_VERSIONS:
                    return (
                        False,
                        "Tomcat version '{}' is not supported. Supported: {}".format(
                            tomcat_version,
                            ", ".join(cls.SUPPORTED_TOMCAT_VERSIONS),
                        ),
                    )
                if java_version is not None:
                    compat_list = cls.TOMCAT_JAVA_COMPAT[tomcat_version]
                    if java_version not in compat_list:
                        return (
                            False,
                            "Java version '{}' is not compatible with Tomcat {}. Allowed: {}".format(
                                java_version, tomcat_version, ", ".join(compat_list)
                            ),
                        )

        return True, ""

    @classmethod
    def get_availability(cls, component: str) -> str:
        """Return the availability mode for a component (tomcat/java/pgsql)."""
        return cls.AVAILABILITY.get(component, "panel_managed")

    @classmethod
    def is_runtime_supported(cls, runtime: str) -> bool:
        """Check if a runtime type is supported."""
        return runtime in cls.DEPLOYMENT_MODES
