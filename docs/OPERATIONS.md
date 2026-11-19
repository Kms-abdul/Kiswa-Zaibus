# Operations

## Daily
- Services: `Get-Service MySQL80, KZ-API, KZ-Gateway` — all should be Running (they restart themselves after a crash).
- Health: open `http://localhost:8080/healthz` on the server.

## Backups
- Automatic every night at 01:30 (task "Kiswa-Zaibus nightly backup") to `data\backups\kz-backup-<date>.zip`,
  kept 30 days (`BACKUP_KEEP_DAYS`). Each zip holds a consistent MySQL dump (with the audit triggers), the evidence
  files and a checksum. Result lines go to `logs\backup.log`.
- On demand: `.\scripts\backup.ps1` or `.\scripts\backup.ps1 -To E:\offsite`.
- **Copy backups off the server** (second disk, NAS or cloud). A backup on the same disk does not survive a disk failure.

## Restore
```powershell
.\scripts\restore.ps1 -BackupFile D:\KiswaZaibus\data\backups\kz-backup-20261025-013000.zip
```
It asks you to type the database name, takes a safety backup of the current data first, stops the services,
checks the backup's checksum, restores, and starts the services again. Test a restore on a spare server every quarter.

## Updating to a new release
1. Copy the new package over the install folder. Keep `backend\.env`, `data`, `logs`, `secrets`, `mysql`.
2. `.\scripts\update.ps1` — backs up, stops services, installs packages, applies schema changes, rebuilds, restarts.

## Logs (folder `logs`)
| File | Content |
|---|---|
| `api.log` | API errors with stack traces (rotates at 5 MB × 10) |
| `KZ-API-service.log`, `KZ-Gateway-service.log` | Service console output (rotates at 10 MB) |
| `gateway.log` | Webhooks, scheduler and outbox activity |
| `backup.log` | One line per backup |
| `setup-<time>.log` | Full transcript of each setup run |
| `mysql-error.log` | MySQL server log |

## Common tasks
| Task | How |
|---|---|
| Restart the application | `Restart-Service KZ-API, KZ-Gateway` |
| Change SMTP / SMS / Razorpay settings | Edit `backend\.env` as Administrator, then restart |
| Unlock a user | In the app: Users & access → Change → status Active (approval), or the user uses "Forgot password" |
| Sign everyone out | Change `SECRET_KEY` in `.env` and restart (also invalidates pending sign-in codes) |
| Check audit integrity | App → Audit trail → Verify integrity |
| Remove the application | `.\scripts\uninstall.ps1` (keeps data); `-RemoveDatabase` also deletes the database after a final backup |
