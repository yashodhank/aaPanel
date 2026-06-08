# Tomcat/Java/PostgreSQL Runtime Support — Design Note

> **Status:** Phases 1-2 of 4 (Creation + Deployment); Phases 3 (Management) and 4 (Monitoring) remain.

## Request Contract: Java Site Creation

**Route:** `POST /site?action=AddSite`

### Canonical Request Keys

| Key | Type | Required | Values | Notes |
|---|---|---|---|---|
| `runtime` | string | yes | `"tomcat"` | Case-insensitive. Backend also reads legacy `project_runtime` key as fallback. |
| `webname` | string (JSON) | yes | `{"domain":"...","domainlist":[],"count":0}` | Same format as PHP sites. |
| `path` | string | yes | e.g. `/www/wwwroot/myapp` | Project root directory. |
| `port` | int | no | 100-65535 | Default: 8080. |
| `ps` | string | no | | Project description. |
| `tomcat_version` | string | yes | `"8.5"`, `"9"`, `"10.1"`, `"11"` | Must be installed or installable. |
| `java_version` | string | yes | `"8"`, `"11"`, `"17"`, `"21"` | Must be compatible with selected Tomcat. |
| `deployment_mode` | string | no | `"shared"`, `"isolated"` | Default: `"shared"`. Shared = co-hosted on system Tomcat. Isolated = per-project Tomcat. |
| `exposure_mode` | string | no | `"root_domain"`, `"subdirectory"` | Default: `"root_domain"`. |
| `database_engine` | string | no | `"none"`, `"mysql"`, `"pgsql"` | Default: `"none"`. Legacy `tomcat_db_type` accepted. |
| `database_name` | string | conditional | | Required when `database_engine == "pgsql"`. |
| `database_username` | string | conditional | | Auto-generated if empty. |
| `database_password` | string | conditional | | Auto-generated if empty. |
| `deployment_pref` | string | no | `"deploy_later"`, `"upload_war"`, `"server_path"`, `"url"` | Default: `"deploy_later"`. |

### Legacy Key Aliases (still accepted)

| Canonical | Legacy Alias |
|---|---|
| `runtime` | `project_runtime` |
| `deployment_mode` | `tomcat_deploy_mode` |
| `exposure_mode` | `tomcat_exposure` |
| `database_engine` | `tomcat_db_type` |
| `deployment_pref` | `tomcat_deploy_pref` |

Legacy enum values (`"PostgreSQL"`, `"Deploy later"`, `"Upload WAR now"`, etc.) are normalized to canonical values.

## Response Contract: V2 Envelope

All V2 responses use `public.success_v2(data)` / `public.fail_v2(msg)`, which wrap payloads in:

```json
{
  "status": 0,
  "timestamp": 1234567890,
  "message": {
    "siteStatus": true,
    "siteId": 42,
    "runtime": "tomcat",
    "projectName": "example.com",
    "projectPath": "/www/wwwroot/example",
    "deploymentMode": "shared",
    "tomcatVersion": "9",
    "javaVersion": "17",
    "databaseInfo": { ... },
    "deploymentPref": "deploy_later",
    "nextSteps": [ ... ]
  }
}
```

**Frontend consumers must read from `rdata.message.*`**, with fallback to flat `rdata.*` for backward compatibility.

## Deployment Lifecycle

### Shared Deployment

1. WAR is extracted into the system Tomcat's `webapps/<project_name>` directory.
2. Previous webapp directory is backed up (single-level rollback).
3. Tomcat is reloaded or restarted (depending on project type).
4. Health check runs against the configured path/port.
5. On failure: previous webapp is restored automatically.

### Isolated Deployment

1. The primary project runtime is copied to `<project_name>_alt`.
2. The new WAR is deployed to the alternate instance.
3. The alternate instance starts on a different port.
4. Health check validates the alternate instance.
5. On success: Nginx is repointed to the alternate port.
6. On failure: alternate instance is deleted; primary is untouched.

**Critical invariant:** The primary runtime's `server.xml` and port are NEVER mutated during isolated deployment. Instead, `project_config.active_port`, `project_config.active_runtime_home`, and `project_config.active_release_id` track the currently active instance.

## Supported Version Matrix

| Tomcat | Compatible Java | Registry Key | Legacy Model Key |
|---|---|---|---|
| 8.5 | 8, 11, 17, 21 | `"8.5"` | `"8"` |
| 9 | 8, 11, 17, 21 | `"9"` | `"9"` |
| 10.1 | 11, 17, 21 | `"10.1"` | `"10"` |
| 11 | 17, 21 | `"11"` | `"11"` |

**Version normalization:** `VersionNormalizer.normalize_tomcat_version()` maps registry keys (`"8.5"`, `"10.1"`) to legacy major keys (`"8"`, `"10"`) before delegation to `javaModel`.

## PostgreSQL Provisioning

1. Database is created from `database_name` (sanitized: lowercase, alphanumeric + `_` only, max 63 chars).
2. Username and password are auto-generated if not provided; user-specified values are preserved.
3. Database is associated with the project via `pid` in the `databases` table.
4. Connection is scoped to `127.0.0.1/32` by default; configurable via `listen_ip`.

## URL Deployment Security Policy

1. Only `http` and `https` schemes are allowed.
2. DNS resolution resolves all addresses (IPv4 + IPv6) before validation.
3. Rejects: loopback, link-local, private (RFC 1918), carrier-grade NAT, multicast, reserved, IPv6 unique-local.
4. DNS failure = immediate rejection (not treated as safe).
5. Download is capped at 200MB and 120-second timeout.
6. The security check is applied before any outbound request is made.

## Known Limitations (Phases 1-2)

- Browser-side file upload is not yet implemented; the "Server File" tab selects a file from the server's filesystem.
- Isolated deployments use alternate-port cutover, not true blue/green with separate runtime homes.
- Tomcat version 11 deployment requires the legacy model to support it; currently works only if the underlying Tomcat 11 binaries are installed.
- No automatic Java/Tomcat installation from the create wizard; versions must be pre-installed via the Java/Tomcat management pages.
- Java site listing status uses TCP port health checks, not Tomcat JMX/MBean monitoring.
