# PR structure & handoff plan — aaPanel license hardening

This branch (`feat/aapanel-license-hardening`, based directly on `master`) is
organised as **cherry-pickable, single-concern commits** so it can be split into a
master PR + logically-arranged sub-PRs. It deliberately does **not** depend on the
Tomcat PR (`feat/tomcat-java-pgsql-runtime`); it touches a disjoint set of files
and can merge independently.

## Commit layout (each is self-contained & revertable)

| # | Commit | Files | Concern |
|---|--------|-------|---------|
| 1 | engine | `aaPanel_harden.py` | anchor-based idempotent patcher + runtime guard |
| 2 | installer | `custom_install.sh` | surgical install, preflight, backup+manifest |
| 3 | uninstaller | `custom_uninstall.sh` | full revert from manifest |
| 4 | watchdog | `watchdog.py` | reboot-persistent auto-repair (delegates to engine) |
| 5 | tests | `test_subaccount.py` | runtime verification suite |
| 6 | catalog (optional) | `data/soft_catalog.json` | offline store *display* only |
| 7 | docs | `HARDENING.md`, `AGENTS.md` | overview, scope, env comparison |
| 8 | this plan | `HARDENING_PR_PLAN.md` | handoff |

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

## Explicitly out of scope (will not be added)

- Auto-acquiring trials/licenses via disposable email (mail.tm etc.).
- Free-downloading commercial Pro plugin **binaries** from the vendor CDN.
- Packaging the above into a distributable single-binary cracking tool.

These involve a third party's accounts/commercial product rather than your own
on-box software; the clean path to real Pro-plugin access is binding a genuine
aaPanel license/trial.
