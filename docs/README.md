# Documentation

| For | Document |
|---|---|
| Everyone using the app | [USER_WORKFLOW.md](USER_WORKFLOW.md) — how each role works, step by step, with flow diagrams |
| Developers (start here) | [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) — architecture, folders, request flow, rules, adding a feature |
| Fixing a problem | [TROUBLESHOOTING.md](TROUBLESHOOTING.md) — find the cause in 5 minutes; symptom → cause → fix tables |
| Error code lookup | [ERROR_CODES.md](ERROR_CODES.md) — every API error code, message and the file:line that raises it *(generated)* |
| Database | [DATABASE.md](DATABASE.md) — conventions, chart of accounts, posting rules, health-check SQL, safe repairs, backup/restore |
| Tables and columns | [SCHEMA.md](SCHEMA.md) *(generated)* |
| Schema changes | [MIGRATIONS.md](MIGRATIONS.md) — Alembic revisions and how to add one |
| API | [API.md](API.md) — every route with its permission *(generated)* |
| Servers | [DEPLOYMENT_OCI.md](DEPLOYMENT_OCI.md) — Oracle Cloud install, update, backups, operations |

The generated files are rebuilt with `python backend/scripts/gen_docs.py` (run it after changing code).
Diagrams are Mermaid: they render on GitHub / GitLab and in VS Code with a Mermaid extension.
