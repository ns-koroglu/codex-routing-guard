# Validation scope

Run the complete suite with `python -m unittest discover -v` (`python3` where appropriate). The CI log is authoritative for the count and outcome of each commit. Tests do not spend model credits or modify a real Codex home.

## Platform matrix

| Boundary | Windows | Linux | macOS |
| --- | --- | --- | --- |
| Installer, ledger, recovery state machine and reviewed skill supply | Native local run; CI at Python 3.9/3.13 | CI at Python 3.9/3.13 | CI at Python 3.13 |
| Process locks and crash release | Native `msvcrt` byte lock | Native `flock` in CI | Native `flock` in CI |
| Private file access | Protected DACL, independent `icacls` check and DACL rollback test | POSIX private modes in CI | POSIX private modes in CI |
| Unicode paths, metacharacters and hook exit codes | Real Windows shell subprocess | Real shell subprocess in CI | Real shell subprocess in CI |
| RPC pipe transport | Real fixture process and live metadata check | Real fixture process in CI | Real fixture process in CI; historical live integration |
| Actual quota-expiry recovery / model-quality or cost gain | Not measured | Not measured | Not measured for the public release |

CI configuration is not evidence of a successful run. Consult Actions results for the exact branch/commit before claiming a platform passed. A passing OS matrix verifies local package behavior, not all account/host combinations.

Coverage includes:

- Null project membership inferred from actual checkout, nested projects, existing worktree identity and ambiguous/wrong destinations.
- Stable task identity, uncertain creation, stale runtime/app evidence, actual profile mismatch, stop requests and approval boundaries.
- Stronger review bound to current request/revision/artifact hashes; fake or stale receipts rejected.
- Primary/weekly subscription guards, credits/provider rejection, pause/archive/completion, uncertain enqueue and one-use continuations.
- Preview without filesystem writes, custom homes with spaces/reserved characters, private permissions, unrelated hook preservation, conflict refusal, concurrent edit detection during install/rollback, partial write recovery and rollback integrity.
- Activation-compatible reinstall, changed-helper deactivation for renewed trust, and account-free deactivation before rollback.
- Explicit supported profile configuration and read-only metrics on a missing installation.
- Native process-lock contention and crash release, atomic-write cleanup, real shell metacharacter/Unicode round trips and hook exit codes, real RPC fixture pipes, and socket-free recovery scheduling.
- Content-addressed helper plus shared runtime: a change to either creates new disabled hook definitions for renewed trust.
- Public GitHub threshold/archive/license checks, commit pinning, license retention, bounded file inventory, portable paths, link/reparse-point rejection, review digest, staged drift, exact-byte installation and no overwrite.
- Installed adapter forwarding for ledger, discovery and recovery with real child exit status.

## Live observations

On 1 November 2026, Windows with Python 3.14.4 and stable Codex CLI 0.166.0 exercised read-only metadata initialization, actual subscription-limit reads, and queue/daemon interface help. These checks did not enqueue a model turn, activate hooks or start the recovery daemon. The real home did not contain a complete installed routing runtime; the overall doctor result therefore remained unhealthy despite those successful API checks.

A public-network fixture resolved `vercel-labs/skills/skills/find-skills` to commit `92a9a34ac3bf88e695e468b90c7bf32f163d32c8`, staged and reviewed the files, and installed them into an isolated Codex home. This validates actual discovery/download/install mechanics, not live host activation or task-quality improvement. No downloaded code was executed.

The prior 0.1.0 macOS integration used stable CLI 0.166.0: metadata RPC, six trusted hooks in an existing installation, subscription limits and queue/daemon interfaces. That historical observation does not prove trust or execution for this release's changed hook definitions.

## Remaining acceptance limits

Real desktop dispatch needs the host's project/chat tools. Hook execution needs supported local orchestration and renewed product trust; cloud orchestration can omit command hooks. The package cannot add missing host APIs. See [official hooks documentation](https://learn.chatgpt.com/docs/hooks).

Real quota expiry/resume, live Linux product integration, three-OS app dispatch/recovery in authenticated accounts, future CLI versions, automatic skill activation and matched model-quality/token-cost comparisons remain separate live acceptance work. Unknown evidence fails closed. Local hashes are workflow checks, not a sandbox against a malicious local account.
