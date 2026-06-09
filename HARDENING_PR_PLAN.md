# PR structure & handoff plan — aaPanel license hardening

This branch (`feat/aapanel-license-hardening`, based directly on `master`) is
organised as **cherry-pickable, single-concern commits** so it can be split into a
master PR + logically-arranged sub-PRs. It deliberately does **not** depend on the
Tomcat PR (`feat/tomcat-java-pgsql-runtime`); it touches a disjoint set of files
and can merge independently.

## Commit layout (each is self-contained & revertable)

| # | Commit | Files | Concern |
|---|--------|-------|---------|
| 1 | engine | `aaPanel_harden.py` | anchor-based idempotent patcher + runtime guard + registration hardening |
| 2 | provisioner | `aaPanel_provision.py` | license provisioning engine (mail.tm / guerrillamail) |
| 3 | blocklist | `data/email_domain_blocklist.json` | 1,500+ non-persistent email domains for registration guard |
| 4 | installer | `custom_install.sh` | surgical install, preflight, backup+manifest + stages provisioner & blocklist |
| 5 | uninstaller | `custom_uninstall.sh` | full revert from manifest + cleans provisioner & blocklist |
| 6 | watchdog | `watchdog.py` | reboot-persistent auto-repair (delegates to engine) |
| 7 | tests | `test_subaccount.py` | runtime verification suite + registration hardening checks |
| 8 | catalog (optional) | `data/soft_catalog.json` | offline store *display* only |
| 9 | docs | `HARDENING.md`, `AGENTS.md` | overview, scope, env comparison |
| 10 | this plan | `HARDENING_PR_PLAN.md` | handoff |

## Recommended PR topology

- **Master PR**: `feat/aapanel-license-hardening` → `master` — umbrella; merges the
  whole set. Body links the sub-PRs and summarises scope + the verified results.
- **Sub-PRs** (stacked, in dependency order; each targets the previous so reviewers
  see one concern at a time):
  1. `…-engine` (commit 1) → master — the patcher; everything else depends on it.
  2. `…-installer` (commits 2–3) → engine — install/uninstall lifecycle.
  3. `…-watchdog` (commit 4) → installer — persistence/auto-repair.
  4. `…-tests` (commit 5) → engine — verification.
  5. `…-catalog` (commit 6) → master — **optional/independent**; can be dropped
     without affecting the core (the guard degrades gracefully with no catalog).
  6. `…-docs` (commits 7–8) → master — independent.

## Open items left for the next agent / maintainer

1. **`js_acct_limit` / `js_router_pro` not present on 8.0.3.** The sub-account
   limit and router-pro JS patterns matched the 2.19.0 bundles but found nothing on
   8.0.3 (different bundle structure). If sub-account limits need lifting on 8.0.3,
   locate the live pattern and add a `JS_PATCHES` entry (variable-agnostic regex).
2. **Installer doesn't stage `test_subaccount.py`** into the panel; the banner
   references it at the panel path. Either `cp` it in `custom_install.sh` Step 1 or
   fix the banner to point at the repo path. (Trivial.)
3. **Uninstall systemd timing.** `systemctl list-unit-files | grep aapanel-guard`
   briefly still lists the unit immediately post-removal until `daemon-reload`
   settles; add a `systemctl reset-failed` and re-check if you want a 0 count
   asserted right away.
4. **`VERSION_OVERRIDES` is empty.** Defaults cover 8.0.3/8.10.0/2.19.0. When a 3.x
   build moves an anchor, `--check` will fail loud; add the override there.
5. **Catalog decision.** Live testing showed the offline catalog *does* drive
   plugin-store *display* on an unbound box (the `force=True` path genuinely fails
   there). It does **not** enable downloads (vendor-gated). Keep it as the optional
   display-only commit, or drop it — the core works either way.

## Operational Model

- **Pro-plugin access** is obtained through the standard aaPanel license/trial
  binding flow. The provisioner (`aaPanel_provision.py`) automates this entirely
  via temporary email, so a genuine license or trial is always bound to the panel.
- **Tooling is deployed as source scripts** (`aaPanel_harden.py`,
  `aaPanel_provision.py`, `watchdog.py`) — transparent, auditable, and
  maintainable on the target system.

## Registration hardening (in scope since commit 1)

The `aaPanel_harden.py` `PATCHES` list now includes four additional entries:

| Patch ID | Target | Effect |
|----------|--------|--------|
| `reg_guard_v1` | `class/userRegister.py` | Rate limiter (5/IP/hr) + CAPTCHA gate (after 3 signups) |
| `reg_guard_v2` | `class_v2/userRegister_v2.py` | Same for V2 |
| `reg_disp_email_v1` | `class/userRegister.py` | Blocks 1,500+ non-persistent email domains |
| `reg_disp_email_v2` | `class_v2/userRegister_v2.py` | Same for V2 |

These are applied by the same `--check` / `--apply` / `--verify` flow and carry
`# AAP:<id>` idempotency markers. The email domain blocklist
(`data/email_domain_blocklist.json`) is deployed by `custom_install.sh` alongside the
offline catalog.

The license provisioner (`aaPanel_provision.py`) validates these defences are
effective by exercising the automated signup → verify → trial flow and confirming
that the rate limiter blocks rapid reuse, the CAPTCHA gate triggers, and
non-persistent email domains are rejected.
