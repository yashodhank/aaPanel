# aaPanel Agent Rules

> Last updated: 2026-06-08 | Branch: feat/aapanel-license-hardening

## Project-specific conventions

### Commits: cherry-pickable, one logical change each

Every commit must be self-contained and independently reviewable. Avoid bundling
multiple unrelated fixes in one commit. When fixing a PR gap or bug, commit it
separately so it can be cherry-picked into other branches.

```bash
# Good: one commit per concern
git commit -m "fix: align frontend param names with backend contract"
git commit -m "fix: harden SSRF checks for URL-based WAR deployment"

# Bad: kitchen-sink commit
git commit -m "fix: various PR issues and cleanup"
```

### V2 response envelope

All V2 endpoints use `public.success_v2(data)` / `public.fail_v2(msg)`, which
wrap the payload in `{"status": 0|-1, "timestamp": ..., "message": data}`.

**Frontend code MUST read from `rdata.message.*`**, never flat `rdata.*`
(unless the backend uses the raw `public.return_message` directly, which is
uncommon in V2 code).

Safe pattern for JS callbacks:
```javascript
var payload = (rdata && rdata.message) ? rdata.message : rdata;
if (payload.siteStatus) { ... }
```

### Parameter naming: canonical keys first, aliases as fallback

When frontend and backend disagree on parameter names, the backend should accept
both the canonical name and legacy aliases. The canonical name takes priority.

```python
# In panel_site_v2.py parameter extraction:
deployment_mode = get.get('deployment_mode', get.get('tomcat_deploy_mode', 'shared'))
```

### Runtime dispatch: always case-insensitive

When dispatching on a runtime string (`php`, `tomcat`), always use `.lower()`
on the input value. Legacy frontends may send capitalized values.

### Python safety: never use signal.SIGALRM

`signal.signal(SIGALRM, ...)` is **not process-safe** in any threaded or
gevent-based server. If a timeout is needed, pass it at the HTTP client level
or use a thread-based timer. Under no circumstances should SIGALRM be used
in aaPanel backend code.

### Python safety: avoid nested quotes in f-strings

aaPanel Python 3.10+ f-strings cannot contain `/` or `'` inside the `{}` braces
without workarounds. Prefer `.format()` or extract variables before the f-string.

```python
# Broken in aaPanel's Python environment:
raise ValueError(f'{self.sitePath + '/' + sub_dir}, ...')

# Fixed:
separator_path = self.sitePath + '/' + sub_dir
raise ValueError('{}, ...'.format(separator_path))
```

### Defensive JSON parsing

Always guard `json.loads()` with try/except for `JSONDecodeError`, `TypeError`,
and `ValueError`. In site listing loops (`sitesModel.get_data_list`),
malformed `project_config` rows crash the entire page if not defended.

### Do not mutate DB during read-only GET requests

Listing handlers (`get_data_list`, etc.) are read-only page loads. Adding
`socket.connect_ex()` or `setField()` calls inside the per-row loop adds
latency and mutates DB rows during a GET. Live status checks belong in the
response dict, not in a DB writeback.

## Hardening Toolchain

The hardening suite lives on `feat/aapanel-license-hardening` and consists of:

| Component | File | Purpose |
|-----------|------|---------|
| Patcher | `aaPanel_harden.py` | Anchor-based, idempotent source patcher + runtime guard. Handles UI feature-enablement AND registration hardening. Importable API + CLI. |
| Provisioner | `aaPanel_provision.py` | Automated trial license setup via temporary email (mail.tm / guerrillamail). Validates registration hardening effectiveness. CLI + importable API. |
| Installer | `custom_install.sh` | Surgical deploy — preflight check, apply patches, stage tooling, deploy blocklist, install watchdog. |
| Uninstaller | `custom_uninstall.sh` | Full revert from backup manifest. |
| Watchdog | `watchdog.py` | Singleton daemon that re-applies patches after panel self-upgrade. |
| Test suite | `test_subaccount.py` | Verification — runtime semantics, sentinels, JS patches, registration hardening, blocklist, provisioner. |
| Blocklist | `data/email_domain_blocklist.json` | 1,500+ known non-persistent email domains. |
| Catalog | `data/soft_catalog.json` | Offline plugin-store display metadata (optional). |

### Patch architecture

All source patches follow the same pattern:

- **Anchor-based** — located by content regex, not line numbers. Survives version drift.
- **Idempotent** — marked with `# AAP:<id>`; re-run is a no-op.
- **Fail-loud** — required anchors produce `[FAIL]` and non-zero exit if missing.
- **Version overrides** — `VERSION_OVERRIDES` dict in the patcher handles per-version anchor drift.

```bash
# Standard workflow:
python3 aaPanel_harden.py /www/server/panel --check    # dry-run
python3 aaPanel_harden.py /www/server/panel --apply    # patch + guard
python3 aaPanel_harden.py /www/server/panel --verify   # runtime check
python3 test_subaccount.py /www/server/panel            # full suite
python3 aaPanel_provision.py /www/server/panel          # provision license
```

## Loaded rules

See `.kilo/agent/rules/` for detailed rule files loaded by compatible agents:
- `commit-patterns.md` — cherry-pickable commit methodology
- `python-safety.md` — Python anti-patterns specific to aaPanel
- `v2-contracts.md` — V2 API contract alignment patterns
