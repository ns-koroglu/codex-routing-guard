# Security and trust boundaries

This project runs with the current user's local permissions. Its ledger, checkpoint hashes and review receipts are workflow checks, not signatures, a sandbox or a defense against a malicious local account. Review code before installing hooks. Installation does not grant chat creation, publication, paid spending or broader filesystem authority.

The installer previews first, refuses symlink targets and conflicting/later edits, preserves unrelated hooks, and creates private backups. Recovery stays disabled until actual hook trust is verified. It requires subscription authentication and never opts into API or paid-credit fallback. Stop/approval/completion and current task identity take precedence over recovery.

Recovery uses documented metadata/queue interfaces where available. It also reads bounded execution records and archive state from local Codex SQLite files in read-only mode; those local layouts can change. Unknown evidence fails closed. No full transcript, authentication file or private state belongs in this repository.

If reporting a vulnerability, use the repository's private vulnerability reporting when available. Do not post credentials, personal paths or real chat data in public issues. If private reporting is unavailable, contact the maintainer through a channel listed on their GitHub profile before disclosing sensitive details. General bugs can use public issues with synthetic reproduction data.

Updates to hook definitions require renewed product trust. This repository never instructs users to bypass that review or silently replace a trusted live helper. Real quota recovery and future Codex compatibility require separate live acceptance.
