# Security

Do not put private migration inventories, bundles, database snapshots, chat logs, screenshots, tokens, enrollment URLs or raw configuration files into public issues/PRs.

Report a vulnerability through the repository's private security advisory channel when available. Otherwise open a minimal issue requesting a private contact without technical exploit details or private data.

Threat model: trusted operator migrating their own files on machines they control. Bundles are not encrypted or signed. SHA256 detects accidental corruption relative to a separately trusted digest; it does not authenticate an attacker-supplied bundle. Stop writers and test on isolated copies. A malicious local process racing filesystem changes is outside this version's protection.

The program never invokes a project command or shell, starts a model turn, performs authentication, formats disks or enables network services. The profile adapter deliberately excludes auth, enrollment rows, queues and automations; transcripts and Git history can still contain secrets. Public distribution includes code and synthetic fixtures only.
