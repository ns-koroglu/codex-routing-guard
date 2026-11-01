# Contributing

Use a branch and keep changes focused. Run `python3 -m unittest discover -v`. For routing/recovery changes, add a behavioral regression test for the affected trust boundary or state transition. Use temporary Codex homes and fake queue adapters; never run a test suite against your real task state or spend model credits as part of CI.

Describe the concrete problem, changed behavior, executed checks and remaining integration limits in a pull request. Do not weaken tests to permit stale reviews, approval bypasses, duplicate dispatch or paid-credit fallback. Host capability metadata must be actual and current at runtime, not a committed account snapshot.

Keep `routing/POLICY.md` canonical, `routing/README.md` the operator command reference, and the skill a small adapter. Refresh official compatibility references when APIs change. Never publish user paths, credentials, chat histories, state databases or local backups. See SECURITY.md for vulnerability reporting.
