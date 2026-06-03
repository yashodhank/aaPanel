# coding: utf-8
# -------------------------------------------------------------------
# aaPanel
# -------------------------------------------------------------------
# Copyright (c) 2015-2099 aaPanel(www.aapanel.com) All rights reserved.
# -------------------------------------------------------------------

import os
import re
import random
import string
import sys
from typing import Dict, List, Optional, Tuple

import public
from public.exceptions import HintException

sys.path.insert(0, os.path.join(public.get_panel_path(), "class_v2"))
from capability_registry import CapabilityRegistry


class PgsqlProjectAdapter:
    """
    Project-aware PostgreSQL provisioning adapter.

    Creates databases and users during project creation, detects
    local PostgreSQL installations from both panel-managed and distro packages.
    Delegates actual database operations to the existing pgsqlModel.py
    and PgsqlTool subsystems.
    """

    _PG_DATA_DIR = "/www/server/pgsql/data"
    _PG_PANEL_BIN_DIR = "/www/server/pgsql/bin"
    _PG_ROOT_PASSWORD_FILE = "/www/server/panel/data/postgresAS.json"
    _PG_HBA_FILE = "/www/server/pgsql/data/pg_hba.conf"
    _PG_CONF_FILE = "/www/server/pgsql/data/postgresql.conf"
    _DISTRO_BASE_DIR = "/usr/lib/postgresql"
    _PROJECT_DB_PS_PREFIX = "[project]"

    def __init__(self):
        self._pgsql_model = None
        self._pgsql_tool = None
        self._cached_install = None

    @property
    def _model(self):
        """Lazy-load the pgsqlModel main class."""
        if self._pgsql_model is None:
            # databaseModelV2 is available in the panel PYTHONPATH at /www/server/panel/class_v2
            from databaseModelV2.pgsqlModel import main as pgsqlModel
            self._pgsql_model = pgsqlModel
        return self._pgsql_model

    @property
    def _tool(self):
        """Lazy-load the PgsqlTool from mod/base/database_tool."""
        if self._pgsql_tool is None:
            from mod.base.database_tool.pgsql import PgsqlTool
            self._pgsql_tool = PgsqlTool()
        return self._pgsql_tool

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_local_installation(self) -> Optional[dict]:
        """
        Detect a local PostgreSQL installation.

        Returns None if not found, otherwise:
        {
            "installation_type": "panel_managed" | "distro",
            "version": "16",
            "port": 5432,
            "data_dir": "/www/server/pgsql/data",
            "bin_dir": "/www/server/pgsql/bin" or "/usr/lib/postgresql/16/bin"
        }

        Check order:
        1. /www/server/pgsql/ (panel-managed installation)
        2. /usr/lib/postgresql/*/bin/postgres (distro packages)
        3. /usr/bin/postgres (system installation)
        4. pg_config or which postgres (PATH-based)
        """
        # 1. Panel-managed installation
        panel_bin = os.path.join(self._PG_PANEL_BIN_DIR, "postgres")
        if os.path.isfile(panel_bin) or os.path.isfile(os.path.join(self._PG_PANEL_BIN_DIR, "pg_config")):
            version = self._get_panel_version()
            port = self._get_panel_port()
            return {
                "installation_type": "panel_managed",
                "version": version,
                "port": port,
                "data_dir": self._PG_DATA_DIR,
                "bin_dir": self._PG_PANEL_BIN_DIR,
            }

        # 2. Distro packages (/usr/lib/postgresql/*)
        distro_info = self._detect_distro_package()
        if distro_info is not None:
            return distro_info

        # 3. System installation (/usr/bin/postgres)
        if os.path.isfile("/usr/bin/postgres"):
            version = self._get_binary_version("/usr/bin/postgres")
            if version:
                distro_bin = os.path.join(self._DISTRO_BASE_DIR, version, "bin")
                return {
                    "installation_type": "distro",
                    "version": version,
                    "port": 5432,
                    "data_dir": self._try_find_data_dir(),
                    "bin_dir": distro_bin if os.path.isdir(distro_bin) else "/usr/bin",
                }

        # 4. PATH-based (pg_config or which postgres)
        pg_config_path = self._which("pg_config")
        if pg_config_path:
            version = self._get_binary_version(pg_config_path)
            if version:
                bin_dir = os.path.dirname(pg_config_path)
                return {
                    "installation_type": "distro",
                    "version": version,
                    "port": 5432,
                    "data_dir": self._try_find_data_dir(),
                    "bin_dir": bin_dir,
                }

        postgres_path = self._which("postgres")
        if postgres_path:
            version = self._get_binary_version(postgres_path)
            if version:
                bin_dir = os.path.dirname(postgres_path)
                return {
                    "installation_type": "distro",
                    "version": version,
                    "port": 5432,
                    "data_dir": self._try_find_data_dir(),
                    "bin_dir": bin_dir,
                }

        return None

    def is_running(self) -> bool:
        """Check if the PostgreSQL service is currently running."""
        # Check panel-managed init script
        result = public.ExecShell("/etc/init.d/pgsql status 2>/dev/null")
        if result and len(result) >= 2 and result[1].find("running") != -1:
            return True

        # Check pg_isready utility
        install = self.detect_local_installation()
        if install:
            pg_isready_bin = os.path.join(install["bin_dir"], "pg_isready")
            if os.path.isfile(pg_isready_bin):
                # Try running as postgres user
                out = public.ExecShell("su - postgres -c '" + pg_isready_bin + " -q 2>/dev/null' && echo OK || echo FAIL")
                if out and len(out) > 0 and "OK" in out[0]:
                    return True

            # Try with sudo
            out = public.ExecShell(pg_isready_bin + " -q 2>/dev/null && echo OK || echo FAIL")
            if out and len(out) > 0 and "OK" in out[0]:
                return True

        # Check process list
        result = public.ExecShell("pgrep -x postgres 2>/dev/null")
        if result and len(result) > 0 and result[0].strip():
            return True

        return False

    def get_available_versions(self) -> List[str]:
        """Return list of available PostgreSQL versions from capability registry."""
        return CapabilityRegistry.get_supported_pgsql_versions()

    # ------------------------------------------------------------------
    # Provisioning
    # ------------------------------------------------------------------

    def provision_database(self, project_id: str, db_spec: dict) -> dict:
        """
        Create a database and user for a project.

        db_spec keys:
            database_name: str
            username: str (auto-generate if not provided)
            password: str (auto-generate if not provided)
            listen_ip: str (default "127.0.0.1")
        Returns:
            {"status": True, "data": {"database_name": ..., "username": ..., "password": ...}}
            or {"status": False, "msg": "..."}
        """
        try:
            pid = int(project_id)
        except (ValueError, TypeError):
            return {"status": False, "msg": "Invalid project_id: {!r}".format(project_id)}

        # 1. Detect local installation
        install = self.detect_local_installation()
        if install is None:
            return {"status": False, "msg": "No local PostgreSQL installation detected. Please install PostgreSQL first."}

        # 2. Validate PostgreSQL is running
        if not self.is_running():
            # Attempt to start
            public.ExecShell("/etc/init.d/pgsql start 2>/dev/null")
            if not self.is_running():
                return {"status": False, "msg": "PostgreSQL is not running and could not be started."}

        # 3. Prepare parameters
        database_name = db_spec.get("database_name")
        if not database_name:
            return {"status": False, "msg": "database_name is required."}
        database_name = self._sanitize_name(database_name)

        username = db_spec.get("username") or self._generate_db_user(database_name)
        password = db_spec.get("password") or self._generate_password()
        listen_ip = db_spec.get("listen_ip", "127.0.0.1/32")

        # Validate listen_ip format
        if not re.match(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/\d{1,2}\b", listen_ip):
            return {"status": False, "msg": "Invalid listen_ip format. Expected: x.x.x.x/xx"}

        # 4. Check if database already exists for this project
        existing = self.get_project_database(str(pid))
        if existing is not None:
            return {
                "status": False,
                "msg": "Project already has a PostgreSQL database: {}".format(existing.get("database_name")),
            }

        # 5. Delegate to pgsqlModel.AddDatabase()
        try:
            args = public.dict_obj()
            args.name = database_name
            args.sid = 0  # local
            args.ps = "{} project_id={}".format(self._PROJECT_DB_PS_PREFIX, pid)
            args.pid = pid
            args.listen_ip = listen_ip

            # If the model provides username/password creation, pre-populate them
            args.username = username
            args.password = password
        except Exception:
            return {"status": False, "msg": "Failed to build request arguments."}

        # TODO: pgsqlModel.AddDatabase() auto-creates username/password via base class
        # and stores them in the databases table with pid. For project integration,
        # we need to ensure our provided username/password are used. This requires
        # either a custom variant or post-creation update.
        try:
            result = self._model().AddDatabase(args)
        except Exception as e:
            return {"status": False, "msg": "AddDatabase failed: {}".format(str(e))}

        if result is None:
            return {"status": False, "msg": "AddDatabase returned None - operation may have partially succeeded."}

        # Parse result - pgsqlModel uses public.return_message / public.success_v2 / public.fail_v2
        if isinstance(result, dict):
            status_flag = result.get("status")
            if status_flag == 1 or status_flag is True:
                pass  # success
            else:
                msg = result.get("msg", result.get("message", "Unknown error"))
                return {"status": False, "msg": str(msg)}
        # If result is a raw tuple (status, code, data) from return_message
        elif isinstance(result, tuple):
            if len(result) >= 1 and result[0] != 0:
                msg = result[-1] if len(result) >= 3 else "Unknown error"
                return {"status": False, "msg": str(msg)}

        # 6. Verify the database was created and return consistent credentials
        db_find = public.M("databases").where(
            "pid=? AND LOWER(type)=LOWER('PgSql')", (pid,)
        ).field("name,username,password").find()

        if not db_find:
            return {"status": False, "msg": "Database was created but could not be found in the databases table."}

        return {
            "status": True,
            "data": {
                "database_name": db_find["name"],
                "username": db_find["username"],
                "password": db_find["password"],
            },
        }

    def deprovision_database(self, project_id: str) -> dict:
        """
        Remove database and user created for a project.
        Looks up the database by project association in the databases table.
        """
        try:
            pid = int(project_id)
        except (ValueError, TypeError):
            return {"status": False, "msg": "Invalid project_id: {!r}".format(project_id)}

        db_find = public.M("databases").where(
            "pid=? AND LOWER(type)=LOWER('PgSql')", (pid,)
        ).field("id,name,username,password,accept,ps,addtime,type,sid,db_type,conn_config").find()

        if not db_find:
            return {"status": False, "msg": "No PostgreSQL database found for project_id={}".format(pid)}

        try:
            args = public.dict_obj()
            args.id = db_find["id"]
            args.name = db_find["name"]
            result = self._model().DeleteDatabase(args)
        except Exception as e:
            return {"status": False, "msg": "DeleteDatabase failed: {}".format(str(e))}

        if isinstance(result, dict):
            status_flag = result.get("status")
            if status_flag == 0 or status_flag is False:
                msg = result.get("msg", result.get("message", "Unknown error"))
                return {"status": False, "msg": str(msg)}
        elif isinstance(result, tuple):
            if len(result) >= 1 and result[0] != 0:
                msg = result[-1] if len(result) >= 3 else "Unknown error"
                return {"status": False, "msg": str(msg)}

        return {"status": True}

    def get_project_database(self, project_id: str) -> Optional[dict]:
        """Get the database info associated with a project. Returns None if not found."""
        try:
            pid = int(project_id)
        except (ValueError, TypeError):
            return None

        db_find = public.M("databases").where(
            "pid=? AND LOWER(type)=LOWER('PgSql')", (pid,)
        ).field("id,name,username,password,accept,ps,addtime").find()

        if not db_find:
            return None

        return {
            "database_name": db_find["name"],
            "username": db_find["username"],
            "password": db_find["password"],
            "accept": db_find.get("accept", "127.0.0.1"),
            "ps": db_find.get("ps", ""),
            "addtime": db_find.get("addtime", ""),
        }

    def list_project_databases(self) -> List[dict]:
        """List all databases that were created through the project provisioning system."""
        rows = public.M("databases").where(
            "ps LIKE ? AND LOWER(type)=LOWER('PgSql')", ("{}%".format(self._PROJECT_DB_PS_PREFIX),)
        ).field("id,pid,name,username,ps,addtime").select()

        if not rows or not isinstance(rows, list):
            return []

        results = []
        for row in rows:
            results.append({
                "database_id": row["id"],
                "project_id": row["pid"],
                "database_name": row["name"],
                "username": row["username"],
                "ps": row.get("ps", ""),
                "addtime": row.get("addtime", ""),
            })
        return results

    # ------------------------------------------------------------------
    # User Management
    # ------------------------------------------------------------------

    def create_least_privilege_user(self, db_name: str) -> dict:
        """
        Create a least-privilege database user with:
        - CONNECT on the database
        - USAGE, CREATE on schema public
        - ALL on all tables, sequences, functions in public
        - ALTER DEFAULT PRIVILEGES for new tables
        Returns: {"status": True, "data": {"username": ..., "password": ...}}
                 or {"status": False, "msg": "..."}
        """
        install = self.detect_local_installation()
        if install is None:
            return {"status": False, "msg": "No local PostgreSQL installation detected."}

        if not self.is_running():
            return {"status": False, "msg": "PostgreSQL is not running."}

        username = self._generate_db_user(db_name)
        password = self._generate_password()

        # Use the existing pgsqlModel's AddDatabase which internally calls
        # __CreateUsers and handles GRANT CONNECT, USAGE, CREATE on schema.
        try:
            from databaseModelV2.pgsqlModel import main as pgsqlModel

            get_obj = public.to_dict_obj({
                "name": db_name,
                "sid": 0,
                "db_user": username,
                "password": password,
                "listen_ip": "127.0.0.1/32",
                "ps": "Created by tomcat adapter (least-privilege user for {})".format(db_name),
            })
            model = pgsqlModel()
            result = model.AddDatabase(get_obj)
            if isinstance(result, dict) and not result.get("status", True):
                return {"status": False, "msg": "Failed to create database user: {}".format(result.get("msg", "unknown error"))}
        except Exception as e:
            return {"status": False, "msg": "Failed to create least-privilege user: {}".format(str(e))}

        return {
            "status": True,
            "data": {
                "username": username,
                "password": password,
            },
        }

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    def _get_panel_version(self) -> Optional[str]:
        """Get PostgreSQL version from panel-managed installation."""
        version_file = os.path.join(public.get_setup_path(), "pgsql", "version.pl")
        if os.path.isfile(version_file):
            v_info = public.readFile(version_file)
            if v_info:
                return v_info.strip().split(".")[0]

        # Fallback: check the binary
        psql_bin = os.path.join(self._PG_PANEL_BIN_DIR, "psql")
        if os.path.isfile(psql_bin):
            return self._get_binary_version(psql_bin)

        return None

    def _get_panel_port(self) -> int:
        """Get the configured PostgreSQL port from panel-managed installation."""
        if os.path.isfile(self._PG_CONF_FILE):
            conf = public.readFile(self._PG_CONF_FILE)
            if conf and isinstance(conf, str):
                match = re.search(r"\s*port\s*=\s*(\d+)", conf)
                if match:
                    return int(match.group(1))
        return 5432

    def _detect_distro_package(self) -> Optional[dict]:
        """Detect a distro-managed PostgreSQL installation at /usr/lib/postgresql."""
        if not os.path.isdir(self._DISTRO_BASE_DIR):
            return None

        for entry in sorted(os.listdir(self._DISTRO_BASE_DIR), reverse=True):
            entry_path = os.path.join(self._DISTRO_BASE_DIR, entry)
            if not os.path.isdir(entry_path):
                continue
            # Verify this looks like a PostgreSQL version directory
            pg_bin = os.path.join(entry_path, "bin", "postgres")
            if os.path.isfile(pg_bin):
                version = self._get_binary_version(pg_bin)
                if version:
                    return {
                        "installation_type": "distro",
                        "version": version,
                        "port": 5432,
                        "data_dir": self._try_find_data_dir(),
                        "bin_dir": os.path.join(entry_path, "bin"),
                    }

        return None

    def _try_find_data_dir(self) -> str:
        """Attempt to locate a distro PostgreSQL data directory."""
        # Common distro data locations
        candidates = [
            "/var/lib/postgresql/data",
            "/var/lib/pgsql/data",
            "/var/lib/postgresql/{}/main".format(self._try_get_version()),
        ]
        for c in candidates:
            if os.path.isdir(c) and os.path.isfile(os.path.join(c, "PG_VERSION")):
                return c
        # Fallback
        for c in candidates:
            if os.path.isdir(c):
                return c
        return "/var/lib/postgresql/data"

    def _try_get_version(self) -> str:
        """Try to determine installed version by listing distro directories."""
        if os.path.isdir(self._DISTRO_BASE_DIR):
            entries = sorted(os.listdir(self._DISTRO_BASE_DIR), reverse=True)
            for entry in entries:
                if re.match(r"^\d+(\.\d+)?$", entry):
                    return entry.split(".")[0]
        return ""

    @classmethod
    def _get_binary_version(cls, binary_path: str) -> Optional[str]:
        """Run binary --version and extract the major version number."""
        if not os.path.isfile(binary_path) or not os.access(binary_path, os.X_OK):
            return None

        result = public.ExecShell("{} --version 2>/dev/null".format(binary_path))
        if not result or len(result) < 1:
            return None

        output = result[0].strip()
        match = re.search(r"(\d+)\.(\d+)", output)
        if match:
            return match.group(1)
        return None

    @classmethod
    def _which(cls, name: str) -> Optional[str]:
        """Find an executable in PATH. Returns the full path or None."""
        result = public.ExecShell("which {} 2>/dev/null".format(name))
        if result and len(result) > 0:
            path = result[0].strip()
            if path and os.path.isfile(path):
                return path
        return None

    @classmethod
    def _sanitize_name(cls, name: str) -> str:
        """Sanitize a database name - lowercase, alphanumeric + underscores only."""
        name = name.lower().strip()
        name = re.sub(r"[^a-z0-9_]", "_", name)
        if not name:
            name = "project_db"
        return name

    @classmethod
    def _generate_db_user(cls, db_name: str) -> str:
        """Generate a username based on the database name."""
        # Username max 63 chars in PostgreSQL
        base = re.sub(r"[^a-z0-9_]", "", db_name.lower())[:47]
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        return "{}_{}".format(base, suffix)

    @classmethod
    def _generate_password(cls) -> str:
        """Generate a cryptographically secure random password."""
        return "".join(
            random.SystemRandom().choice(string.ascii_letters + string.digits + "!@#$%^&*")
            for _ in range(20)
        )
