# Server Backup Retention Handoff (2026-09-24)

## Goal and Scope

- Reduce local SQLite backup accumulation without deleting the live `/data/radar.db`, Docker volumes, `.env` backups, or `CACHED`.
- Keep `lp` stopped. Do not start it unless the user explicitly requests it.
- Branch: `codex/frontend-localization-polish`; implementation baseline `f36a80c`, server baseline `88d0564`.

## Implementation

- Commit `4acd2f603e14842a0358275ecf1746a4df51481f` adds `deploy/backup_retention.py`, tests, and a call from `deploy/linux-update.sh` after a successful Compose update. Push to the current branch succeeded.
- Each deployment still creates and validates a pre-update SQLite backup. Automatic retention then keeps the latest three, one per UTC day for the last seven days, and one per ISO week for the preceding four weeks. Only backups with the deployment script's `radar-before-<12-hex>-<UTC-time>.db` naming pattern are pruned automatically. Named manual backups are excluded.
- Preview is the default. `--apply` deletes selected `.db` files only; `--include-legacy` is required to include older named snapshots. `--protect BASENAME` preserves a special snapshot, and `--expect-delete-count` aborts if the reviewed selection changes. The live volume and `.db-wal`/`.db-shm` sidecars are not touched.

## One-Time Cleanup

- The reviewed legacy preview selected 33 of 43 `.db` files for deletion. `--include-legacy --protect radar-20260921T003355Z-pre-restore-ranked-opportunities.db --expect-delete-count 33 --apply` completed successfully as the normal server user.
- Deleted bytes: `21,745,082,368` (about 20.25 GiB). Ten database snapshots remain; the backup directory is about 6.2 GiB. Root filesystem changed from 50 GiB total, 41 GiB used, 6.4 GiB available (87%) to 22 GiB used, 26 GiB available (47%).
- Retained recent deployment points: `radar-before-88d05647bce5-20260924T053011Z.db`, `radar-before-6926681fb8a4-20260924T051046Z.db`, `radar-before-f3472516552b-20260924T035220Z.db`.
- Retained daily/older points: `radar-20260923T073854Z-pre-hide-astro-route-panel.db`, `radar-20260922T133053Z-pre-lighter-rh-lighter.db`, `radar-20260921T115549Z-pre-position-dust-filter.db`, `radar-20260920T123520Z-pre-rh-lighter-visibility.db`, `radar-preadd-20260916T030333Z.db`, `radar-pre-oil-news-d9f2fc3.db`.
- Special protected point: `radar-20260921T003355Z-pre-restore-ranked-opportunities.db`.

## Verification and Production State

- `PRAGMA quick_check=ok` on the newest retained backup, oldest retained backup, and protected pre-restore backup. The newest backup SHA-256 is `8f5fc805d02a1b4815859d25e5185c77bb22aa1ce95ecc4d608cdb64f2cabf5a`.
- Local and server Python unit tests: 3 passed. Git Bash and server Bash syntax checks passed. Production automatic-policy preview: zero of three eligible deployment backups selected for deletion.
- The server repository used `git pull --ff-only` and reached `4acd2f603e14842a0358275ecf1746a4df51481f`. No application images were rebuilt or restarted for this operations-only script change, avoiding an unnecessary new backup. Backend and frontend still run healthy images tagged `88d05647bce57a668cf0596a9d142f1117a4ef6a`; `/api/health` reports `status=ok`.
- All three `lp` containers remain exited with actual Docker restart policy `no`. The `lp` Compose file still specifies `unless-stopped`; a future explicit `docker compose up` in that project would restart them, so do not run it without user authorization.

## Residual Risks and Next Steps

- These are local restore points, not off-host disaster recovery. No off-host copy was made during this task; host failure can still remove the live database and all remaining local backups.
- The retention rule bounds the number of full deployment snapshots; it is not incremental or block-level deduplication. Manual snapshots remain outside automatic pruning and need deliberate review if new ones are created.
- No live database rows were deleted or compacted. Old second-level samples remain in the live database; changing that requires a separate, backed-up maintenance operation with sufficient free disk space.
- Unrelated working-tree and untracked files were preserved. Stage only this handoff file for its documentation commit; do not stage other task artifacts.
