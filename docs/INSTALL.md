# Installation guide

## 1. Server requirements

| | Minimum | Recommended |
|---|---|---|
| Windows | Server 2019 | Server 2022 or 2025 |
| CPU / RAM | 2 cores / 8 GB | 4 cores / 16 GB |
| Disk | 40 GB free | SSD, separate data disk |
| Network | Internet during setup (or `-OfflineDir`) | Static IP, DNS name, TLS certificate |

The setup script installs whatever is missing: Visual C++ runtime, **Python 3.12.7**, **Node.js 20.18.1 LTS**,
**MySQL 8.0.40** (as service `MySQL80`, listening on localhost only) and **NSSM 2.24** (service wrapper). It uses `winget`
when present and the official installers otherwise.

## 2. Run setup

```powershell
# PowerShell as Administrator, from the package folder
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1 [options]
```

| Option | Default | Use |
|---|---|---|
| `-ReverseProxy IIS` | `None` | Put IIS in front (installs URL Rewrite + ARR, creates site `KiswaZaibus` on 80/443) |
| `-Domain ops.example.in` | computer name | Public host name (IIS binding, printed URL) |
| `-TlsPfxPath C:\certs\site.pfx` | none | Certificate: IIS binds it, or the gateway serves https itself when no IIS |
| `-WebPort 8080` | 8080 | Gateway port |
| `-UseExistingMySql` | | MySQL 8 already installed; you are asked for its root password |
| `-MySqlServiceName` | `MySQL80` | Name of the MySQL service |
| `-DbName` / `-DbUser` | `kiswa_zaibus` / `kz_app` | Database and application account |
| `-DemoStudents` | off | Load the 35 cached MSEA demo students (until the ERP sync is live) |
| `-TestMode` | off | Shows sign-in codes on screen. **Test servers only.** |
| `-OfflineDir D:\kz-offline` | | Folder with the installers already downloaded (same file names as in the script) |

Setup is **safe to re-run**: it keeps existing secrets in `backend\.env`, never re-seeds a database that already has data,
and re-registers the services.

### What setup does, in order
1. Checks prerequisites and installs missing ones.
2. Installs MySQL (or uses yours), generates the root password and stores it in `secrets\mysql-root.txt` (Administrators only).
3. Writes `backend\.env` with generated secrets (readable by Administrators and the service account only).
4. Creates the database and the `kz_app` account with **data rights only** (it cannot alter tables or remove the audit guard).
5. Creates the Python environment, installs packages, applies `schema.sql` (as root), loads the go-live data.
6. Builds the gateway and the web app.
7. Registers `KZ-API` and `KZ-Gateway` as automatic services running as **LocalService**, restart on failure, rotating logs.
8. Optional IIS site; firewall rule for the web port only; nightly backup task at 01:30; health check.

## 3. After setup — go-live checklist

- [ ] Sign in as **KZ-IT-001** (Super Admin) and **KZ-HO-001** (Director); each sets a personal password.
      All 17 users start with `Kiswa@2026` and must change it at first sign-in.
- [ ] Fill `SMTP_*` and `SMS_API_*` in `backend\.env` so one-time codes and alerts are delivered, then
      `Restart-Service KZ-API, KZ-Gateway`. Until SMS works, codes also go to the user's email.
- [ ] Razorpay: register `https://<site>/webhooks/razorpay/KG` and `/ZE` in each company's dashboard, put the webhook
      secrets in `RAZORPAY_WEBHOOK_SECRET_KG/ZE`, restart. Live mode stays blocked until approved in the app.
- [ ] Install a certificate (`-TlsPfxPath`) before anyone uses the system outside the office network.
- [ ] Check the first nightly backup in `data\backups` and copy backups off the server (another disk or cloud storage).
- [ ] Run the automated checks: `.\scripts\run-tests.ps1` (uses a separate `kiswa_zaibus_test` database).
- [ ] Make sure the server time zone is **India Standard Time** (`tzutil /g`).

## 4. Ports

| Port | Who listens | Open in firewall |
|---|---|---|
| 8080 (or 80/443 with IIS) | Gateway / IIS | Yes (setup adds the rule) |
| 5000 | API, 127.0.0.1 only | No |
| 3306 | MySQL, 127.0.0.1 only | No |

## 5. Troubleshooting

| Symptom | Check |
|---|---|
| Page does not open | `Get-Service KZ-*`, then `logs\KZ-Gateway-service.log` |
| "The server is starting or unavailable" | `logs\KZ-API-service.log` and `logs\api.log`; MySQL service running? |
| No sign-in code arrives | `SMTP_*` / `SMS_API_*` in `.env`; table `outbox` shows status and last error |
| Sign-in works over https but not http | `COOKIE_SECURE=1` requires https; set 0 only for LAN testing |
| Webhook "bad_signature" | Webhook secret in `.env` must match the Razorpay dashboard for that company |
