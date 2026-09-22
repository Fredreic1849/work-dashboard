# Worklog v1 contract

Data repo: `projects.json` is `{schema_version:1, projects:[{id,name,goal?}]}`.
Each device writes only `devices/<device_id>/days/YYYY-MM-DD.jsonl` and `snapshot.json`.
Snapshot: `{schema_version:1,device:{id,name},generated_at,days:[YYYY-MM-DD],events:[Event],issues:[{code,message}],coverage_start}`.
Snapshot events include the last 30 days plus all current idea/checkpoint/revision/tombstone records needed to render long-lived state. Read older daily logs on demand. Events duplicated between devices have the SAME logical id.

Event: `{schema_version:1,id,kind,project_id,occurred_at,day,title,summary,result,next_action,priority,evidence_level,source,evidence,idea_status?,hypothesis?,validation?,supersedes?}`.
Kinds: activity, worklog, idea, checkpoint, revision, tombstone. priority P0/P1/P2. evidence_level unverified/local/remote.
source: `{kind:manual|codex|git,thread_id?,turn_id?,commit?,device_id}`. No provider credentials, raw transcripts or absolute paths.
evidence: array of `{id,label,device_id,digest?,checked_at?,kind:local|remote|reference,url?}`. IDs opaque, local paths stay in local SQLite only. Remote evidence must explicitly support any remote claim. Missing evidence forces evidence_level unverified.
`supersedes` is an array of prior event IDs. Revisions copy the full replacement event payload. Concurrent leaf revisions remain visible as conflicts. Tombstones suppress referenced records but remain stored. Frontend merges by id, flags same-id/different-content conflicts ignoring only source.device_id differences.
All text is rendered as text (no raw HTML/Markdown). Titles max 160 chars, summary/result/next_action/hypothesis/validation max 2000 each. Cloud content rejects credential patterns, internal addresses, absolute paths, long/code-fenced payloads, unknown fields. Project privacy is LOCAL configuration: auto or review or local_only. Defaults local_only; JD/business projects review or local_only. Unclassified source emits only local issue.

CLI state location (overridable with --home / WORKLOG_HOME): ~/Library/Application Support/Worklog. config.json stores device id/name, repo clone path, registered codex homes and local project mappings [{id,name,paths,policy}]. SQLite stores events, upload eligibility, evidence locations, cursors and issues. Never store model or GitHub tokens there.

Python public contracts (root owns core.py/cli.py; sync agent owns collector.py/git_sync.py):
- `core.Store(home: Path)` properties .home, .config, .db (sqlite3 connection); schema managed in core.
- `Store.add_event(event:dict, eligible:bool=False) -> str` validates/sanitizes and persists immutable event, idempotent; core determines project policy so caller cannot bypass local_only/review by setting eligible. Optional `approved=True` only via explicit local review.
- `Store.events(eligible_only=False) -> list[dict]` returns all local events (eligible_only selects exportable, including synced).
- `Store.pending() -> list[dict]`, `Store.mark_synced(ids:list[str])`, `Store.issue(code,message)`, `Store.clear_issue(code)`; sanitized generic issues only.
- `Store.cursor(key) -> dict|None`, `Store.set_cursor(key,value)`; cursor JSON supports offset/identity.
- `Store.project_for_path(path) -> dict|None` uses longest explicit path match (worktree aliases registered explicitly).
- `Store.save_config()`, `Store.get_project(project_id)`.
- `core.make_event(kind,project_id,title, **fields)` fills defaults, UTC timestamp+Asia/Shanghai day, UUID id unless source logical id supplied.
- `core.resolve_events(events)` mirrors frontend revision/tombstone semantics.
- `collector.scan(store, days=7) -> dict` collects metadata-only Codex and Git activity, persists cursor after full JSONL lines; never calls models or exports raw final text. Handles inherited history, duplicate meta, archival, partial lines, and visible unsupported schemas.
- `git_sync.sync(store, retries=3) -> dict` exports eligible records only, commits only own directory, fetch/rebase/push, confirms remote receipt before mark_synced; locks per local clone, no force pushes. `git_sync.backup(store) -> dict` weekly git bundle and restore verification helper.

Frontend: docs/index.html/styles.css/app.js/data.js/demo.json. Native JS modules (no runtime deps). api.github.com GET only; token ONLY in memory. Verify /user numeric ID against configured/entered owner ID and /repos/<owner>/<repo> private===true, permissions.pull. Resolve main commit SHA then read project catalog/tree/device snapshots at that exact SHA. Fetch history daily files at fixed SHA on demand. 5-minute visible-tab polling, manual refresh, logout clear all. 401/403 revocation clears data; 429/network show stale state, bounded retries/no busy loops. Demo is explicit and synthetic, never mixed with real data. No localStorage/sessionStorage/service worker or analytics.
