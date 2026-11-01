# Validation scope

The original local baseline passed32 routing and57 recovery tests. The public package adds installer and portability regression tests. Run the complete current suite with `python3 -m unittest discover -v`; the CI log is the authoritative count for each commit.

Coverage includes:

- Null project membership inferred from actual checkout, nested projects, existing worktree identity and ambiguous/wrong destinations.
- Stable task identity, uncertain creation, stale runtime/app evidence, actual profile mismatch, stop requests and approval boundaries.
- Stronger review bound to current request/revision/artifact hashes; fake or stale receipts rejected.
- Primary/weekly subscription guards, credits/provider rejection, pause/archive/completion, uncertain enqueue and one-use continuations.
- Preview without filesystem writes, custom homes with spaces/reserved characters, private permissions, unrelated hook preservation, conflict refusal, concurrent edit detection during install/rollback, partial write recovery and rollback integrity.
- Activation-compatible reinstall, changed-helper deactivation for renewed trust, and account-free deactivation before rollback.
- Explicit supported profile configuration and read-only metrics on a missing installation.

Public-package local integration was exercised on macOS with stable Codex CLI0.166.0: metadata RPC initialization, six trusted hooks in the existing installation, subscription limit reads, queue/daemon interface availability and enabled skill discovery. These checks do not queue a new model turn. The previously installed live hook definitions remain unchanged by public-package development.

Linux/macOS CI checks local logic using isolated test doubles and temporary homes. It has no access to the author's account, project chats, subscription, hook trust or paid models. Real quota-expiry recovery, arbitrary future CLI versions, native Windows, live Linux product integration, physical devices and model-quality/token-cost savings are not validated by these tests.
