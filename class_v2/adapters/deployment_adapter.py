# coding: utf-8
# +-------------------------------------------------------------------
# | aaPanel
# +-------------------------------------------------------------------
# | Copyright (c) 2015-2099 aaPanel(www.aapanel.com) All rights reserved.
# +-------------------------------------------------------------------
# | Author: aaPanel
# +-------------------------------------------------------------------
# ------------------------------
# WAR Deployment Adapter for Tomcat/Java projects
# ------------------------------

import os
import sys
import time
import hashlib
import json
import shlex
import shutil
import urllib.request
import urllib.error
import zipfile
from typing import Dict, List, Optional, Tuple, Any

import public
from public.exceptions import HintException


class DeploymentAdapter:
    """
    Standardized Java artifact deployment adapter.

    Handles artifact intake, release storage, activation strategy,
    health checks, and rollback for Tomcat projects.

    Release directory structure:
        {project_path}/releases/{release_id}/      <-- extracted artifact
        {project_path}/releases/current             <-- symlink to active release
        {project_path}/releases/previous            <-- symlink to previous release
        {project_path}/releases/releases.json       <-- release metadata
    """

    MAX_UPLOAD_SIZE = 200 * 1024 * 1024  # 200MB

    ACCEPTED_SOURCES = frozenset({"browser_upload", "server_path", "url", "exploded_dir"})

    DEFAULT_HEALTH_CHECK_PATH = "/"
    DEFAULT_HEALTH_CHECK_TIMEOUT = 5
    HEALTH_CHECK_RETRIES = 3
    HEALTH_CHECK_INTERVAL = 2  # seconds
    MAX_RELEASES_KEEP = 5

    TOMCAT_WEB_BASE = "/www/server/bt_tomcat_web"
    TOMCAT_BUILTIN_BASE = "/usr/local/bttomcat"

    def __init__(self):
        self._tomcat_web_base = self.TOMCAT_WEB_BASE
        self._tomcat_builtin_base = self.TOMCAT_BUILTIN_BASE

    def _get_release_dir(self, project_path: str) -> str:
        return os.path.join(project_path, "releases")

    def _get_releases_metadata(self, project_path: str) -> dict:
        releases_file = os.path.join(self._get_release_dir(project_path), "releases.json")
        if not os.path.exists(releases_file):
            return {"releases": []}
        try:
            data = public.readFile(releases_file)
            if not data or not isinstance(data, str):
                return {"releases": []}
            return json.loads(data)
        except (json.JSONDecodeError, ValueError, Exception):
            return {"releases": []}

    def _save_releases_metadata(self, project_path: str, metadata: dict) -> None:
        release_dir = self._get_release_dir(project_path)
        os.makedirs(release_dir, exist_ok=True)
        releases_file = os.path.join(release_dir, "releases.json")
        public.writeFile(releases_file, json.dumps(metadata, indent=2))

    def _compute_checksum(self, file_path: str) -> str:
        sha256 = hashlib.sha256()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha256.update(chunk)
        return sha256.hexdigest()

    def _generate_release_id(self) -> str:
        return "{}-{}".format(
            time.strftime("%Y%m%d%H%M%S"),
            hashlib.md5(str(time.time()).encode()).hexdigest()[:8],
        )

    def _get_active_release(self, project_path: str) -> Optional[dict]:
        metadata = self._get_releases_metadata(project_path)
        for release in metadata.get("releases", []):
            if release.get("status") == "active":
                return release
        return None

    def _get_previous_release(self, project_path: str) -> Optional[dict]:
        metadata = self._get_releases_metadata(project_path)
        for release in metadata.get("releases", []):
            if release.get("status") == "previous":
                return release
        return None

    def _get_release_by_id(self, project_path: str, release_id: str) -> Optional[dict]:
        metadata = self._get_releases_metadata(project_path)
        for release in metadata.get("releases", []):
            if release.get("release_id") == release_id:
                return release
        return None

    def _mark_release_status(self, project_path: str, release_id: str, status: str) -> None:
        metadata = self._get_releases_metadata(project_path)
        for release in metadata.get("releases", []):
            if release.get("release_id") == release_id:
                release["status"] = status
        self._save_releases_metadata(project_path, metadata)

    def _add_release_entry(self, project_path: str, release_entry: dict) -> None:
        metadata = self._get_releases_metadata(project_path)
        metadata["releases"].insert(0, release_entry)
        self._save_releases_metadata(project_path, metadata)

    def _get_project_nginx_conf(self, project_name: str) -> str:
        return os.path.join(public.get_panel_path(), "vhost", "nginx", "java_{}.conf".format(project_name))

    def _set_project_nginx_proxy_port(self, project_name: str, port: int, domain: str = None) -> None:
        conf_file = self._get_project_nginx_conf(project_name)
        if not os.path.exists(conf_file):
            raise HintException("Nginx configuration not found for project: {}".format(project_name))

        conf = public.readFile(conf_file)
        if not conf:
            raise HintException("Empty nginx config for project: {}".format(project_name))

        new_conf = self._update_proxy_pass(conf, port)
        public.writeFile(conf_file, new_conf)
        public.serviceReload()

    def _update_proxy_pass(self, conf_text: str, new_port: int) -> str:
        import re
        pattern = r"(proxy_pass\s+)(https?://[^:\s]+:)(\d+)(.*?;)"
        replacement = r"\g<1>\g<2>{}".format(new_port) + r"\g<4>"
        updated = re.sub(pattern, replacement, conf_text)

        if updated == conf_text:
            pattern2 = r"(proxy_pass\s+)(https?://[^:;\s]+)(;?\s*)$"
            replacement2 = r"\g<1>\g<2>:{};".format(new_port)
            updated = re.sub(pattern2, replacement2, conf_text, flags=re.MULTILINE)

        return updated

    def _get_tomcat_webapps(self, tomcat_home: str) -> str:
        return os.path.join(tomcat_home, "webapps")

    def _get_tomcat_for_project(self, project_config: dict) -> str:
        java_type = project_config.get("java_type", "")
        if java_type == "neizhi":
            version = project_config.get("tomcat_version", "9")
            return os.path.join(self._tomcat_builtin_base, "tomcat{}".format(version))
        elif java_type == "duli":
            project_name = project_config.get("project_name", "")
            return os.path.join(self._tomcat_web_base, project_name)
        raise HintException("Unsupported java_type for WAR deployment: {}".format(java_type))

    def _validate_war(self, file_path: str) -> Tuple[bool, str]:
        if not os.path.exists(file_path):
            return False, "File does not exist: {}".format(file_path)

        if not file_path.lower().endswith(".war"):
            return False, "File must have .war extension: {}".format(file_path)

        file_size = os.path.getsize(file_path)
        if file_size > self.MAX_UPLOAD_SIZE:
            return False, "WAR file exceeds maximum size of {} bytes: {} bytes".format(
                self.MAX_UPLOAD_SIZE, file_size
            )

        try:
            with zipfile.ZipFile(file_path, "r") as zf:
                namelist = zf.namelist()
                has_web_inf = any(
                    n.startswith("WEB-INF/web.xml") or n.startswith("WEB-INF/") and n.endswith("/")
                    for n in namelist
                )
                has_manifest = any(
                    n == "META-INF/MANIFEST.MF" for n in namelist
                )
                if not has_web_inf and not has_manifest:
                    return False, "WAR file is not a valid Java web application: missing WEB-INF or META-INF/MANIFEST.MF"

                if zf.testzip() is not None:
                    return False, "WAR file is corrupted: ZIP integrity check failed"

                return True, ""
        except zipfile.BadZipFile:
            return False, "File is not a valid ZIP archive"
        except Exception as e:
            return False, "Failed to validate WAR file: {}".format(str(e))

    def intake_artifact(self, source_type: str, source_config: dict, project_path: str) -> dict:
        if source_type not in self.ACCEPTED_SOURCES:
            return {"status": False, "msg": "Unsupported source type: {}. Accepted: {}".format(
                source_type, ", ".join(sorted(self.ACCEPTED_SOURCES))
            )}

        release_id = self._generate_release_id()
        release_dir = os.path.join(self._get_release_dir(project_path), release_id)
        war_path = os.path.join(release_dir, "app.war")
        os.makedirs(release_dir, exist_ok=True)

        try:
            if source_type == "browser_upload":
                file_data = source_config.get("file_data")
                filename = source_config.get("filename", "uploaded.war")
                if not file_data:
                    raise HintException("No file data provided for browser upload")

                if not filename.lower().endswith(".war"):
                    raise HintException("Uploaded file must be a .war file")

                with open(war_path, "wb") as f:
                    if isinstance(file_data, bytes):
                        f.write(file_data)
                    else:
                        raise HintException("Invalid file data type")

            elif source_type == "server_path":
                src_path = source_config.get("path", "")
                if not src_path:
                    raise HintException("No server path provided")
                if not os.path.exists(src_path):
                    raise HintException("Server path does not exist: {}".format(src_path))

                if os.path.isdir(src_path):
                    raise HintException("Server path must be a file, not a directory: {}".format(src_path))

                shutil.copy2(src_path, war_path)

            elif source_type == "url":
                url = source_config.get("url", "")
                if not url:
                    raise HintException("No URL provided")

                if not self._is_safe_url(url):
                    raise HintException("Unsafe URL: deployment from private/internal addresses is not allowed.")

                public.HttpGet(url, war_path)

                if not os.path.exists(war_path) or os.path.getsize(war_path) == 0:
                    raise HintException("Failed to download WAR from URL: {}".format(url))

            elif source_type == "exploded_dir":
                dir_path = source_config.get("path", "")
                if not dir_path:
                    raise HintException("No directory path provided")
                if not os.path.exists(dir_path):
                    raise HintException("Directory does not exist: {}".format(dir_path))
                if not os.path.isdir(dir_path):
                    raise HintException("Path must be a directory: {}".format(dir_path))

                exploded_dir = os.path.join(release_dir, "exploded")
                shutil.copytree(dir_path, exploded_dir)

                war_path = self._package_exploded_dir(exploded_dir, release_dir)

            else:
                raise HintException("Unsupported source type: {}".format(source_type))

            valid, error_msg = self._validate_war(war_path)
            if not valid:
                shutil.rmtree(release_dir, ignore_errors=True)
                return {"status": False, "msg": error_msg}

            checksum = self._compute_checksum(war_path)
            file_size = os.path.getsize(war_path)

            if source_type == "exploded_dir":
                self._extract_war(war_path, release_dir, remove_war=True)
            else:
                self._extract_war(war_path, release_dir, remove_war=False)

            release_entry = {
                "release_id": release_id,
                "version": release_id,
                "checksum": checksum,
                "size": file_size,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "status": "archived",
                "source_type": source_type,
                "note": "Prepared from {}".format(source_type),
            }
            self._add_release_entry(project_path, release_entry)

            return {"status": True, "data": {"release_id": release_id, "checksum": checksum, "size": file_size}}

        except HintException:
            raise
        except Exception as e:
            shutil.rmtree(release_dir, ignore_errors=True)
            return {"status": False, "msg": "Artifact intake failed: {}".format(str(e))}

    def _extract_war(self, war_path: str, dest_dir: str, remove_war: bool = False) -> None:
        extract_dir = os.path.join(dest_dir, "extracted")
        os.makedirs(extract_dir, exist_ok=True)
        result = public.ExecShell("unzip -o {} -d {}".format(shlex.quote(war_path), shlex.quote(extract_dir)))
        if result[1] and ("error" in result[1].lower() or "cannot find" in result[1].lower()):
            raise HintException("Failed to extract WAR file: {}".format(result[1]))
        if remove_war:
            os.remove(war_path)

    def _package_exploded_dir(self, dir_path: str, dest_dir: str) -> str:
        war_path = os.path.join(dest_dir, "app.war")
        result = public.ExecShell(
            "cd {} && jar cf {} *".format(shlex.quote(dir_path), shlex.quote(war_path))
        )
        if result[1] and "error" in result[1].lower():
            raise HintException("Failed to package exploded directory as WAR: {}".format(result[1]))
        shutil.rmtree(dir_path, ignore_errors=True)
        return war_path

    def _is_safe_url(self, url: str) -> bool:
        """Validate URL is safe for fetching: http/https scheme only, no private/reserved IPs."""
        import socket
        import re
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
        except Exception:
            return False

        if parsed.scheme not in ("http", "https"):
            return False

        hostname = parsed.hostname
        if not hostname:
            return False

        try:
            ip = socket.gethostbyname(hostname)
        except Exception:
            return True

        if ip.startswith("127.") or ip.startswith("10.") or ip.startswith("0."):
            return False
        if ip.startswith("172.") and 16 <= int(ip.split(".")[1]) <= 31:
            return False
        if ip.startswith("192.168."):
            return False
        if ip.startswith("169.254."):
            return False

        return True

    def activate_release(self, project_id: str, release_id: str, mode: str, project_config: dict) -> dict:
        project_name = project_config.get("project_name", "")
        project_path = project_config.get("path", "")
        if not project_path:
            project_path = os.path.join(self._tomcat_web_base, project_name)
        if not project_name:
            raise HintException("Project name is missing from project_config")

        release = self._get_release_by_id(project_path, release_id)
        if not release:
            return {"status": False, "msg": "Release not found: {}".format(release_id)}

        if mode not in ("shared", "isolated"):
            return {"status": False, "msg": "Unsupported activation mode: {}. Use 'shared' or 'isolated'.".format(mode)}

        try:
            release_dir = os.path.join(self._get_release_dir(project_path), release_id)
            extract_dir = os.path.join(release_dir, "extracted")

            if mode == "shared":
                result = self._activate_shared(project_id, release_id, release_dir, extract_dir, project_config)
            else:
                result = self._activate_isolated(project_id, release_id, release_dir, extract_dir, project_config)

            if result.get("status"):
                previous_active = self._get_active_release(project_path)
                if previous_active:
                    self._mark_release_status(project_path, previous_active["release_id"], "previous")
                self._mark_release_status(project_path, release_id, "active")

            return result

        except HintException:
            raise
        except Exception as e:
            return {"status": False, "msg": "Activation failed: {}".format(str(e))}

    def _activate_shared(
        self, project_id: str, release_id: str, release_dir: str, extract_dir: str, project_config: dict
    ) -> dict:
        project_name = project_config.get("project_name", "")
        java_type = project_config.get("java_type", "duli")

        tomcat_home = self._get_tomcat_for_project(project_config)
        webapps_dir = self._get_tomcat_webapps(tomcat_home)
        project_webapp = os.path.join(webapps_dir, project_name)

        previous_webapp_backup = os.path.join(webapps_dir, "{}_backup".format(project_name))
        if os.path.exists(previous_webapp_backup):
            shutil.rmtree(previous_webapp_backup, ignore_errors=True)

        if os.path.exists(project_webapp):
            shutil.move(project_webapp, previous_webapp_backup)

        try:
            shutil.copytree(extract_dir, project_webapp)

            context_war = os.path.join(webapps_dir, "{}.war".format(project_name))
            if not os.path.exists(context_war):
                war_file = os.path.join(release_dir, "app.war")
                if os.path.exists(war_file):
                    shutil.copy2(war_file, context_war)

            if java_type == "duli":
                self._reload_tomcat_context(project_name)
            elif java_type == "neizhi":
                version = project_config.get("tomcat_version", "9")
                public.ExecShell("/etc/init.d/bttomcat{} restart".format(version))

            health_path = project_config.get("health_check_path", self.DEFAULT_HEALTH_CHECK_PATH)
            port = project_config.get("port", 8080)
            health_result = self.health_check(project_id, health_path, port)

            if not health_result.get("status"):
                if os.path.exists(previous_webapp_backup):
                    shutil.rmtree(project_webapp, ignore_errors=True)
                    shutil.move(previous_webapp_backup, project_webapp)
                return {"status": False, "msg": "Health check failed after activation: {}".format(
                    health_result.get("msg", "unknown error")
                )}

            health_status = "healthy" if health_result.get("data", {}).get("code") in (200, 301, 302) else "unknown"

            if os.path.exists(previous_webapp_backup):
                shutil.rmtree(previous_webapp_backup, ignore_errors=True)

            return {"status": True, "data": {"release_id": release_id, "health_status": health_status}}

        except Exception:
            if os.path.exists(previous_webapp_backup):
                if os.path.exists(project_webapp):
                    shutil.rmtree(project_webapp, ignore_errors=True)
                shutil.move(previous_webapp_backup, project_webapp)
            raise

    def _activate_isolated(
        self, project_id: str, release_id: str, release_dir: str, extract_dir: str, project_config: dict
    ) -> dict:
        project_name = project_config.get("project_name", "")
        current_port = project_config.get("port", 8080)
        alternate_port = self._find_alternate_port(current_port)

        current_project_web = os.path.join(self._tomcat_web_base, project_name)

        alternate_project_dir = os.path.join(self._tomcat_web_base, "{}_alt".format(project_name))

        if os.path.exists(alternate_project_dir):
            shutil.rmtree(alternate_project_dir, ignore_errors=True)

        try:
            shutil.copytree(current_project_web, alternate_project_dir)

            alternate_webapps = os.path.join(alternate_project_dir, "webapps")
            alternate_app = os.path.join(alternate_webapps, project_name)
            if not os.path.exists(alternate_webapps):
                os.makedirs(alternate_webapps, exist_ok=True)

            if os.path.exists(alternate_app):
                shutil.rmtree(alternate_app, ignore_errors=True)

            shutil.copytree(extract_dir, alternate_app)

            self._update_tomcat_port(alternate_project_dir, alternate_port)

            public.ExecShell(
                "{}/bin/startup.sh".format(shlex.quote(alternate_project_dir))
            )

            self._wait_for_port(alternate_port, timeout=15)

            health_path = project_config.get("health_check_path", self.DEFAULT_HEALTH_CHECK_PATH)
            health_result = self.health_check(project_id, health_path, alternate_port)

            if not health_result.get("status"):
                self._stop_isolated_instance(alternate_project_dir)
                shutil.rmtree(alternate_project_dir, ignore_errors=True)
                return {"status": False, "msg": "Health check failed on alternate port {}: {}".format(
                    alternate_port, health_result.get("msg", "unknown error")
                )}

            project_domains = project_config.get("domains", [])
            if project_domains:
                for domain in project_domains:
                    self._set_project_nginx_proxy_port(
                        project_name, alternate_port,
                        domain=domain if isinstance(domain, str) else domain
                    )

            project_config["port"] = alternate_port
            public.M("sites").where("name=?", (project_name,)).update(
                {"project_config": json.dumps(project_config)}
            )
            self._update_primary_tomcat_port(current_project_web, alternate_port)

            health_status = "healthy" if health_result.get("data", {}).get("code") in (200, 301, 302) else "unknown"

            return {"status": True, "data": {"release_id": release_id, "health_status": health_status}}

        except Exception:
            shutil.rmtree(alternate_project_dir, ignore_errors=True)
            raise

    def _find_alternate_port(self, current_port: int) -> int:
        for offset in (1, -1, 2, -2, 3, -3, 4, -4, 5, -5):
            alt = current_port + offset
            if 1024 <= alt <= 65535 and public.checkPort(str(alt)):
                return alt
        alt = current_port + 1
        if alt > 65535:
            alt = current_port - 1
        return alt

    def _update_tomcat_port(self, tomcat_home: str, new_port: int) -> None:
        server_xml = os.path.join(tomcat_home, "conf", "server.xml")
        if not os.path.exists(server_xml):
            raise HintException("server.xml not found in tomcat home: {}".format(tomcat_home))

        import re
        content = public.readFile(server_xml)
        content = re.sub(
            r'(<Connector[^>]*port=")[^"]*(")',
            r"\g<1>{}".format(new_port) + r"\g<2>",
            content,
            count=1
        )
        content = re.sub(
            r'(<Server[^>]*port=")[^"]*(")',
            r"\g<1>{}".format(new_port + 8000) + r"\g<2>",
            content,
            count=1
        )
        public.writeFile(server_xml, content)

    def _update_primary_tomcat_port(self, tomcat_home: str, new_port: int) -> None:
        server_xml = os.path.join(tomcat_home, "conf", "server.xml")
        if os.path.exists(server_xml):
            import re
            content = public.readFile(server_xml)
            content = re.sub(
                r'(<Connector[^>]*port=")[^"]*(")',
                r"\g<1>{}".format(new_port) + r"\g<2>",
                content,
                count=1
            )
            public.writeFile(server_xml, content)

    def _wait_for_port(self, port: int, timeout: int = 15) -> None:
        """Poll until the port is accepting connections or timeout expires."""
        import socket
        start = time.time()
        while (time.time() - start) < timeout:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(1)
                result = sock.connect_ex(('127.0.0.1', port))
                sock.close()
                if result == 0:
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise HintException("Timed out waiting for port {} ({} s)".format(port, timeout))

    def _stop_isolated_instance(self, tomcat_home: str) -> None:
        shutdown_script = os.path.join(tomcat_home, "bin", "shutdown.sh")
        if os.path.exists(shutdown_script):
            public.ExecShell("{} 2>/dev/null".format(shlex.quote(shutdown_script)))
            time.sleep(2)

    def _reload_tomcat_context(self, project_name: str) -> None:
        project_dir = os.path.join(self._tomcat_web_base, project_name)
        web_xml = os.path.join(project_dir, "webapps", project_name, "WEB-INF", "web.xml")
        if os.path.exists(web_xml):
            os.utime(web_xml, None)

    def health_check(self, project_id: str, check_path: str, port: int = None) -> dict:
        url = "http://127.0.0.1"
        if port:
            url = "{}:{}".format(url, port)
        if check_path:
            if not check_path.startswith("/"):
                check_path = "/" + check_path
            url = url + check_path

        for attempt in range(self.HEALTH_CHECK_RETRIES):
            try:
                start_time = time.time()
                req = urllib.request.Request(url, method="HEAD")
                response = urllib.request.urlopen(req, timeout=self.DEFAULT_HEALTH_CHECK_TIMEOUT)
                elapsed_ms = int((time.time() - start_time) * 1000)
                return {
                    "status": True,
                    "data": {"code": response.getcode(), "time_ms": elapsed_ms},
                }
            except urllib.error.HTTPError as e:
                elapsed_ms = int((time.time() - start_time) * 1000)
                if e.code >= 400:
                    return {
                        "status": False,
                        "msg": "Health check failed: HTTP {} returned".format(e.code),
                    }
                return {
                    "status": True,
                    "data": {"code": e.code, "time_ms": elapsed_ms},
                }
            except urllib.error.URLError as e:
                if attempt < self.HEALTH_CHECK_RETRIES - 1:
                    time.sleep(self.HEALTH_CHECK_INTERVAL)
                    continue
                return {"status": False, "msg": "Health check failed: {}".format(str(e.reason))}
            except (ConnectionRefusedError, OSError) as e:
                if attempt < self.HEALTH_CHECK_RETRIES - 1:
                    time.sleep(self.HEALTH_CHECK_INTERVAL)
                    continue
                return {"status": False, "msg": "Health check failed: connection refused"}
            except Exception as e:
                return {"status": False, "msg": "Health check error: {}".format(str(e))}

        return {"status": False, "msg": "Health check failed after {} retries".format(self.HEALTH_CHECK_RETRIES)}

    def rollback_release(
        self, project_id: str, target_release_id: str = None, project_config: dict = None
    ) -> dict:
        if not project_config:
            return {"status": False, "msg": "project_config is required for rollback"}

        project_name = project_config.get("project_name", "")
        project_path = project_config.get("path", "")
        if not project_path:
            project_path = os.path.join(self._tomcat_web_base, project_name)

        if target_release_id is None:
            prev_release = self._get_previous_release(project_path)
            if not prev_release:
                return {"status": False, "msg": "No previous release available for rollback"}
            target_release_id = prev_release["release_id"]
        else:
            target_release = self._get_release_by_id(project_path, target_release_id)
            if not target_release:
                return {"status": False, "msg": "Target release not found: {}".format(target_release_id)}

        active_release = self._get_active_release(project_path)
        if active_release and active_release["release_id"] == target_release_id:
            return {"status": False, "msg": "Cannot rollback to the currently active release"}

        try:
            mode = project_config.get("deployment_mode", "shared")
            activate_result = self.activate_release(project_id, target_release_id, mode, project_config)

            if activate_result.get("status"):
                return {
                    "status": True,
                    "data": {
                        "release_id": target_release_id,
                        "message": "Successfully rolled back to release {}".format(target_release_id),
                    },
                }
            return activate_result

        except HintException:
            raise
        except Exception as e:
            return {"status": False, "msg": "Rollback failed: {}".format(str(e))}

    def list_releases(self, project_id: str, project_path: str) -> dict:
        try:
            if not os.path.exists(project_path):
                return {"status": False, "msg": "Project path does not exist: {}".format(project_path)}

            metadata = self._get_releases_metadata(project_path)
            releases = metadata.get("releases", [])

            display_data = []
            for release in releases:
                display_data.append({
                    "release_id": release.get("release_id", ""),
                    "version": release.get("version", ""),
                    "checksum": release.get("checksum", ""),
                    "size": release.get("size", 0),
                    "created_at": release.get("created_at", ""),
                    "status": release.get("status", "archived"),
                    "source_type": release.get("source_type", "unknown"),
                    "note": release.get("note", ""),
                })

            return {"status": True, "data": display_data}

        except Exception as e:
            return {"status": False, "msg": "Failed to list releases: {}".format(str(e))}

    def cleanup_old_releases(self, project_id: str, project_path: str, keep: int = None) -> dict:
        if keep is None:
            keep = self.MAX_RELEASES_KEEP

        if keep < 1:
            return {"status": False, "msg": "Must keep at least 1 release"}

        try:
            metadata = self._get_releases_metadata(project_path)
            releases = metadata.get("releases", [])

            active_release = self._get_active_release(project_path)
            previous_release = self._get_previous_release(project_path)

            protected_ids = set()
            if active_release:
                protected_ids.add(active_release["release_id"])
            if previous_release:
                protected_ids.add(previous_release["release_id"])

            sorted_releases = sorted(releases, key=lambda r: r.get("created_at", ""), reverse=True)
            to_keep = []
            to_remove = []

            for release in sorted_releases:
                rid = release.get("release_id")
                if rid in protected_ids:
                    to_keep.append(release)
                elif len(to_keep) < keep:
                    to_keep.append(release)
                else:
                    to_remove.append(release)

            removed_count = 0
            release_base = self._get_release_dir(project_path)
            for release in to_remove:
                rid = release.get("release_id")
                release_dir = os.path.join(release_base, rid)
                if os.path.exists(release_dir):
                    shutil.rmtree(release_dir, ignore_errors=True)
                removed_count += 1

            remaining = [r for r in releases if r not in to_remove]
            metadata["releases"] = remaining
            self._save_releases_metadata(project_path, metadata)

            return {"status": True, "data": {"removed": removed_count, "kept": len(remaining)}}

        except Exception as e:
            return {"status": False, "msg": "Cleanup failed: {}".format(str(e))}

    def get_deployment_strategy(self, mode: str) -> dict:
        strategies = {
            "shared": {
                "name": "Shared Deployment",
                "description": (
                    "Deploy artifact to existing Tomcat container at context level. "
                    "No full Tomcat restart required - only the context is reloaded. "
                    "Previous artifact is backed up for rollback. "
                    "Suitable for staging and low-traffic environments."
                ),
                "restart_required": "context_reload",
                "downtime": "minimal (< 5 seconds)",
                "rollback_strategy": "Previous context restored from backup.",
            },
            "isolated": {
                "name": "Isolated Blue/Green Deployment",
                "description": (
                    "Deploy artifact to a new isolated Tomcat instance on an alternate port. "
                    "New instance is started and health-checked. "
                    "Nginx upstream is switched to the new instance after verification. "
                    "Old instance continues running until cutover succeeds. "
                    "Provides zero-downtime deployments suitable for production."
                ),
                "restart_required": "new_instance_startup",
                "downtime": "zero",
                "rollback_strategy": "Nginx upstream switched back to original instance.",
            },
        }

        if mode in strategies:
            return {"status": True, "data": strategies[mode]}

        return {
            "status": False,
            "msg": "Unknown deployment mode: {}. Supported modes: shared, isolated".format(mode),
        }
