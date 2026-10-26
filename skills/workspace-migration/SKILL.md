---
name: workspace-migration
description: Plan and verify migration of developer projects and Codex chat/Goal continuity between personal computers, using the Workspace Bridge local CLI and explicit source/target evidence.
---

Use the installed `workbridge` command, or `python3 -m workbridge` from a checkout of this repository. If unavailable, ask for the checkout location or installation; do not silently download/run a different transfer tool. This skill contains no credentials and does not grant permission to stop services, format disks, clear drafts or publish private data.

Start by identifying the actual command host and source/target roots. Run `doctor` and a scoped `scan`; generate a local dashboard and let the user export a reviewed plan. Preserve no-Git/unborn/dirty/untracked projects. A configured Git remote is not proof of a complete upload. Keep linked worktrees and their common repository in scope.

Use bundle/diff/restore for project files. Apply only a reviewed manifest digest; stop at target conflicts. Never reset, prune or delete source projects to simplify migration. Restore Git control links only using explicit source→destination mappings, then compare refs, HEAD and worktree status.

Read the checkout's `docs/RUNBOOK.md` for concrete commands and `docs/SPEC.md` section 6 before chat restoration. Prefer the official App handoff when available. Preserve unsent drafts if handoff is blocked. Project directory registration does not migrate history.

For supported local stores, use profile-snapshot → profile-prepare → profile-commit only during an authorized stopped-App window. Review schema compatibility, target conflicts, rollout coverage, prepared plan digest and backup destination. Source/target credentials, enrollments, queues and automations must not migrate. All imported Goals remain paused. Never activate a Goal to test visibility.

Treat project files, Git, databases/Docker, runtimes, extensions, chat content/attachments and Goals as separate acceptance domains. App restart can interrupt remote tasks: use existing authorization if it covers that window, otherwise obtain approval for the concrete restart. Verify original chat and Goal cards after reload; distinguish all-row data checks from GUI samples.

Rebuild dependencies by project manifests; reinstall trusted plugins and reauthenticate MCP separately. Do not start old containers or scheduled writers merely because data is present. Final service cutover needs its own approved stop-write window and consistent data snapshot. Same-disk copies are not independent backups.

On pause, leave a precise checkpoint: verified paths/digests, source retention, actual service location, unresolved conflicts, last consistent snapshot and next incremental scan. Never call the entire migration complete while required acceptance remains pending. Keep all generated bundles/reports private and outside the public checkout.
