# aaPanel License Hardening — overview, scope & environment notes

This tooling unlocks aaPanel **Pro UI features** on a **self-hosted, you-own-it**
panel by patching the open-source panel code in place. It is intended for your
own infrastructure only.

## Components

| File | Role |
|------|------|
| `aaPanel_harden.py` | Canonical, anchor-based, idempotent patcher + behavioural runtime guard. Single source of truth for every edit. |
| `custom_install.sh` | Surgical installer — preflight `--check`, applies via the patcher, backs up every touched file, installs the watchdog service. |
| `custom_uninstall.sh` | Full revert from the backup manifest; removes guard + watchdog. |
| `watchdog.py` | Singleton service that re-applies patches if a panel self-upgrade reverts them (delegates to the patcher; never re-implements edits). |
| `test_subaccount.py` | Verification suite (delegates runtime checks to `--check`/`--verify`). |
| `data/soft_catalog.json` | **Optional.** Static plugin-store *metadata* for offline display only (see limitations). |

## What it does (verified on a fresh aaPanel 8.0.3 box)

- `is_pro()` → `True`, `get_not_auth_status()` → `200`
- `get_pd()` → `pro=0` (**Lifetime**) — forced unconditionally, so a cold license
  cache still shows Pro
- Neutralises the `/binds` trial-signup redirect (variable-agnostic JS regex that
  survives per-build minification — handles both observed bundle variants)
- Plugin store **renders** the offline catalog (41 entries)

All edits are anchor-based (survive version drift across 8.0.3 / 8.10.0 / the
"2.19.0 Pro" display build), idempotent (`# AAP:<id>` markers), and **fail loud**
if a required anchor goes missing on a future version.

## Scope & limitations — IMPORTANT

This unlocks **local UI/feature gating only**. It does **not**, and this tooling
will not, do the following:

- **Download Pro plugin binaries for free.** Pro plugins are the vendor's
  commercial product, served from aapanel.com's **authenticated CDN**. Downloading
  one requires a valid bound account/license token. On an **unbound** panel the
  store *lists* plugins (from `soft_catalog.json`) but the actual download path
  (`force=True` → `download.aapanel.com`) is rejected by the vendor — this is why
  a fresh box shows fewer options / broken plugin icons than a box bound to a real
  account.
- **Obtain trial/licenses by automation.** Fabricating accounts (e.g. via
  disposable email) to farm trials is out of scope.

### Why two reference environments differ

| | Bound/licensed box | Fresh unbound box |
|---|---|---|
| Pro UI features | yes (via patches) | yes (via patches) |
| Plugin store list | live licensed list | offline `soft_catalog.json` subset |
| Download/install Pro plugins | **yes** (valid token) | **no** (vendor CDN rejects) |
| Plugin icons | load from CDN | may be broken (gated assets) |

To get genuine Pro-plugin downloads, **bind the panel to a real aaPanel
account/trial**. That token is what authorises the vendor's download API; no local
patch can substitute for it.

## Usage

```bash
sudo bash custom_install.sh /www/server/panel      # install
python3 aaPanel_harden.py /www/server/panel --check # dry-run anchor check
python3 aaPanel_harden.py /www/server/panel --verify# runtime verification
sudo bash custom_uninstall.sh /www/server/panel     # full revert
```
