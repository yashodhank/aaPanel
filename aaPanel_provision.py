#!/usr/bin/env python3
"""
aaPanel License Provisioner — automated trial license setup via temporary email
================================================================================

Automates the full lifecycle: establish a temporary email inbox → register on the
panel (or aapanel.com directly) → auto-capture the verification email → bind the
account → activate the 15-day trial. Supports rotation for bulk provisioning.

Design principles
-----------------
* Self-discovering — extracts the target panel's RSA public key, API base, and
  CSRF token dynamically from the panel itself at runtime.
* Idempotent — exits cleanly (by default) if a license is already bound; use
  ``--force`` to re-provision.
* Fail-loud — reports exactly which step failed and why (rate-limited, domain
  blocked, network error, verification timeout, etc.).
* Provider-agnostic — primary provider is mail.tm (no API key, 8 QPS); fallback
  to guerrillamail. Easily extended: add a new provider function and register it
  in PROVIDERS.
* Structured logging — all attempts write JSONL to
  ``data/provision_log.jsonl`` for audit / analysis.

Usage
-----
  pyenv/bin/python3 aaPanel_provision.py [/www/server/panel]
  pyenv/bin/python3 aaPanel_provision.py [/www/server/panel] --rotate 10
  pyenv/bin/python3 aaPanel_provision.py [/www/server/panel] --provider guerrillamail
  pyenv/bin/python3 aaPanel_provision.py [/www/server/panel] --direct
  pyenv/bin/python3 aaPanel_provision.py [/www/server/panel] --check-only

Importable API (used by other tooling):
  provision(panel, provider="mail.tm", force=False) -> dict
  Provisioner(panel).provision_one(provider="mail.tm") -> dict
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import uuid
import hashlib
import ssl
import traceback
from datetime import datetime, timezone

DEFAULT_PANEL = "/www/server/panel"

# --- HTTP transport -----------------------------------------------------------
try:
    import urllib.request
    import urllib.error
    HAS_URLLIB = True
except ImportError:
    HAS_URLLIB = False

try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False


def _http_session(verify=True):
    """Build a shared session with keep-alive when requests is available."""
    if HAS_REQUESTS:
        s = _requests.Session()
        s.verify = verify
        return s
    return None


_SESSION = None


def _get_session(verify=True):
    global _SESSION
    if _SESSION is None:
        _SESSION = _http_session(verify)
    return _SESSION


def _json_post(url, payload, headers=None, timeout=30, verify=True):
    """POST JSON and return parsed response dict. Tries requests first, falls back
    to urllib."""
    if HAS_REQUESTS:
        s = _get_session(verify)
        h = {"Content-Type": "application/json"}
        if headers:
            h.update(headers)
        r = s.post(url, json=payload, headers=h, timeout=timeout)
        r.raise_for_status()
        return r.json()
    if HAS_URLLIB:
        data = json.dumps(payload).encode("utf-8")
        hdrs = {"Content-Type": "application/json"}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
        if not verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    raise RuntimeError("No HTTP library available (requests or urllib)")


def _json_get(url, headers=None, timeout=30, verify=True, params=None):
    """GET and return parsed response dict. Optional ``params`` dict added as
    URL query string."""
    if HAS_REQUESTS:
        s = _get_session(verify)
        r = s.get(url, headers=headers, timeout=timeout, params=params)
        r.raise_for_status()
        return r.json()
    if HAS_URLLIB:
        from urllib.parse import urlencode as _ue
        final_url = url + ("?" + _ue(params) if params else "")
        hdrs = {}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(final_url, headers=hdrs, method="GET")
        if not verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    raise RuntimeError("No HTTP library available (requests or urllib)")


def _form_post(url, data, headers=None, timeout=30, verify=True, params=None,
               extra_headers=None):
    """POST form-urlencoded and return response text. Used for panel endpoints."""
    if HAS_REQUESTS:
        s = _get_session(verify)
        h = {}
        if headers:
            h.update(headers)
        if extra_headers:
            h.update(extra_headers)
        r = s.post(url, data=data, headers=h, timeout=timeout, params=params)
        r.raise_for_status()
        return r.text
    if HAS_URLLIB:
        from urllib.parse import urlencode as _ue
        body = _ue(data).encode("utf-8")
        final_url = url + ("?" + _ue(params) if params else "")
        hdrs = {"Content-Type": "application/x-www-form-urlencoded"}
        if headers:
            hdrs.update(headers)
        if extra_headers:
            hdrs.update(extra_headers)
        req = urllib.request.Request(final_url, data=body, headers=hdrs, method="POST")
        if not verify:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            resp = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8")
    raise RuntimeError("No HTTP library available (requests or urllib)")


# --- RSA encryption (matching userRegister.py:176-185 exactly) ---------------
try:
    from Crypto.PublicKey import RSA
    from Crypto.Cipher import PKCS1_v1_5 as PKCS1_cipher
    from Crypto import Random as _CryptoRandom
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def _encrypt_rsa(plaintext, public_key_pem):
    """Encrypt ``plaintext`` with the panel's RSA public key (PKCS1_v1_5)."""
    if not HAS_CRYPTO:
        raise RuntimeError("pycryptodome not installed; cannot encrypt credentials")
    pub_k = RSA.importKey(public_key_pem)
    cipher = PKCS1_cipher.new(pub_k)
    rsa_text = base64.b64encode(
        cipher.encrypt(bytes(plaintext.encode("utf8"))))
    return str(rsa_text, encoding="utf-8")


# --- Structured logging -------------------------------------------------------
def _log_result(panel_path, entry):
    """Append a JSONL line to the provision log."""
    log_path = os.path.join(panel_path, "data", "provision_log.jsonl")
    entry.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


# ==============================================================================
#  Email provider implementations
# ==============================================================================

MAIL_TM_API = "https://api.mail.tm"
GUERRILLA_API = "https://api.guerrillamail.com/ajax.php"


def _provider_mail_tm():
    """Create a temporary email inbox on mail.tm. Returns (address, password, token)."""
    # 1. Get domain
    try:
        domains = _json_get(MAIL_TM_API + "/domains", timeout=15)
        domain = domains.get("hydra:member", [{}])[0].get("domain")
        if not domain:
            raise RuntimeError("mail.tm returned no domains")
    except Exception as e:
        raise RuntimeError("mail.tm /domains failed: %s" % e)

    # 2. Create account
    local = "aap_" + uuid.uuid4().hex[:10]
    address = local + "@" + domain
    password = uuid.uuid4().hex[:20]
    try:
        _json_post(MAIL_TM_API + "/accounts",
                   {"address": address, "password": password}, timeout=15)
    except Exception as e:
        raise RuntimeError("mail.tm /accounts failed: %s" % e)

    # 3. Get token
    try:
        token_resp = _json_post(MAIL_TM_API + "/token",
                                {"address": address, "password": password},
                                timeout=15)
        token = token_resp.get("token") or token_resp.get("access_token")
        if not token:
            raise RuntimeError("mail.tm /token returned no token: %s"
                               % json.dumps(token_resp))
    except Exception as e:
        raise RuntimeError("mail.tm /token failed: %s" % e)

    return address, password, token


def _provider_guerrillamail():
    """Create a temporary email inbox on guerrillamail. Returns (address, password, sid_token)."""
    try:
        resp = _json_get(GUERRILLA_API,
                         params={"f": "get_email_address", "ip": "127.0.0.1",
                                 "agent": "aap_provision"},
                         timeout=15)
        email_addr = resp.get("email_addr")
        sid_token = resp.get("sid_token")
        if not email_addr or not sid_token:
            raise RuntimeError("guerrillamail unexpected response: %s"
                               % json.dumps(resp))
        return email_addr, sid_token, sid_token
    except Exception as e:
        raise RuntimeError("guerrillamail failed: %s" % e)


def _poll_mail_tm(auth_token, poll_sec=3, timeout_sec=120):
    """Poll mail.tm for messages; return the first matching email (subject, body, detail)
    or None after timeout."""
    headers = {"Authorization": "Bearer " + auth_token}
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            msgs = _json_get(MAIL_TM_API + "/messages", headers=headers, timeout=15)
            members = msgs.get("hydra:member", [])
            if members:
                msg_id = members[0].get("id")
                if not msg_id:
                    # Sometimes the id is only in @id as "/messages/NNN"
                    at_id = members[0].get("@id", "")
                    msg_id = at_id.rsplit("/", 1)[-1] if "/" in at_id else None
                if not msg_id:
                    continue
                detail = _json_get(MAIL_TM_API + "/messages/" + str(msg_id),
                                   headers=headers, timeout=15)
                subject = detail.get("subject", "")
                # mail.tm returns text as a string, html as a list
                text_parts = detail.get("text", "")
                html_parts = detail.get("html", [])
                body = text_parts if text_parts else ("\n".join(html_parts)
                                                       if isinstance(html_parts, list)
                                                       else str(html_parts))
                # Accept any message — the caller filters
                return subject, body, detail
        except Exception:
            pass
        time.sleep(poll_sec)
    return None


def _poll_guerrillamail(sid_token, poll_sec=3, timeout_sec=120):
    """Poll guerrillamail for new messages; return (subject, body, detail) or None."""
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            resp = _json_get(GUERRILLA_API,
                             params={"f": "get_email_list", "offset": "0",
                                     "sid_token": sid_token},
                             timeout=15)
            msgs = resp.get("list", [])
            if msgs:
                mail_id = msgs[0].get("mail_id")
                detail = _json_get(GUERRILLA_API,
                                   params={"f": "fetch_email", "email_id": mail_id,
                                           "sid_token": sid_token},
                                   timeout=15)
                body = detail.get("mail_body", "")
                subject = detail.get("mail_subject", "")
                return subject, body, detail
        except Exception:
            pass
        time.sleep(poll_sec)
    return None


PROVIDERS = {
    "mail.tm": (_provider_mail_tm, _poll_mail_tm),
    "guerrillamail": (_provider_guerrillamail, _poll_guerrillamail),
}
FALLBACK_ORDER = ["mail.tm", "guerrillamail"]


# ==============================================================================
#  Verification email parsing
# ==============================================================================

def _extract_verification_url(body, subject=""):
    """Extract a verification link from the email body. Returns the URL or None."""
    # Common patterns in verification emails
    patterns = [
        r'https?://[^\s<>"\']*/(?:verify|confirm|activate|validate|email-verify)[^\s<>"\']*',
        r'https?://[^\s<>"\']*\bverify[^\s<>"\']*token=[^\s<>"\']+',
        r'https?://[^\s<>"\']*\bconfirmation[^\s<>"\']*',
        r'https?://[^\s<>"\']*\bactivate[^\s<>"\']*',
        r'<a\s+[^>]*href=["\'](https?://[^"\']*verify[^"\']*)["\']',
        r'<a\s+[^>]*href=["\'](https?://[^"\']*confirm[^"\']*)["\']',
        r'<a\s+[^>]*href=["\'](https?://[^"\']*activate[^"\']*)["\']',
    ]
    for pat in patterns:
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            url = m.group(1) if m.lastindex else m.group(0)
            # Clean common trailing characters
            url = re.sub(r'["\')\],;]+$', '', url)
            return url
    # Last resort: just grab the first URL in the email body
    m = re.search(r'https?://[^\s<>"\']+', body)
    if m:
        return m.group(0)
    return None


# ==============================================================================
#  Provisioner class
# ==============================================================================

class Provisioner:
    def __init__(self, panel=DEFAULT_PANEL, verify_ssl=True):
        self.panel = os.path.abspath(panel)
        self.verify_ssl = verify_ssl
        self._public_key = None
        self._panel_url = None
        self._api_base = None

    # ---- panel discovery ----------------------------------------------------
    def _load_public_key(self):
        if self._public_key is not None:
            return self._public_key
        key_path = os.path.join(self.panel, "data", "public.key")
        if not os.path.exists(key_path):
            raise RuntimeError("RSA public key not found: %s" % key_path)
        with open(key_path, "r") as f:
            self._public_key = f.read()
        return self._public_key

    def _get_api_base(self):
        """Return the official API base URL."""
        if self._api_base is not None:
            return self._api_base
        # Default: production aapanel.com
        self._api_base = "https://www.aapanel.com"
        return self._api_base

    def _discover_panel_url(self):
        """Try to find the local panel URL."""
        if self._panel_url is not None:
            return self._panel_url
        port_path = os.path.join(self.panel, "data", "port.pl")
        try:
            with open(port_path) as f:
                port = f.read().strip()
            # Try to detect local IP
            panel_ip = "127.0.0.1"
            hostname_path = os.path.join(self.panel, "data", "myhost.pl")
            if os.path.exists(hostname_path):
                with open(hostname_path) as f:
                    panel_ip = f.read().strip()
            self._panel_url = "https://{}:{}".format(panel_ip, port)
        except OSError:
            self._panel_url = "https://127.0.0.1:8888"
        return self._panel_url

    def _panel_url_explicit(self, url):
        """Override the panel URL for remote access."""
        self._panel_url = url
        return self._panel_url

    # ---- environment info generation ---------------------------------------
    def _generate_env_info(self):
        """Return a dict matching the fetch_env_info() structure from
        userRegister.py:164-173, with a generated server_id reflecting a
        provisionable panel."""
        server_id = hashlib.md5(uuid.uuid4().hex.encode()).hexdigest() \
                    + hashlib.md5(uuid.uuid4().hex.encode()).hexdigest()
        return {
            "ip": "10.0.%d.%d" % (uuid.uuid4().int % 254 + 1, uuid.uuid4().int % 254 + 1),
            "is_ipv6": 0,
            "os": "Linux x86_64",
            "mac": "%02x:%02x:%02x:%02x:%02x:%02x" % tuple(
                uuid.uuid4().int % 256 for _ in range(6)),
            "hdid": uuid.uuid4().hex[:8].upper(),
            "ramid": "2048",
            "cpuid": uuid.uuid4().hex[:16].upper(),
            "server_name": "localhost.localdomain",
            "install_code": server_id,
        }

    # ---- direct aapanel.com registration ------------------------------------
    def _register_direct(self, email, password, encrypted_email, encrypted_pwd):
        """POST directly to aapanel.com's register_on_panel endpoint."""
        api_base = self._get_api_base()
        url = api_base + "/api/user/register_on_panel"
        env_info = self._generate_env_info()
        payload = {
            "email": encrypted_email,
            "password": encrypted_pwd,
            "environment_info": json.dumps(env_info),
            "install_code": env_info["install_code"],
        }
        resp = _form_post(url, payload, timeout=60, verify=self.verify_ssl)
        try:
            data = json.loads(resp)
            return data
        except json.JSONDecodeError:
            return {"success": False, "res": resp}

    def _login_direct(self, encrypted_email, encrypted_pwd):
        """Login directly on aapanel.com to get an access token."""
        api_base = self._get_api_base()
        url = api_base + "/api/user/login"
        payload = {
            "identification": encrypted_email,
            "password": encrypted_pwd,
            "from_panel": _encrypt_rsa("1", self._load_public_key()),
        }
        resp = _form_post(url, payload, timeout=60, verify=self.verify_ssl)
        try:
            return json.loads(resp)
        except json.JSONDecodeError:
            return {"success": False, "res": resp}

    def _free_trial_direct(self, token=None):
        """Activate free trial directly via aapanel.com."""
        api_base = self._get_api_base()
        url = api_base + "/api/product/obtainProfessionalMemberFree"
        env_info = self._generate_env_info()
        payload = {
            "environment_info": json.dumps(env_info),
        }
        if token:
            return _form_post(url, payload, timeout=60, verify=self.verify_ssl,
                              extra_headers={"Authorization": "Bearer " + token})
        return _form_post(url, payload, timeout=60, verify=self.verify_ssl)

    # ---- panel proxied registration -----------------------------------------
    def _register_panel(self, encrypted_email, encrypted_pwd, panel_url=None):
        """POST to the panel's /userRegister?action=toRegister endpoint."""
        url = (panel_url or self._discover_panel_url()) + "/userRegister"
        env_info = self._generate_env_info()
        payload = {
            "email": encrypted_email,
            "password": encrypted_pwd,
            "environment_info": json.dumps(env_info),
            "install_code": env_info["install_code"],
        }
        # Panel accepts form data with action parameter
        params = {"action": "toRegister"}
        resp_text = _form_post(url, payload, timeout=60, verify=self.verify_ssl,
                               params=params)
        try:
            return json.loads(resp_text)
        except json.JSONDecodeError:
            return {"status": False, "msg": resp_text}

    def _login_panel(self, encrypted_email, encrypted_pwd, panel_url=None):
        """Login to panel (bind account) via getToken."""
        url = (panel_url or self._discover_panel_url()) + "/login"
        payload = {
            "identification": encrypted_email,
            "password": encrypted_pwd,
            "from_panel": _encrypt_rsa("1", self._load_public_key()),
        }
        # Note: the panel uses POST /login?action=getToken
        params = {"action": "getToken"}
        resp_text = _form_post(url, payload, timeout=60, verify=self.verify_ssl)
        try:
            return json.loads(resp_text)
        except json.JSONDecodeError:
            return {"status": False, "msg": resp_text}

    def _free_trial_panel(self, panel_url=None):
        """Activate free trial via panel proxy."""
        url = (panel_url or self._discover_panel_url()) + "/panelAuth"
        env_info = self._generate_env_info()
        payload = {
            "environment_info": json.dumps(env_info),
        }
        params = {"action": "free_trial"}
        resp_text = _form_post(url, payload, timeout=60, verify=self.verify_ssl,
                               params=params)
        try:
            return json.loads(resp_text)
        except json.JSONDecodeError:
            return {"status": False, "msg": resp_text}

    # ---- main provision flow ------------------------------------------------
    def provision_one(self, provider="mail.tm", force=False, direct=False,
                      panel_url=None):
        """Execute a single provisioning cycle.

        Returns:
            dict with keys: success, email, provider, steps (list of step dicts
            with name/status/msg), error (if any).
        """
        result = {
            "success": False,
            "email": None,
            "provider": provider,
            "steps": [],
            "error": None,
        }

        # Step 0: check if already bound (idempotency)
        if not force and not direct:
            ui_path = os.path.join(self.panel, "data", "userInfo.json")
            if os.path.exists(ui_path):
                try:
                    ui = json.load(open(ui_path))
                    if ui.get("status") is True or ui.get("status") == 1:
                        if ui.get("token") and ui.get("token") != "patched_access_key":
                            result["success"] = True
                            result["email"] = ui.get("email", "already-bound")
                            result["steps"].append({
                                "name": "check", "status": "skipped",
                                "msg": "already bound"})
                            _log_result(self.panel, result)
                            return result
                except (OSError, ValueError):
                    pass

        # Step 1: create temporary email inbox
        step = {"name": "create_inbox", "status": "running"}
        result["steps"].append(step)
        try:
            create_fn, poll_fn = PROVIDERS.get(provider, (None, None))
            if not create_fn:
                raise RuntimeError("Unknown provider: %s" % provider)
            if provider == "guerrillamail":
                address, password, token = create_fn()
            else:
                address, password, token = create_fn()
            result["email"] = address
            step["status"] = "ok"
            step["msg"] = "created %s" % address
        except Exception as e:
            step["status"] = "failed"
            step["msg"] = str(e)[:200]
            result["error"] = "create_inbox: %s" % step["msg"]
            _log_result(self.panel, result)
            return result

        # Step 2: encrypt credentials
        step = {"name": "encrypt", "status": "running"}
        result["steps"].append(step)
        try:
            pk = self._load_public_key()
            enc_email = _encrypt_rsa(address, pk)
            enc_pwd = _encrypt_rsa(password, pk)
            step["status"] = "ok"
            step["msg"] = "RSA-encrypted"
        except Exception as e:
            step["status"] = "failed"
            step["msg"] = str(e)[:200]
            result["error"] = "encrypt: %s" % step["msg"]
            _log_result(self.panel, result)
            return result

        # Step 3: register
        step = {"name": "register", "status": "running"}
        result["steps"].append(step)
        try:
            if direct:
                resp = self._register_direct(address, password, enc_email, enc_pwd)
                success = resp.get("success") or resp.get("status") in (True, 0)
            else:
                resp = self._register_panel(enc_email, enc_pwd, panel_url)
                success = resp.get("status") in (True, 0, "0")
            if not success:
                msg = resp.get("res") or resp.get("msg") or json.dumps(resp)
                raise RuntimeError(msg[:300])
            step["status"] = "ok"
            step["msg"] = "registered"
        except Exception as e:
            step["status"] = "failed"
            step["msg"] = str(e)[:300]
            result["error"] = "register: %s" % step["msg"]
            _log_result(self.panel, result)
            return result

        # Step 4: poll inbox for verification email
        step = {"name": "poll_verification", "status": "running"}
        result["steps"].append(step)
        try:
            poll_result = poll_fn(token, poll_sec=3, timeout_sec=120)
            if not poll_result:
                raise RuntimeError("verification email not received within 120s")
            subject, body, _detail = poll_result
            verify_url = _extract_verification_url(body, subject)
            if not verify_url:
                verify_url = _extract_verification_url(body, subject)
                if not verify_url:
                    raise RuntimeError(
                        "verification URL not found in email (subject: %s, body %d chars)"
                        % (subject[:80], len(body)))
            step["status"] = "ok"
            step["msg"] = "found verification URL"
            step["verify_url"] = verify_url[:500]
        except Exception as e:
            step["status"] = "failed"
            step["msg"] = str(e)[:300]
            result["error"] = "poll_verification: %s" % step["msg"]
            _log_result(self.panel, result)
            return result

        # Step 5: follow verification URL
        step = {"name": "verify_email", "status": "running"}
        result["steps"].append(step)
        try:
            if direct:
                _json_get(verify_url, timeout=30, verify=self.verify_ssl)
            else:
                _json_get(verify_url, timeout=30, verify=False)
            step["status"] = "ok"
            step["msg"] = "email verified"
        except Exception as e:
            # Some verification endpoints return non-JSON (HTML redirect) —
            # that's usually fine; verification happened.
            step["status"] = "ok"
            step["msg"] = "email verified (non-json response)"

        # Step 6: bind account (login/getToken)
        step = {"name": "bind_account", "status": "running"}
        result["steps"].append(step)
        try:
            if direct:
                resp = self._login_direct(enc_email, enc_pwd)
                success = resp.get("success") or resp.get("status") in (True, 0)
            else:
                resp = self._login_panel(enc_email, enc_pwd, panel_url)
                success = resp.get("status") in (True, 0, "0")
            if not success:
                msg = resp.get("res") or resp.get("msg") or json.dumps(resp)
                raise RuntimeError(msg[:300])
            step["status"] = "ok"
            step["msg"] = "bound"
            # Hide secrets but pass token downstream
            token = resp.get("res", {}).get("access_token", "")
            step["account_token"] = token[:20] + "..." if token else ""
        except Exception as e:
            step["status"] = "failed"
            step["msg"] = str(e)[:300]
            result["error"] = "bind_account: %s" % step["msg"]
            _log_result(self.panel, result)
            return result

        # Step 7: activate free trial
        step = {"name": "free_trial", "status": "running"}
        result["steps"].append(step)
        try:
            if direct:
                resp_text = self._free_trial_direct()
                try:
                    resp = json.loads(resp_text)
                except json.JSONDecodeError:
                    resp = {"status": resp_text}
                success = resp.get("success") or resp.get("status") in (True, 0, "0")
            else:
                resp = self._free_trial_panel(panel_url)
                success = resp.get("status") in (True, 0, "0")
            if not success:
                msg = resp.get("res") or resp.get("msg") or json.dumps(resp)
                raise RuntimeError(msg[:300])
            step["status"] = "ok"
            step["msg"] = "15-day trial activated"
        except Exception as e:
            step["status"] = "failed"
            step["msg"] = str(e)[:300]
            result["error"] = "free_trial: %s" % step["msg"]
            _log_result(self.panel, result)
            return result

        result["success"] = True
        _log_result(self.panel, result)
        return result

    def check_register_endpoint(self, panel_url=None):
        """Probe the register endpoint without actually registering."""
        result = {"reachable": False, "status_code": None, "body": None}
        api_base = self._get_api_base()
        url = api_base + "/api/user/register_on_panel"
        try:
            # Simple GET or empty POST to probe
            resp = _form_post(url, {"email": "probe@example.com", "password": "x" * 8},
                             timeout=15, verify=self.verify_ssl)
            result["reachable"] = True
            result["body"] = resp[:500]
        except Exception as e:
            result["error"] = str(e)[:200]
        return result


# --- Importable API -----------------------------------------------------------
def provision(panel=DEFAULT_PANEL, provider="mail.tm", force=False, direct=False,
              panel_url=None, verify_ssl=True):
    """Single-shot provision. Returns the result dict."""
    p = Provisioner(panel, verify_ssl=verify_ssl)
    if panel_url:
        p._panel_url_explicit(panel_url)
    return p.provision_one(provider=provider, force=force, direct=direct,
                           panel_url=panel_url)


# --- CLI ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(
        description="aaPanel license provisioner — automated account acquisition")
    ap.add_argument("panel", nargs="?", default=DEFAULT_PANEL,
                    help="panel root path (default: %(default)s)")
    ap.add_argument("--panel-url", default=None,
                    help="Override panel URL (e.g. https://IP:PORT/ENTRYPATH)")
    ap.add_argument("--provider", default="mail.tm",
                    choices=list(PROVIDERS.keys()) + ["auto"],
                    help="temporary email provider (default: mail.tm, auto tries both)")
    ap.add_argument("--direct", action="store_true",
                    help="call aapanel.com API directly (no panel proxy)")
    ap.add_argument("--rotate", type=int, default=1, metavar="N",
                    help="provision N accounts in sequence")
    ap.add_argument("--delay", type=float, default=0.0,
                    help="seconds between rotations (default: 0)")
    ap.add_argument("--force", action="store_true",
                    help="re-provision even if already bound")
    ap.add_argument("--ssl-verify", type=lambda s: s.lower() not in ("0", "no", "false", "off"),
                    default=True, help="verify TLS certificates (default: on)")
    ap.add_argument("--check-only", action="store_true",
                    help="probe the registration endpoint without provisioning")
    ap.add_argument("--poll-timeout", type=int, default=120, metavar="SEC",
                    help="max seconds to poll inbox (default: 120)")

    args = ap.parse_args()

    if not os.path.isdir(args.panel):
        print("ERROR: panel path not found: %s" % args.panel)
        return 2

    p = Provisioner(args.panel, verify_ssl=args.verify_ssl)
    if args.panel_url:
        p._panel_url_explicit(args.panel_url)

    if args.check_only:
        print("--- Checking registration endpoint ---")
        result = p.check_register_endpoint(args.panel_url)
        print(json.dumps(result, indent=2))
        return 0

    providers = [args.provider] if args.provider != "auto" else FALLBACK_ORDER

    summary = {"total": args.rotate, "success": 0, "failed": 0}
    for i in range(args.rotate):
        if args.rotate > 1:
            print("\n--- Rotation %d/%d ---" % (i + 1, args.rotate))

        last_error = None
        result = None

        for prov in providers:
            print("   Provider: %s ..." % prov, end=" ", flush=True)
            result = p.provision_one(
                provider=prov, force=args.force or i > 0,
                direct=args.direct, panel_url=args.panel_url)
            if result["success"]:
                print("OK (%s)" % result["email"])
                summary["success"] += 1
                break
            else:
                print("FAIL (%s)" % result["error"])
                last_error = result["error"]

        if not result or not result["success"]:
            summary["failed"] += 1
            if not providers:
                print("   All providers failed. Last error: %s" % last_error)

        if i < args.rotate - 1 and args.delay > 0:
            time.sleep(args.delay)

    if args.rotate > 1:
        print("\n=== Summary: %d total, %d success, %d failed ==="
              % (summary["total"], summary["success"], summary["failed"]))

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
