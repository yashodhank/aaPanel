# coding: utf-8
# ----------------------------------------------------
# aapanel
# ----------------------------------------------------
# Copyright (c) 2015-2099 aapanel Software(https://aapanel.com) All rights reserved.
# ----------------------------------------------------

"""
Tomcat / Java Runtime Adapter

Wraps the existing Java project subsystem behind a clean, contract-based interface
for the unified site creation flow. Delegates all heavy lifting to:
  - class_v2/projectModelV2/javaModel.py (main class)
  - mod/project/java/utils.py       (TomCat, JDKManager, utilities)

Contract: every public method accepts and returns plain dicts. The legacy
"get" (object-as-namespace) pattern is handled internally and never exposed.
"""

import json
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

import public
from public.exceptions import HintException

sys.path.insert(0, public.get_panel_path() + "/class_v2")
from capability_registry import CapabilityRegistry
from version_normalizer import VersionNormalizer


class TomcatRuntimeAdapter:
    """
    Adapter that presents a stable, contract-driven interface for creating,
    managing, and querying Tomcat / Java projects in aaPanel.

    Internal delegation target:
      - projectModelV2.javaModel.main   (projectBase subclass, ~5700 lines)
      - mod.project.java.utils.TomCat   (per-instance Tomcat management)
      - mod.project.java.utils.JDKManager (JDK listing / installation)

    The adapter does NOT reimplement logic — it translates between the new
    dict-based contract and the legacy `get`-object API used internally.
    """

    def __init__(self):
        self._java_model = None          # Lazy: projectModelV2.javaModel.main instance
        self._tomcat_utils = None        # Lazy: mod.project.java.utils module
        self._panel_path = public.get_panel_path()

    # ------------------------------------------------------------------
    # Lazy-load helpers
    # ------------------------------------------------------------------

    def _get_java_model(self):
        """Lazy-load and cache the existing javaModel main class instance."""
        if self._java_model is not None:
            return self._java_model

        project_path = self._panel_path + "/class_v2"
        if project_path not in sys.path:
            sys.path.insert(0, project_path)

        from projectModelV2.javaModel import main as JavaModel
        self._java_model = JavaModel()
        return self._java_model

    def _get_tomcat_utils(self):
        """Lazy-load and cache the mod/project/java/utils module."""
        if self._tomcat_utils is not None:
            return self._tomcat_utils

        mod_path = self._panel_path + "/mod/project/java"
        if mod_path not in sys.path:
            sys.path.insert(0, mod_path)

        import utils as _tomcat_utils
        self._tomcat_utils = _tomcat_utils
        return self._tomcat_utils

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate_project_args(self, args: dict) -> List[str]:
        """
        Validate project-creation arguments. Returns a list of human-readable
        error messages; empty list means all checks passed.

        Checks performed:
          - Required fields present
          - Runtime combo valid per CapabilityRegistry
          - Domain format basic sanity
        """
        errors = []

        # --- required fields ---
        for field in ("name", "domain", "path", "deployment_mode", "tomcat_version", "java_version", "port"):
            if not args.get(field):
                errors.append("Missing required field: {}".format(field))

        if errors:
            return errors

        # --- CapabilityRegistry validation ---
        valid, err_msg = CapabilityRegistry.validate_runtime_combo(
            runtime="tomcat",
            tomcat_version=args["tomcat_version"],
            java_version=args["java_version"],
            database_engine=args.get("database_engine"),
        )
        if not valid:
            errors.append(err_msg)

        # --- deployment mode supported ---
        modes = CapabilityRegistry.get_deployment_modes("tomcat")
        if modes is None or args["deployment_mode"] not in modes:
            errors.append(
                "Unsupported deployment_mode '{}'. Must be one of: {}".format(
                    args["deployment_mode"], modes or []
                )
            )

        # --- database engine (optional) ---
        db_engine = args.get("database_engine")
        if db_engine and db_engine != "none":
            engines = CapabilityRegistry.get_database_engines("tomcat")
            if engines is None or db_engine not in engines:
                errors.append(
                    "Unsupported database_engine '{}'. Must be one of: {}".format(
                        db_engine, engines or []
                    )
                )

        return errors

    # ------------------------------------------------------------------
    # Version discovery & installation
    # ------------------------------------------------------------------

    def get_installed_tomcat_versions(self) -> List[str]:
        """
        Return sorted list of installed Tomcat major versions (e.g. ["8.5", "9", "10.1"]).

        Checks /usr/local/bttomcat/tomcat<ver>/bin/daemon.sh existence for
        each known supported version.
        """
        utils = self._get_tomcat_utils()
        installed = []
        for ver_str in CapabilityRegistry.get_supported_tomcat_versions():
            try:
                ver_int = int(ver_str.split(".")[0])
            except ValueError:
                continue
            tc = utils.bt_tomcat(ver_int)
            if tc is not None and tc.installed:
                installed.append(ver_str)
        return installed

    def get_installed_java_versions(self) -> List[str]:
        """
        Return sorted list of installed Java major versions (e.g. ["8", "11", "17", "21"]).

        Reads /www/server/java directory and matches against the JDKManager listing.
        """
        utils = self._get_tomcat_utils()
        jdk_mgr = utils.JDKManager()
        installed = []
        for ver_name in jdk_mgr.versions_list:
            jdk_path = "/www/server/java/{}".format(ver_name)
            if os.path.isdir(jdk_path):
                # extract major version from name like "jdk-17.0.8" -> "17"
                major = VersionNormalizer.parse_java_major(ver_name)
                if major and major not in installed:
                    installed.append(major)
        return installed

    def install_tomcat_version(self, version: str) -> dict:
        """Queue installation of a Tomcat version via the aaPanel package system (async)."""
        if version not in CapabilityRegistry.get_supported_tomcat_versions():
            return {"status": False, "msg": "Tomcat version {} is not supported.".format(version)}
        utils = self._get_tomcat_utils()
        utils.TomCat.async_install_tomcat_new(version, jdk_path=None)
        return {"status": True, "data": {"version": version, "msg": "Installation queued."}}

    def install_java_version(self, version: str) -> dict:
        """Queue installation of a Java JDK version via the aaPanel package system (async)."""
        utils = self._get_tomcat_utils()
        jdk_mgr = utils.JDKManager()
        matching = [v for v in jdk_mgr.versions_list if VersionNormalizer.parse_java_major(v) == version]
        if not matching:
            return {"status": False, "msg": "Java version {} is not available.".format(version)}
        jdk_mgr.install_jdk(matching[0])
        return {"status": True, "data": {"version": version, "msg": "Installation queued."}}

    # ------------------------------------------------------------------
    # Project lifecycle
    # ------------------------------------------------------------------

    def create_project(self, project_contract: dict) -> dict:
        """
        Create a Tomcat / Java project.

        project_contract keys:
            name               (str)   Unique project name
            domain             (str)   Primary domain for Nginx reverse-proxy
            path               (str)   War path or exploded webapp directory
            deployment_mode    (str)   "shared" = co-hosted on system Tomcat,
                                        "isolated" = dedicated per-project Tomcat
            tomcat_version     (str)   e.g. "9", "10.1"
            java_version       (str)   e.g. "17", "21"
            port               (int)   Application listen port
            database_engine    (Optional[str]) "mysql", "pgsql", or None
            database_config    (Optional[dict]) db connection details

        Returns: {"status": True, "data": {...}} or {"status": False, "msg": "..."}

        Steps:
          1. Validate via CapabilityRegistry
          2. Ensure Tomcat version is installed, install if needed
          3. Ensure Java version is installed, install if needed
          4. Route to shared (project_type=1) or isolated (project_type=2) creation
          5. Configure Nginx reverse-proxy via existing javaModel infrastructure
          6. Return success with project details
        """
        # 1. Validate
        errors = self.validate_project_args(project_contract)
        if errors:
            return {"status": False, "msg": "; ".join(errors)}

        # 2. Check / install Tomcat
        installed_tc = self.get_installed_tomcat_versions()
        if project_contract["tomcat_version"] not in installed_tc:
            install_res = self.install_tomcat_version(project_contract["tomcat_version"])
            return {
                "status": False,
                "msg": "Tomcat {} is not installed. Installation queued. Please try again after installation completes.".format(
                    project_contract["tomcat_version"]
                ),
            }

        # 3. Check / install Java
        installed_jdk = self.get_installed_java_versions()
        if project_contract["java_version"] not in installed_jdk:
            install_res = self.install_java_version(project_contract["java_version"])
            return {
                "status": False,
                "msg": "Java {} is not installed. Installation queued. Please try again after installation completes.".format(
                    project_contract["java_version"]
                ),
            }

        # 4. Route to shared or isolated
        try:
            java_model = self._get_java_model()

            # Build the legacy `get` object from the contract dict
            get = self._contract_to_get(project_contract)

            if project_contract["deployment_mode"] == "shared":
                # project_type 1 = internal (shared)
                get.project_type = 1
                result = java_model.create_internal_project(get)
                return self._parse_legacy_result(result, project_contract)
            elif project_contract["deployment_mode"] == "isolated":
                # project_type 2 = independent (isolated)
                get.project_type = 2
                result = java_model.create_independent_project(get)
                return self._parse_legacy_result(result, project_contract)
            else:
                return {"status": False, "msg": "Unsupported deployment_mode: {}".format(project_contract["deployment_mode"])}

        except Exception as ex:
            return {"status": False, "msg": "Project creation failed: {}".format(str(ex))}

    def delete_project(self, project_id: str) -> dict:
        """
        Delete a Tomcat project by name and clean up all associated configs.

        Delegates to javaModel.main.remove_project which handles:
          - Stopping the app / tomcat instance
          - Removing Nginx/Apache config
          - Deleting site DB row
          - Cleaning directory structure
        """
        try:
            java_model = self._get_java_model()

            # Build a minimal legacy get object
            class Get:
                pass
            get = Get()
            get.project_name = project_id

            result = java_model.remove_project(get)
            return self._parse_legacy_result(result, {"name": project_id})
        except Exception as ex:
            return {"status": False, "msg": "Project deletion failed: {}".format(str(ex))}

    # ------------------------------------------------------------------
    # Query methods
    # ------------------------------------------------------------------

    def get_project_info(self, project_id: str) -> dict:
        """
        Retrieve project details from the sites database table.

        Returns full site row including project_config JSON blob.
        """
        try:
            site = public.M("sites").where("name=?", (project_id,)).find()
            if not site:
                return {"status": False, "msg": "Project '{}' not found.".format(project_id)}
            return {"status": True, "data": site}
        except Exception as ex:
            return {"status": False, "msg": "DB query failed: {}".format(str(ex))}

    def list_tomcat_projects(self) -> List[dict]:
        """
        List all projects with project_type='Java' in the sites table.

        Returns list of dicts with keys: id, name, path, project_config, status.
        """
        try:
            projects = public.M("sites").where("project_type=?", ("Java",)).select()
            return projects if projects else []
        except Exception as ex:
            public.WriteLog("TomcatAdapter", "Failed to list projects: {}".format(str(ex)))
            return []

    # ------------------------------------------------------------------
    # Runtime operations
    # ------------------------------------------------------------------

    def restart_service(self, project_id: str, scope: str = "app") -> dict:
        """
        Restart a project at the specified scope.

        Scope values:
          "app"     -> Reload the specific application context via Tomcat manager.
                       Delegates to existing javaModel logic for the project.
          "runtime" -> Restart the isolated Tomcat instance dedicated to this project.
                       Only valid for isolated (independent) deployments.
          "shared"  -> Restart the shared system Tomcat service as a whole.
                       Dangerous — affects all co-hosted projects.

        Returns: {"status": True, "data": {...}} or {"status": False, "msg": "..."}
        """
        if scope not in ("app", "runtime", "shared"):
            return {"status": False, "msg": "Invalid scope '{}'. Must be 'app', 'runtime', or 'shared'.".format(scope)}

        site = public.M("sites").where("name=?", (project_id,)).find()
        if not site:
            return {"status": False, "msg": "Project '{}' not found.".format(project_id)}

        project_config = site.get("project_config")
        if isinstance(project_config, str):
            try:
                project_config = json.loads(project_config)
            except json.JSONDecodeError:
                return {"status": False, "msg": "Invalid project_config JSON for project '{}'.".format(project_id)}

        java_type = project_config.get("java_type", "") if isinstance(project_config, dict) else ""

        try:
            java_model = self._get_java_model()
            utils = self._get_tomcat_utils()

            if scope == "app":
                try:
                    get_obj = public.to_dict_obj({"project_name": project_id})
                    java_model.restart_project(get_obj)
                except Exception as e:
                    return {"status": False, "msg": "App context reload failed: {}".format(str(e))}
                return {"status": True, "data": {"msg": "Application context reloaded for {}.".format(project_id)}}

            elif scope == "runtime":
                if java_type != "duli":
                    return {"status": False, "msg": "Isolated container restart only applies to independent projects."}
                try:
                    tc = utils.site_tomcat(project_id)
                    if tc is None:
                        return {"status": False, "msg": "Tomcat instance for project {} not found.".format(project_id)}
                    run_user = project_config.get("run_user", "root") if isinstance(project_config, dict) else "root"
                    if tc.running():
                        tc.restart(by_user=run_user)
                    else:
                        tc.start(by_user=run_user)
                except Exception as e:
                    return {"status": False, "msg": "Runtime restart failed: {}".format(str(e))}
                return {"status": True, "data": {"msg": "Isolated runtime restarted for {}.".format(project_id)}}

            elif scope == "shared":
                tomcat_version = project_config.get("tomcat_version", "") if isinstance(project_config, dict) else ""
                if not tomcat_version:
                    return {"status": False, "msg": "No Tomcat version found in project config."}
                ver_int = 0
                try:
                    ver_int = int(str(tomcat_version).split(".")[0])
                except ValueError:
                    pass
                if ver_int:
                    tc = utils.bt_tomcat(ver_int)
                    if tc is None:
                        return {"status": False, "msg": "Shared Tomcat {} not found.".format(tomcat_version)}
                    if tc.running():
                        tc.restart()
                    else:
                        tc.start()
                return {"status": True, "data": {"msg": "Shared Tomcat {} restarted.".format(tomcat_version)}}

        except Exception as ex:
            return {"status": False, "msg": "Restart failed: {}".format(str(ex))}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _contract_to_get(self, contract: dict):
        """
        Convert the adapter's dict-based contract into a legacy `get` object
        suitable for passing to projectModelV2.javaModel.main methods.

        This isolates the adapter's interface from the internal object-namespace
        calling convention used throughout the existing projectModelV2 classes.
        """
        class Get:
            pass
        get = Get()

        get.project_name = contract["name"]
        get.domain = contract["domain"]
        get.project_path = contract["path"]
        get.tomcat_version = contract["tomcat_version"]
        get.project_ps = contract.get("description", contract["name"])

        # Port handling
        port = contract.get("port")
        if port is not None:
            get.port = str(port)

        # Java version -> jdk path resolution
        jdk_path = self._resolve_jdk_path(contract["java_version"])
        if jdk_path:
            get.jdk_path = jdk_path

        # Deployment-specific extras
        if contract.get("deployment_mode") == "shared":
            # Shared (internal) Tomcat requires bind_extranet optionally
            if contract.get("bind_extranet"):
                get.bind_extranet = True

        # Database config passthrough
        db_cfg = contract.get("database_config")
        if db_cfg:
            get.database_config = db_cfg

        return get

    def _parse_legacy_result(self, result, context: dict) -> dict:
        """
        Interpret the return value of legacy projectModelV2.javaModel methods.

        The legacy API returns structured dicts via public.returnMsg(boolean, str).
        We normalize those into the adapter's {"status": bool, "data": ...} format.
        """
        if not isinstance(result, dict):
            return {
                "status": True,
                "data": {"name": context.get("name", ""), "result": result},
            }

        is_success = result.get("status", False) or result.get("success")
        if is_success:
            return {"status": True, "data": {"name": context.get("name", ""), "details": result}}
        else:
            msg = result.get("msg", "Unknown error.")
            return {"status": False, "msg": str(msg)}

    def _resolve_jdk_path(self, java_version: str) -> Optional[str]:
        """
        Map a Java major version string (e.g. "17", "21") to an installed
        JDK path on the system.
        """
        utils = self._get_tomcat_utils()
        jdk_mgr = utils.JDKManager()
        for ver_name in jdk_mgr.versions_list:
            if VersionNormalizer.parse_java_major(ver_name) == java_version:
                candidate = "/www/server/java/{}".format(ver_name)
                if os.path.isdir(candidate):
                    return candidate
        return None
