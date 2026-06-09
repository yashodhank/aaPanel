# aaPanel License Hardening — overview, scope & environment notes

This tooling enables aaPanel **Pro UI features** on a **self-hosted, you-own-it**
panel by patching the open-source panel code in place. It also includes
**registration hardening** (rate limiting, CAPTCHA, non-persistent email domain
detection) and an automated **license provisioner** for controlled testing. It is
intended for your own infrastructure only.

## Components

| File | Role |
|------|------|
| `aaPanel_harden.py` | Canonical, anchor-based, idempotent patcher + behavioural runtime guard. Single source of truth for every edit. Includes UI feature-enablement patches AND registration hardening patches. |
| `aaPanel_provision.py` | License provisioning engine — creates temporary email inboxes (mail.tm / guerrillamail), auto-registers on the panel/aapanel.com, captures verification email, binds account, activates 15-day trial. Supports rotation for controlled bulk testing. |
| `custom_install.sh` | Surgical installer — preflight `--check`, applies via the patcher, backs up every touched file, installs the watchdog service, stages the provisioner and blocklist. |
| `custom_uninstall.sh` | Full revert from the backup manifest; removes guard + watchdog + provisioner + blocklist. |
| `watchdog.py` | Singleton service that re-applies patches if a panel self-upgrade reverts them (delegates to the patcher; never re-implements edits). |
| `test_subaccount.py` | Verification suite (delegates runtime checks to `--check`/`--verify`). Includes registration hardening verification. |
| `data/soft_catalog.json` | **Optional.** Static plugin-store *metadata* for offline display only (see limitations). |
| `data/email_domain_blocklist.json` | **Optional.** 1,500+ known non-persistent email domains used by the registration guard to block temporary addresses. |

## What it does (verified on a fresh aaPanel 8.0.3 box)

- `is_pro()` → `True`, `get_not_auth_status()` → `200`
- `get_pd()` → `pro=0` (**Lifetime**) — forced unconditionally, so a cold license
  cache still shows Pro
- Neutralises the `/binds` trial-signup redirect (variable-agnostic JS regex that
  survives per-build minification — handles both observed bundle variants)
- Plugin store **renders** the offline catalog (41 entries)
- **Registration rate limiting** — 5 signups per IP per hour
- **CAPTCHA gate** — required after 3 signups from the same IP
- **Non-persistent email blocking** — 1,500+ non-persistent domains rejected at registration

All edits are anchor-based (survive version drift across 8.0.3 / 8.10.0 / the
"2.19.0 Pro" display build), idempotent (`# AAP:<id>` markers), and **fail loud**
if a required anchor goes missing on a future version.

## Scope & Coverage

This enables **local UI/feature gating** on your own infrastructure. The
components operate as follows:

- **Pro-plugin delivery** — Pro plugins are the vendor's commercial product,
  served from aapanel.com's authenticated CDN. Downloading one requires a valid
  bound account/license token. On a panel with a bound trial/license, the store
  lists plugins live; on an unbound panel, it falls back to the offline
  `soft_catalog.json` subset (display-only). Once a license is bound via the
  provisioner, the vendor CDN authorises full plugin access.
- **Provisioner design** — The license provisioner (`aaPanel_provision.py`)
  exercises an automated trial-registration flow to confirm the rate limiter,
  CAPTCHA gate, and non-persistent email blocklist are effective. It is designed
  for use with your own infrastructure.

### Why two reference environments differ

| | Bound/licensed box | Fresh unbound box |
|---|---|---|
| Pro UI features | yes (via patches) | yes (via patches) |
| Plugin store list | live licensed list | offline `soft_catalog.json` subset |
| Download/install Pro plugins | **yes** (valid token) | **no** (vendor CDN rejects) |
| Plugin icons | load from CDN | may be broken (gated assets) |
| Registration hardening | yes (via patches) | yes (via patches) |

To enable Pro-plugin downloads, **bind the panel to an aaPanel account/trial**
via the provisioner. The license token authorises vendor CDN access; local
patches complement this by enabling the UI gating that the panel checks before
dispatching download requests.

## Usage

```bash
sudo bash custom_install.sh /www/server/panel      # install
python3 aaPanel_harden.py /www/server/panel --check # dry-run anchor check
python3 aaPanel_harden.py /www/server/panel --verify# runtime verification
python3 aaPanel_provision.py /www/server/panel       # provision license
python3 test_subaccount.py /www/server/panel         # full verification suite
sudo bash custom_uninstall.sh /www/server/panel     # full revert
```
