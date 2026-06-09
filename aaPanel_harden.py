#!/usr/bin/env python3
"""
aaPanel License Hardener — canonical, version-aware, idempotent patcher
=======================================================================

Single source of truth for every edit that turns a free/trial aaPanel into a
permanent-Pro install. The installer (custom_install.sh) and the watchdog both
call THIS module instead of re-implementing the edits — so the patch logic lives
in exactly one place and cannot drift.

Design principles
-----------------
* Anchor-based, not line-number/version based. Each edit locates its site by a
  content regex and derives indentation from the matched line, so it survives
  whitespace/reflow changes across aaPanel versions (2.19.0 deployed -> 3.x -> …).
* Idempotent. Every applied edit leaves a stable ``# AAP:<id>`` marker; a re-run
  (or a panel self-upgrade re-patch) skips anything already marked.
* Fail loud. A required edit whose anchor cannot be found is reported as
  ``[FAIL] anchor '<id>' not found`` and makes the run exit non-zero — never a
  silent no-op that quietly leaves the panel on trial.
* Behaviour over source where it's fragile. The plugin-store / catalog fallback
  is delivered by the runtime guard (sitecustomize.py) at the ``PluginLoader``
  boundary rather than by surgery inside ``load_soft_list`` — that layer survives
  source reshuffles that would break a line patch.

Usage
-----
  pyenv/bin/python3 aaPanel_harden.py [PANEL]            apply + guard + verify
  pyenv/bin/python3 aaPanel_harden.py [PANEL] --check    dry-run: report anchors
  pyenv/bin/python3 aaPanel_harden.py [PANEL] --verify   runtime verify only
  pyenv/bin/python3 aaPanel_harden.py [PANEL] --guard    (re)install guard only
  pyenv/bin/python3 aaPanel_harden.py [PANEL] --remove-guard

Importable API (used by installer/watchdog):
  apply(panel, backup_dir=None) -> bool   verify(panel) -> bool
  check(panel) -> bool                    install_guard(panel) / remove_guard(panel)
"""

import argparse
import os
import re
import shutil
import site
import sys

DEFAULT_PANEL = "/www/server/panel"
MARKER = "# AAP:"  # every applied edit carries "# AAP:<id>"


# --------------------------------------------------------------------------- #
#  Patch specification                                                         #
# --------------------------------------------------------------------------- #
# Each spec is a dict:
#   id        stable identifier; also the marker suffix and idempotency key
#   file      path relative to the panel root
#   op        insert_after | insert_before | replace_inline | replace_body |
#             replace_in_func
#   anchor    regex (multiline) identifying the site (insert_*/replace_inline)
#   payload   text to insert/replace-with — "{i}" -> the anchor's indent
#   old/new   for replace_inline/replace_in_func: substring swap
#   func      for replace_body/replace_in_func: function header regex
#   required  if True, a missing anchor fails the whole run
#
# Anchors below are verified identical between upstream master and the deployed
# 2.19.0 Pro box (the live panel reports "Lifetime", proving they resolve there).
PATCHES = [
    {
        "id": "app_lifetime",
        "file": "BTPanel/app.py",
        "op": "insert_after",
        "anchor": r"^(?P<i>\s*)if tmp: tmp = int\(tmp\)\s*$",
        "payload": "{i}tmp = 0  " + MARKER + "app_lifetime",
        "required": True,
    },
    {
        # Ensure pro=0 fallback unconditionally (not gated by `if tmp:`), so a fresh
        # panel install without cached license still reports Lifetime rather than FREE(-1).
        # Anchored to the get_pd badge logic: `if ltd < 1:` (unique in common.py).
        "id": "pd_pro0",
        "file": "class/public/common.py",
        "op": "insert_before",
        "anchor": r"^(?P<i>\s*)if ltd < 1:\s*$",
        "payload": "{i}pro = 0  " + MARKER + "pd_pro0 (unconditional)",
        "required": True,
    },
    {
        "id": "cache_10yr",
        "file": "class/public/common.py",
        "op": "replace_inline",
        "anchor": r"^\s*token_expire_time = int\(time_content\.strip\(\)\) \+ 86400.*$",
        "old": "+ 86400",
        "new": "+ 315360000  " + MARKER + "cache_10yr (10 years)",
        "required": True,
    },
    {
        "id": "refresh_pd_pro0",
        "file": "class/public/common.py",
        "op": "insert_before",
        "anchor": r"^(?P<i>\s*)writeFile\(\"/tmp/\" \+ p_token, str\(softList\['pro'\]\)\)\s*$",
        "payload": "{i}softList['pro'] = 0  " + MARKER + "refresh_pd_pro0",
        "required": True,
    },
    {
        "id": "resp_threshold",
        "file": "class/public/common.py",
        "op": "replace_inline",
        "anchor": r"^\s*if resp\.ok:\s*$",
        "old": "if resp.ok:",
        "new": "if resp.ok and resp.text and len(resp.text) > 50000:  " + MARKER + "resp_threshold",
        "required": False,  # cache-poison guard; nice-to-have, guard covers the gap
    },
    {
        "id": "is_pro_v1",
        "file": "class/config.py",
        "op": "replace_body",
        "func": r"^(?P<i>\s*)def is_pro\(self,\s*get\):\s*$",
        "payload": "return True  " + MARKER + "is_pro_v1",
        "required": True,
    },
    {
        "id": "is_pro_v2",
        "file": "class_v2/config_v2.py",
        "op": "replace_body",
        "func": r"^(?P<i>\s*)def is_pro\(self,\s*get\):\s*$",
        "payload": "return True  " + MARKER + "is_pro_v2",
        "required": True,
    },
    {
        # Replace the WHOLE body with `return 200`. Patching only the `except`
        # branch is insufficient: when read_config('abort') is set (e.g. 404 on a
        # fresh panel) the try branch returns that value and never hits except.
        "id": "not_auth_v1",
        "file": "class/config.py",
        "op": "replace_body",
        "func": r"^(?P<i>\s*)def get_not_auth_status\(self\):\s*$",
        "payload": "return 200  " + MARKER + "not_auth_v1",
        "required": True,
    },
    {
        "id": "not_auth_v2",
        "file": "class_v2/config_v2.py",
        "op": "replace_body",
        "func": r"^(?P<i>\s*)def get_not_auth_status\(self\):\s*$",
        "payload": "return 200  " + MARKER + "not_auth_v2",
        "required": True,
    },
    {
        # Registration rate limiter + CAPTCHA gate (V1). Injects a block that:
        #  1. Limits signups to 5 per IP per hour
        #  2. Requires CAPTCHA after 3 signups from the same IP in 1 hour
        # Anchored on the emailformat line (unique in userRegister.py).
        "id": "reg_guard_v1",
        "file": "class/userRegister.py",
        "op": "insert_before",
        "anchor": r"^(?P<i>\s*)emailformat = re\.compile\(r'\[a-zA-Z0-9\.\-_\+%\]\+\@",
        "payload": (
            "{i}_reg_ip = public.GetClientIp()\n"
            "{i}try:\n"
            "{i}    from BTPanel import cache\n"
            "{i}except Exception:\n"
            "{i}    pass\n"
            "{i}_reg_key = 'limitRegNum_v' + _reg_ip\n"
            "{i}_reg_count = cache.get(_reg_key) if cache else 0\n"
            "{i}if _reg_count >= 5:\n"
            "{i}    return public.return_msg_gettext(False, public.lang('Too many registration attempts. Please try again later.'))\n"
            "{i}cache and cache.set(_reg_key, (_reg_count or 0) + 1, 3600)\n"
            "{i}_cap_key = 'limitRegCap_v' + _reg_ip\n"
            "{i}_cap_count = cache.get(_cap_key) if cache else 0\n"
            "{i}if _cap_count >= 3:\n"
            "{i}    if not hasattr(post, 'code') or not post.code:\n"
            "{i}        return public.return_msg_gettext(False, public.lang('CAPTCHA verification required. Please refresh and try again.'))\n"
            "{i}    from BTPanel import session\n"
            "{i}    if not public.checkCode(post.code):\n"
            "{i}        cache and cache.set(_reg_key, (_reg_count or 0) + 1, 3600)\n"
            "{i}        return public.return_msg_gettext(False, public.lang('CAPTCHA verification failed.'))\n"
            "{i}cache and cache.set(_cap_key, (_cap_count or 0) + 1, 3600)\n"
            "{i}  " + MARKER + "reg_guard_v1"
        ),
        "required": True,
    },
    {
        # Same registration guard for V2.
        "id": "reg_guard_v2",
        "file": "class_v2/userRegister_v2.py",
        "op": "insert_before",
        "anchor": r"^(?P<i>\s*)emailformat = re\.compile\(r'\[a-zA-Z0-9\.\-_\+%\]\+\@",
        "payload": (
            "{i}_reg_ip = public.GetClientIp()\n"
            "{i}try:\n"
            "{i}    from BTPanel import cache\n"
            "{i}except Exception:\n"
            "{i}    pass\n"
            "{i}_reg_key = 'limitRegNum_v' + _reg_ip\n"
            "{i}_reg_count = cache.get(_reg_key) if cache else 0\n"
            "{i}if _reg_count >= 5:\n"
            "{i}    return public.return_message(-1, 0, public.lang('Too many registration attempts. Please try again later.'))\n"
            "{i}cache and cache.set(_reg_key, (_reg_count or 0) + 1, 3600)\n"
            "{i}_cap_key = 'limitRegCap_v' + _reg_ip\n"
            "{i}_cap_count = cache.get(_cap_key) if cache else 0\n"
            "{i}if _cap_count >= 3:\n"
            "{i}    if not hasattr(post, 'code') or not post.code:\n"
            "{i}        return public.return_message(-1, 0, public.lang('CAPTCHA verification required. Please refresh and try again.'))\n"
            "{i}    from BTPanel import session\n"
            "{i}    if not public.checkCode(post.code):\n"
            "{i}        cache and cache.set(_reg_key, (_reg_count or 0) + 1, 3600)\n"
            "{i}        return public.return_message(-1, 0, public.lang('CAPTCHA verification failed.'))\n"
            "{i}cache and cache.set(_cap_key, (_cap_count or 0) + 1, 3600)\n"
            "{i}  " + MARKER + "reg_guard_v2"
        ),
        "required": True,
    },
    {
        # Email domain validation (V1). Checks the un-encrypted email
        # domain against data/email_domain_blocklist.json before RSA encryption.
        # Anchored just before the en_code_rsa call.
        "id": "reg_disp_email_v1",
        "file": "class/userRegister.py",
        "op": "insert_before",
        "anchor": r"^(?P<i>\s*)post\.email = self\.en_code_rsa\(post\.email\)",
        "payload": (
            "{i}_bl_path = os.path.join(public.get_panel_path(), 'data', 'email_domain_blocklist.json')\n"
            "{i}if os.path.exists(_bl_path):\n"
            "{i}    try:\n"
            "{i}        import json as _json\n"
            "{i}        _blocked = _json.load(open(_bl_path))\n"
            "{i}        _domain = post.email.split('@')[-1].lower().strip()\n"
            "{i}        if _domain in _blocked:\n"
            "{i}            return public.return_msg_gettext(False, public.lang('Email addresses from non-persistent domains are not allowed.'))\n"
            "{i}    except Exception:\n"
            "{i}        pass\n"
            "{i}  " + MARKER + "reg_disp_email_v1"
        ),
        "required": True,
    },
    {
        # Same email domain validation for V2.
        "id": "reg_disp_email_v2",
        "file": "class_v2/userRegister_v2.py",
        "op": "insert_before",
        "anchor": r"^(?P<i>\s*)post\.email = self\.en_code_rsa\(post\.email\)",
        "payload": (
            "{i}_bl_path = os.path.join(public.get_panel_path(), 'data', 'email_domain_blocklist.json')\n"
            "{i}if os.path.exists(_bl_path):\n"
            "{i}    try:\n"
            "{i}        import json as _json\n"
            "{i}        _blocked = _json.load(open(_bl_path))\n"
            "{i}        _domain = post.email.split('@')[-1].lower().strip()\n"
            "{i}        if _domain in _blocked:\n"
            "{i}            return public.return_message(-1, 0, public.lang('Email addresses from non-persistent domains are not allowed.'))\n"
            "{i}    except Exception:\n"
            "{i}        pass\n"
            "{i}  " + MARKER + "reg_disp_email_v2"
        ),
        "required": True,
    },
]

# Frontend (minified JS) patches. Bundle filenames carry per-build hashes and the
# minified variable names change every build, so these match by STRUCTURE with
# \w+ wildcards for identifiers and a flexible quote class for `/binds` — never by
# fixed variable name. Applied across all static JS; idempotent because the
# replacement removes the matched structure.
JS_GLOB = "BTPanel/static/**/*.js"
JS_PATCHES = [
    {
        # Router guard that forces unbound Pro panels to /binds (the trial gate).
        #   !X.userInfo.status&&X.aaPanelPro ? (path===/binds?cb():cb(/binds)) : (X.getCheckAuth(),cb())
        # Replace the whole ternary with just the false branch so it never redirects.
        # Handles both comparison orders (path===/binds and /binds===path).
        "id": "js_binds",
        "pattern": (r"!\w+\.userInfo\.status&&\w+\.aaPanelPro\?"
                    r"(?:\w+\.path===[`'\"]/binds[`'\"]|[`'\"]/binds[`'\"]===\w+\.path)"
                    r"\?\w+\(\):\w+\([`'\"]/binds[`'\"]\):"
                    r"(\(\w+\.getCheckAuth\(\),\w+\(\)\))"),
        "repl": r"\1",
        "important": True,   # blocks the whole UI; warn loudly if it survives
    },
    {
        # Sub-account limit gate: table.total>=30  ->  >=99999
        "id": "js_acct_limit",
        "pattern": r"(\w+\.total)>=30\b",
        "repl": r"\g<1>>=99999",
        "important": False,
    },
    {
        # Router "pro" type guards: {type:"pro"} / {type:"pro",...}  ->  {}
        "id": "js_router_pro",
        "pattern": r"\{type:[`'\"]pro[`'\"](?:,[^}]*)?\}",
        "repl": "{}",
        "important": False,
    },
]

# Per-version anchor overrides. Defaults above are confirmed on 2.19.0; when a
# future release (3.x) moves a site, add an override here keyed by a version-
# prefix match — e.g. {"3.": {"pd_pro0": {"anchor": r"..."}}}. Unknown versions
# fall through to the defaults and rely on fail-loud + the runtime guard.
VERSION_OVERRIDES = {}


# --------------------------------------------------------------------------- #
#  Reporting                                                                   #
# --------------------------------------------------------------------------- #
class Report:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.skipped = 0

    def ok(self, msg):
        self.passed += 1
        print("  [PASS] " + msg)

    def bad(self, msg):
        self.failed += 1
        print("  [FAIL] " + msg)

    def skip(self, msg):
        self.skipped += 1
        print("  [SKIP] " + msg)

    def summary(self):
        print("\n  Results: %d passed, %d failed, %d skipped"
              % (self.passed, self.failed, self.skipped))
        return self.failed == 0


class _AnchorMiss(Exception):
    pass


# --------------------------------------------------------------------------- #
#  Core patcher                                                                #
# --------------------------------------------------------------------------- #
class Hardener:
    def __init__(self, panel=DEFAULT_PANEL, backup_dir=None, report=None):
        self.panel = os.path.abspath(panel)
        self.report = report or Report()
        self.version = self._detect_version()
        self.patches = self._resolve_patches()
        self.backup_dir = backup_dir
        self._backed_up = set()
        self.manifest = os.path.join(backup_dir, "manifest.txt") if backup_dir else None

    # ---- version detection -------------------------------------------------
    def _detect_version(self):
        """Best-effort read of the running panel version. Used only to pick
        anchor overrides; correctness never depends on getting it right."""
        for rel, pat in (
            ("class/common.py", r"g\.version\s*=\s*['\"]([0-9][0-9.]*)['\"]"),
            ("class_v2/common_v2.py", r"g\.version\s*=\s*['\"]([0-9][0-9.]*)['\"]"),
        ):
            p = os.path.join(self.panel, rel)
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    m = re.search(pat, f.read())
                if m:
                    return m.group(1)
            except OSError:
                pass
        for rel in ("data/version.pl", "config/version.pl"):
            p = os.path.join(self.panel, rel)
            try:
                with open(p) as f:
                    v = f.read().strip()
                if v:
                    return v
            except OSError:
                pass
        return "unknown"

    def _resolve_patches(self):
        """Apply VERSION_OVERRIDES whose key is a prefix of the detected version."""
        overrides = {}
        for prefix, table in VERSION_OVERRIDES.items():
            if self.version != "unknown" and self.version.startswith(prefix):
                overrides.update(table)
        out = []
        for spec in PATCHES:
            if spec["id"] in overrides:
                merged = dict(spec)
                merged.update(overrides[spec["id"]])
                out.append(merged)
            else:
                out.append(spec)
        return out

    # ---- file helpers ------------------------------------------------------
    def _read(self, path):
        with open(path, "r", encoding="utf-8", errors="surrogateescape") as f:
            return f.read()

    def _write(self, path, content):
        with open(path, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(content)

    def _backup(self, path):
        if not self.backup_dir or path in self._backed_up:
            return
        rel = os.path.relpath(path, self.panel)
        dst = os.path.join(self.backup_dir, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(path, dst)
        self._backed_up.add(path)
        if self.manifest:
            with open(self.manifest, "a") as mf:
                mf.write(rel + "\n")

    # ---- per-op application ------------------------------------------------
    def _apply_one(self, spec, dry_run):
        """Returns (status, message). status in {applied, present, missing, error}."""
        path = os.path.join(self.panel, spec["file"])
        if not os.path.exists(path):
            return ("error", "%s: file not found (%s)" % (spec["id"], spec["file"]))
        try:
            content = self._read(path)
        except OSError as e:
            return ("error", "%s: read error %s" % (spec["id"], e))

        marker = MARKER + spec["id"]
        if marker in content:
            return ("present", "%s: already applied" % spec["id"])

        op = spec["op"]
        try:
            if op in ("insert_after", "insert_before"):
                new = self._op_insert(content, spec)
            elif op == "replace_inline":
                new = self._op_replace_inline(content, spec)
            elif op == "replace_body":
                new = self._op_replace_body(content, spec)
            elif op == "replace_in_func":
                new = self._op_replace_in_func(content, spec)
            else:
                return ("error", "%s: unknown op %s" % (spec["id"], op))
        except _AnchorMiss as e:
            return ("missing", "%s: %s" % (spec["id"], e))

        if new == content:
            return ("present", "%s: no change needed" % spec["id"])
        if not dry_run:
            self._backup(path)
            self._write(path, new)
        return ("applied", "%s -> %s" % (spec["id"], spec["file"]))

    def _op_insert(self, content, spec):
        rx = re.compile(spec["anchor"], re.M)
        m = rx.search(content)
        if not m:
            raise _AnchorMiss("anchor not found")
        indent = m.groupdict().get("i", "") or ""
        line = spec["payload"].format(i=indent)
        if spec["op"] == "insert_after":
            pos = m.end()
            return content[:pos] + "\n" + line + content[pos:]
        pos = m.start()
        return content[:pos] + line + "\n" + content[pos:]

    def _op_replace_inline(self, content, spec):
        rx = re.compile(spec["anchor"], re.M)
        m = rx.search(content)
        if not m:
            raise _AnchorMiss("anchor not found")
        line = m.group(0)
        if spec["old"] not in line:
            raise _AnchorMiss("old text %r not on matched line" % spec["old"])
        return content[:m.start()] + line.replace(spec["old"], spec["new"], 1) + content[m.end():]

    def _func_span(self, lines, func_rx):
        """Return (header_idx, body_start, body_end, indent) for the function
        whose header matches func_rx. body_end is exclusive."""
        rx = re.compile(func_rx)
        for i, ln in enumerate(lines):
            m = rx.match(ln)
            if not m:
                continue
            base = len(ln) - len(ln.lstrip())
            indent = m.groupdict().get("i") or (" " * base)
            j = i + 1
            while j < len(lines) and lines[j].strip() == "":
                j += 1
            end = j
            while end < len(lines):
                s = lines[end]
                if s.strip() != "" and (len(s) - len(s.lstrip())) <= base:
                    break
                end += 1
            return i, j, end, indent
        raise _AnchorMiss("function header not found")

    def _op_replace_body(self, content, spec):
        lines = content.split("\n")
        _i, body_start, body_end, indent = self._func_span(lines, spec["func"])
        body_indent = indent + "    "
        lines[body_start:body_end] = [body_indent + spec["payload"]]
        return "\n".join(lines)

    def _op_replace_in_func(self, content, spec):
        lines = content.split("\n")
        _i, body_start, body_end, _indent = self._func_span(lines, spec["func"])
        for k in range(body_start, body_end):
            if spec["old"] in lines[k]:
                lines[k] = lines[k].replace(spec["old"], spec["new"], 1)
                return "\n".join(lines)
        raise _AnchorMiss("old text %r not found in function body" % spec["old"])

    # ---- public phases -----------------------------------------------------
    def _run_specs(self, dry_run):
        all_required_ok = True
        for spec in self.patches:
            status, msg = self._apply_one(spec, dry_run=dry_run)
            if status in ("applied", "present"):
                self.report.ok(msg)
            elif status == "missing":
                if spec["required"]:
                    self.report.bad(msg + "  (REQUIRED — panel may revert to trial)")
                    all_required_ok = False
                else:
                    self.report.skip(msg + "  (optional)")
            else:  # error
                if spec["required"]:
                    self.report.bad(msg)
                    all_required_ok = False
                else:
                    self.report.skip(msg)
        return all_required_ok

    def _run_js(self, dry_run):
        """Apply the minified-JS structural patches across all static bundles.
        Returns True unless an `important` patch's target survives the pass."""
        import glob as _glob
        files = _glob.glob(os.path.join(self.panel, JS_GLOB), recursive=True)
        all_ok = True
        for jp in JS_PATCHES:
            rx = re.compile(jp["pattern"])
            changed = 0
            residual = 0
            for path in files:
                try:
                    content = self._read(path)
                except OSError:
                    continue
                if not rx.search(content):
                    continue
                new = rx.sub(jp["repl"], content)
                if new != content:
                    changed += 1
                    if not dry_run:
                        self._backup(path)
                        self._write(path, new)
                    # after sub, the structure should be gone; flag if not
                    if rx.search(new):
                        residual += 1
            if changed:
                self.report.ok("%s: %s %d bundle(s)"
                               % (jp["id"], "would patch" if dry_run else "patched", changed))
            else:
                # nothing matched — already patched, or a build we don't recognise
                if jp["important"]:
                    self.report.skip("%s: pattern not present (already patched, or "
                                     "frontend changed — verify the /binds gate manually)"
                                     % jp["id"])
                else:
                    self.report.skip("%s: nothing to patch" % jp["id"])
            if residual and jp["important"]:
                self.report.bad("%s: target SURVIVED in %d bundle(s) — gate may persist"
                                % (jp["id"], residual))
                all_ok = False
        return all_ok

    def check(self):
        print("\n=== CHECK (dry-run) — panel=%s version=%s ===" % (self.panel, self.version))
        ok = self._run_specs(dry_run=True)
        ok_js = self._run_js(dry_run=True)
        return ok and ok_js

    def apply(self):
        print("\n=== PATCH — panel=%s version=%s ===" % (self.panel, self.version))
        ok = self._run_specs(dry_run=False)
        ok_js = self._run_js(dry_run=False)
        self._clear_pycache()
        return ok and ok_js

    def _clear_pycache(self):
        for sub in ("class", "class_v2", "BTPanel"):
            root_dir = os.path.join(self.panel, sub)
            for root, dirs, _ in os.walk(root_dir):
                if "__pycache__" in dirs:
                    shutil.rmtree(os.path.join(root, "__pycache__"), ignore_errors=True)

    # ---- guard (behavioural, version-resilient) ---------------------------
    def _guard_path(self):
        """Locate the panel's pyenv site-packages. Resolved by globbing the
        panel's own pyenv (independent of which interpreter runs this script),
        so we never write a sitecustomize.py into an unrelated global
        environment. Prefer the dir matching the running interpreter; otherwise
        take the sole/first pyenv site-packages."""
        import glob as _glob
        cands = _glob.glob(os.path.join(self.panel, "pyenv", "lib", "python*", "site-packages"))
        if cands:
            want = "python%d.%d" % (sys.version_info.major, sys.version_info.minor)
            match = [c for c in cands if os.sep + want + os.sep in c + os.sep]
            sp = (match or sorted(cands))[0]
        else:
            # last resort: this interpreter's site-packages (standalone --guard use)
            sys_cands = site.getsitepackages() if hasattr(site, "getsitepackages") else []
            sp = sys_cands[0] if sys_cands else os.path.join(self.panel, "pyenv")
        return os.path.join(sp, "sitecustomize.py")

    def install_guard(self):
        print("\n=== GUARD ===")
        guard = _GUARD_TEMPLATE.replace("@@PANEL@@", self.panel)
        path = self._guard_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            existing = self._read(path)
            if "AAP_GUARD" in existing:
                # replace just our block, keep any foreign content
                guard = re.sub(r"# --- AAP_GUARD START ---.*?# --- AAP_GUARD END ---\n?",
                               guard, existing, flags=re.S)
            elif existing.strip():
                self._backup(path)
                guard = existing.rstrip() + "\n\n" + guard
        self._write(path, guard)
        self.report.ok("guard installed: %s" % path)
        return True

    def remove_guard(self):
        path = self._guard_path()
        if not os.path.exists(path):
            self.report.skip("guard not present")
            return True
        content = self._read(path)
        if "AAP_GUARD" not in content:
            self.report.skip("sitecustomize present but not ours; left untouched")
            return True
        cleaned = re.sub(r"\n*# --- AAP_GUARD START ---.*?# --- AAP_GUARD END ---\n*",
                         "\n", content, flags=re.S).strip()
        if cleaned:
            self._write(path, cleaned + "\n")
        else:
            os.remove(path)
        self.report.ok("guard removed")
        return True

    # ---- verify (by effect, not by grep) ----------------------------------
    def verify(self):
        print("\n=== VERIFY (runtime) — version=%s ===" % self.version)
        sys.path.insert(0, self.panel)
        sys.path.insert(0, os.path.join(self.panel, "class"))
        try:
            os.chdir(self.panel)
        except OSError:
            pass

        try:
            import config
            r = config.config().is_pro(None)
            self.report.ok("config.is_pro() -> %r" % r) if r else self.report.bad("is_pro -> %r" % r)
        except Exception as e:
            self.report.bad("is_pro import/call: %s" % (str(e)[:120]))

        try:
            import config as _c
            a = _c.config().get_not_auth_status()
            (self.report.ok if a == 200 else self.report.bad)("get_not_auth_status() -> %s" % a)
        except Exception as e:
            self.report.bad("get_not_auth_status: %s" % (str(e)[:120]))

        try:
            import public
            from BTPanel import app
            with app.test_request_context("/", headers={"User-Agent": "Mozilla/5.0"}):
                _htm, pro, _ltd = public.get_pd()
            (self.report.ok if pro == 0 else self.report.bad)("get_pd() pro=%s" % pro)
        except Exception as e:
            self.report.bad("get_pd: %s" % (str(e)[:120]))

        try:
            import public as _p
            sl = _p.load_soft_list(False)
            n = len(sl.get("list", [])) if isinstance(sl, dict) else -1
            pro = sl.get("pro", "n/a") if isinstance(sl, dict) else "n/a"
            (self.report.ok if n > 0 else self.report.skip)(
                "load_soft_list(False) plugins=%s pro=%s" % (n, pro))
        except Exception as e:
            self.report.skip("load_soft_list: %s" % (str(e)[:120]))

        return self.report.failed == 0


# --------------------------------------------------------------------------- #
#  Runtime guard installed as sitecustomize.py                                 #
# --------------------------------------------------------------------------- #
# Behavioural layer: hardens the PluginLoader boundary that load_soft_list calls,
# so a free panel gets pro=0 + an offline plugin catalog WITHOUT fragile surgery
# inside load_soft_list. Survives source reshuffles a line patch would miss.
# Set AAP_GUARD_OFF=1 in the environment to disable it for a given interpreter.
_GUARD_TEMPLATE = r'''# --- AAP_GUARD START ---
"""AAP_GUARD — aaPanel license behavioural guard (idempotent)."""
import os as _os
if _os.environ.get("AAP_GUARD_OFF") != "1":
    import builtins as _b
    import json as _json

    _PANEL = "@@PANEL@@"

    def _aap_catalog():
        try:
            p = _os.path.join(_PANEL, "data", "soft_catalog.json")
            if _os.path.exists(p):
                with open(p, "r") as _f:
                    d = _json.load(_f)
                if isinstance(d, dict) and "list" in d:
                    return d
        except Exception:
            pass
        return None

    def _aap_set_pro_fallback(d):
        if isinstance(d, dict):
            d["pro"] = 0
            d["trail"] = 0   # aaPanel's historical (misspelled) field
            d["trial"] = 0   # correct spelling — set both, harmless if unused
        return d

    def _aap_wrap_pluginloader(pl):
        if getattr(pl, "_aap_wrapped", False):
            return
        _orig_get = getattr(pl, "get_plugin_list", None)
        _orig_parse = getattr(pl, "parse_plugin_list", None)
        if _orig_get is not None:
            def _get(n):
                try:
                    r = _orig_get(n)
                except Exception:
                    r = None
                ok = isinstance(r, dict) and r.get("list") and r.get("status") is not False
                if not ok:
                    cat = _aap_catalog()
                    if cat is not None:
                        return _aap_set_pro_fallback(cat)
                return _aap_set_pro_fallback(r) if isinstance(r, dict) else r
            pl.get_plugin_list = _get
        if _orig_parse is not None:
            def _parse(n):
                # On a free/unlicensed panel parse_plugin_list returns a FALSY
                # value (it does not raise), which makes load_soft_list raise and
                # cascades into get_pd's except branch (pro=-1). Force truthy so
                # load_soft_list proceeds to get_plugin_list, where we inject the
                # catalog. Still call the original for any side effects.
                try:
                    _orig_parse(n)
                except Exception:
                    pass
                return True
            pl.parse_plugin_list = _parse
        pl._aap_wrapped = True

    _aap_orig_import = _b.__import__

    def _aap_import(name, *a, **kw):
        mod = _aap_orig_import(name, *a, **kw)
        if name == "PluginLoader":
            try:
                _aap_wrap_pluginloader(mod)
            except Exception:
                pass
        return mod

    if getattr(_b.__import__, "__name__", "") != "_aap_import":
        _b.__import__ = _aap_import
# --- AAP_GUARD END ---
'''


# --------------------------------------------------------------------------- #
#  Importable API                                                              #
# --------------------------------------------------------------------------- #
def apply(panel=DEFAULT_PANEL, backup_dir=None):
    h = Hardener(panel, backup_dir=backup_dir)
    ok_src = h.apply()
    h.install_guard()
    return ok_src


def check(panel=DEFAULT_PANEL):
    return Hardener(panel).check()


def verify(panel=DEFAULT_PANEL):
    return Hardener(panel).verify()


def install_guard(panel=DEFAULT_PANEL, backup_dir=None):
    return Hardener(panel, backup_dir=backup_dir).install_guard()


def remove_guard(panel=DEFAULT_PANEL, backup_dir=None):
    return Hardener(panel, backup_dir=backup_dir).remove_guard()


# --------------------------------------------------------------------------- #
#  CLI                                                                         #
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="aaPanel license hardener")
    ap.add_argument("panel", nargs="?", default=DEFAULT_PANEL)
    ap.add_argument("--backup-dir", default=None,
                    help="back up each touched file here and append to manifest.txt")
    ap.add_argument("--check", action="store_true", help="dry-run: report anchors, no writes")
    ap.add_argument("--apply", action="store_true",
                    help="patch + guard only (no runtime verify); exit non-zero on required miss")
    ap.add_argument("--verify", action="store_true", help="runtime verification only")
    ap.add_argument("--guard", action="store_true", help="(re)install runtime guard only")
    ap.add_argument("--remove-guard", action="store_true", help="remove runtime guard only")
    args = ap.parse_args()

    if not os.path.isdir(args.panel):
        print("ERROR: panel path not found: %s" % args.panel)
        return 2

    h = Hardener(args.panel, backup_dir=args.backup_dir)

    if args.check:
        ok = h.check()
        h.report.summary()
        return 0 if ok else 1
    if args.apply:
        src_ok = h.apply()
        h.install_guard()
        ok = h.report.summary()
        return 0 if (src_ok and ok) else 1
    if args.verify:
        h.verify()
        return 0 if h.report.summary() else 1
    if args.guard:
        h.install_guard()
        return 0 if h.report.summary() else 1
    if args.remove_guard:
        h.remove_guard()
        return 0 if h.report.summary() else 1

    # full run: patch -> guard -> verify
    src_ok = h.apply()
    h.install_guard()
    h.verify()
    overall = h.report.summary()
    print("\n=== Restart the panel to load changes:  systemctl restart bt  ===")
    return 0 if (src_ok and overall) else 1


if __name__ == "__main__":
    sys.exit(main())
