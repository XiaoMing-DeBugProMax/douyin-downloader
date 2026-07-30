# 规格周期 1：抖音本地档案基础与持久任务

- 状态：用户已批准
- 日期：2026-07-30
- 产品：抖音本地授权媒体下载管理器
- 规格范围：现有 Windows 本地应用的第一个增量演进周期

## Problem Statement

现有应用已经能够安全地解析一个抖音公开视频，并通过浏览器将无水印 MP4 下载到用户选择的位置。但是，这种快速下载不会记录文件位置、检查下载完整性、保存配套成果，也无法在应用关闭后恢复。因此，用户下载较多作品后，需要自己处理重名、文件散落、缺失封面、重复下载、失败重试和打开所在文件夹等问题。

用户希望在不破坏现有快速下载体验的前提下，将单个作品加入由应用管理的本地档案。应用应直接把视频、封面、筛选后的元数据以及可选音频和文案写入用户授权的归档根目录，持久记录任务与档案状态，并提供进度、暂停、取消、恢复、重试、修复和打开所在文件夹等能力。

这个周期必须先建立可靠的托管归档、持久任务、SQLite、本地档案库、设置、托盘和文件完整性基础。作者主页、公开视频合集、多来源扫描和隔离授权会在后续规格周期复用这些基础，而不是与本周期一起进行高风险重写。

## Solution

在现有原生网页和 Windows 启动器中增加顶部工作区导航，保留“快速下载”的现有单视频流程，并在解析结果下方增加独立的本地归档操作。用户第一次归档时选择归档根目录和归档方案，之后可以把当前作品加入托管归档。

应用创建一个包含单一来源任务和单一作品任务的归档操作。任务持久化到 SQLite，由托管归档模块负责重新解析当前远端资源、选择最高可靠码率、下载到 `.part`、校验媒体完整性、原子改名、生成配套成果并登记本地档案。应用重启后不会自动联网恢复，而是把未完成任务标记为已中断，等待用户主动继续。

任务中心用于观察和控制执行过程，本地档案库用于管理长期成果，设置工作区用于管理归档根目录、基础名称模板、归档方案、并发重试和隐私诊断。活动归档存在时，关闭控制窗口可以选择最小化到系统托盘继续运行。

## User Stories

1. As a 快速下载用户, I want the existing single-video parsing and browser download journey to remain unchanged, so that the expansion does not remove the simplest use case.
2. As a 快速下载用户, I want to see whether the parsed work is already archived, so that I can avoid accidental duplicate work.
3. As a 快速下载用户, I want an independent “加入本地归档” action below the existing download actions, so that browser download and managed archive remain clearly separated.
4. As a first-time archive user, I want to choose an archive root before files are written, so that the application only uses a location I explicitly authorized.
5. As an archive user, I want the application to remember one current default archive root, so that future archive operations require less setup.
6. As an archive user, I want changing the default root to affect only future operations, so that existing archives are not silently moved.
7. As an archive user with an external drive, I want an unavailable old root to be shown as “位置不可用”, so that temporary disconnection is not misreported as corruption.
8. As an archive user, I want to manually relocate an archive and have it revalidated, so that I can recover after moving files outside the application.
9. As an archive user, I want a fixed author/year/work directory layout, so that archives remain predictable and repairable.
10. As an archive user, I want every work directory to include the stable work ID, so that renames and duplicate titles cannot merge unrelated works.
11. As an archive user, I want author directories to include a stable author identity, so that authors with the same nickname remain distinct.
12. As an archive user, I want to customize a supported base-name template, so that files remain recognizable without compromising path safety.
13. As an archive user, I want invalid Windows filename characters and trailing spaces or dots removed automatically, so that archive creation is reliable.
14. As an archive user, I want path length and reserved-name handling to be automatic, so that long descriptions do not break an operation.
15. As an archive user, I want the default archive profile to include MP4, cover and filtered metadata JSON, so that each archived work is useful without extra configuration.
16. As an archive user, I want to optionally extract the complete video soundtrack to M4A, so that I can listen to exactly what is heard in the video.
17. As an archive user, I want audio extraction to use stream copy without re-encoding, so that it is fast and does not reduce quality.
18. As an archive user, I want a video with no audio track to report “无音轨” instead of failing the whole archive, so that silent videos can still be complete.
19. As an archive user, I want to optionally export the original work description to TXT, so that the author’s text is easy to use locally.
20. As an archive user, I want platform subtitles and speech transcription to remain clearly separate from description export, so that generated and source-provided text are not confused.
21. As a privacy-conscious user, I want metadata JSON to contain only filtered stable information and declared snapshots, so that it does not become a dump of the platform response.
22. As a privacy-conscious user, I want metadata JSON and SQLite to exclude cookies, authorization data, signed media URLs and comments, so that archives do not leak sensitive or short-lived data.
23. As an archive user, I want local artifact paths in metadata JSON to be relative, so that archives remain understandable after a root is relocated.
24. As an archive user, I want artifact size, MIME type and SHA-256 recorded, so that local integrity can be checked later.
25. As an archive user, I want the application to select the highest reliable `play_addr` bitrate rather than a fixed first entry, so that archived video uses the best available source.
26. As an archive user, I want multiple URLs for one bitrate treated as CDN mirrors, so that the application can retry mirrors without inventing watermark variants.
27. As an archive user, I want downloads written to `.part` files and promoted atomically, so that incomplete bytes never look like completed media.
28. As an archive user, I want MP4 structure, video stream, duration and size checked before completion, so that an HTML error page or truncated file cannot enter my archive.
29. As an archive user, I want covers to be decoded and validated, so that a nominal image response is not enough to mark the result complete.
30. As an archive user, I want extracted audio stream and duration validated, so that a broken M4A is detected.
31. As an archive user, I want metadata JSON validated against its schema before registration, so that local rebuilding remains dependable.
32. As an archive user, I want a work marked archived only when every requested artifact passes its check, so that completeness reflects my selected archive profile.
33. As an archive user, I want optional artifacts added later without redownloading a valid MP4, so that changing the archive profile does not waste bandwidth.
34. As an archive user, I want a missing or corrupt requested artifact to show “待修复”, so that I can distinguish repair from a new download.
35. As an archive user, I want an explicit repair action, so that the application can recreate only missing or invalid artifacts.
36. As an archive user, I want force rearchive to remain a secondary explicit action, so that normal use does not overwrite healthy files.
37. As an archive user, I want the application to estimate required space with a safety margin before starting, so that large files do not unexpectedly fill the disk.
38. As an archive user, I want the operation to pause when the configured disk reserve is threatened, so that the application does not consume the final available space.
39. As an archive user, I want unknown remote size clearly labeled and explicitly confirmed, so that uncertainty is not represented as a false precise estimate.
40. As an archive user, I want current progress, downloaded bytes and percentage when reliable, so that I know whether work is advancing.
41. As an archive user, I want speed and ETA shown only when they are meaningful, so that unstable estimates do not mislead me.
42. As an archive user, I want transient network failures retried with bounded jittered backoff, so that temporary problems can recover automatically.
43. As an archive user, I want invalid, deleted, permission, disk and local file errors not retried blindly, so that permanent failures remain understandable.
44. As an archive user, I want to pause an active work at a safe chunk boundary, so that I can stop network and disk activity without discarding reusable progress.
45. As an archive user, I want pause to remain distinct from application interruption, so that intent is visible after restart.
46. As an archive user, I want to cancel an operation and choose whether to retain unfinished parts, so that cancellation matches my storage preference.
47. As an archive user, I want already completed artifacts preserved when a larger operation is cancelled, so that cancellation does not undo successful work.
48. As an archive user, I want an interrupted operation to remain stopped after restart, so that the application never resumes network activity without my action.
49. As an archive user, I want to continue an interrupted operation explicitly, so that remote resources and local parts are revalidated before reuse.
50. As an archive user, I want Range resume used only when the remote resource can be trusted to match the retained part, so that resumed files cannot silently combine different content.
51. As an archive user, I want completed and failed operation history retained until I clear it, so that I can review past outcomes.
52. As an archive user, I want active, paused or interrupted records protected from history cleanup, so that cleanup cannot orphan running work.
53. As an archive user, I want clearing task history not to delete archive files or library entries, so that execution history and long-term results remain separate.
54. As an archive user, I want the task center to show operation, source and work levels, so that later batch features can use the same task model.
55. As an archive user, I want single-work operations to use the same three-level model internally, so that the application does not maintain a second state machine.
56. As an archive user, I want lifecycle, execution phase and final result displayed separately, so that “paused”, “verifying” and “partial success” do not conflict.
57. As an archive user, I want the local archive library to show one row per work, so that multiple attempts do not create duplicate library entries.
58. As an archive user, I want to open a work’s directory from the result, task center or library, so that I can reach the actual files immediately.
59. As an archive user, I want deleting an archive to move its directory to the Windows Recycle Bin before removing its record, so that ordinary deletion remains recoverable.
60. As an archive user, I want manually missing files detected as invalid rather than silently forgotten, so that I can choose repair or removal.
61. As an archive user, I want SQLite backed up before migrations and once per day at first startup, so that local state can recover from corruption.
62. As an archive user, I want the last seven daily SQLite backups retained, so that recovery has recent choices without unlimited accumulation.
63. As an archive user, I want a corrupt database isolated rather than overwritten, so that recovery attempts do not destroy the only remaining evidence.
64. As an archive user, I want core library records rebuildable from filtered metadata JSON, so that media archives remain useful even if task history cannot be recovered.
65. As an archive user, I want the current operation to keep its confirmed settings after global defaults change, so that resume and retry remain deterministic.
66. As an archive user, I want configurable download concurrency from one to five with a default of three, so that I can balance speed and resource use.
67. As an archive user, I want retry count and archive profile stored in settings, so that common preferences do not need repeated entry.
68. As an archive user, I want three themes and responsive behavior retained across all new workspaces, so that the expanded application still feels like the original product.
69. As a keyboard user, I want workspace navigation, dialogs and task actions to have visible focus and predictable order, so that the new interface remains accessible.
70. As an archive user, I want closing the idle control window to stop the application as before, so that the lifecycle remains familiar.
71. As an archive user with active work, I want close to offer tray continuation, stop-and-exit or cancel, so that a normal close cannot accidentally terminate a long archive.
72. As an archive user, I want tray actions to reopen the application, open the task center, pause all work or stop the application, so that background work stays controllable.
73. As a Windows user, I want FFmpeg and ffprobe bundled with the application, so that audio extraction and probing require no external installation.
74. As a Windows user, I want the bundled FFmpeg build and license provenance visible, so that the distributed binary remains auditable.
75. As a security-conscious user, I want the local server to remain bound only to `127.0.0.1`, so that archive management is not exposed to the LAN.
76. As a security-conscious user, I want archive routes protected by the existing local session and same-origin rules, so that an external page cannot start or delete local work.
77. As a security-conscious user, I want archive paths constrained to approved roots and protected from traversal and reparse-point escape, so that remote metadata cannot redirect writes.
78. As a security-conscious user, I want logs to contain only operation names, stable anonymous error codes, durations and byte counts, so that share text, media URLs and local secrets are not exposed.
79. As an existing user, I want the original validated single-video sample and download behavior to remain part of UAT, so that archive work cannot regress the shipped capability.
80. As a future batch-download user, I want the archive foundation to expose stable module interfaces, so that homepage and collection scanning can be added without rewriting file, task or library behavior.

## Implementation Decisions

- The existing Windows launcher, loopback-only FastAPI process, local session, native HTML/CSS/JavaScript frontend and quick download path are evolved in place.
- The application remains a single process. No separate backend, worker daemon, cloud service or embedded copy of the reference project is introduced.
- Top-level navigation uses five workspaces: 快速下载、批量下载、任务中心、本地档案库、设置. In this cycle, batch source scanning is not active; the workspace may explain that it arrives in the next specification without accepting source URLs.
- The existing “下载视频” and “选择保存位置” actions remain. A separate archive strip beneath the parsed result reports archive state and offers archive, supplement, repair, task, folder and library actions as applicable.
- The quick download module remains independent of SQLite and persistent tasks. A failure in managed archive initialization must not remove the existing browser download capability.
- A deep managed archive module is added. Its external interface covers starting a single-work archive, controlling an operation or task, and returning immutable operation snapshots. Callers do not orchestrate retries, state transitions, files or database writes.
- The managed archive module uses one internal task model for single archive, supplement and repair operations: archive operation → source task → work task. Single-work operations contain exactly one source task and one work task.
- Lifecycle, execution phase and result are independent persisted fields. Pause and interruption are resumable states; cancellation is terminal; partial success is an operation result rather than an execution phase.
- Work identity is the Douyin `aweme_id`. Author and mutable presentation metadata do not create a new work.
- The parser’s internal normalized projection is extended as needed for archive identity, publish time, complete bitrate candidates, cover candidates, duration, public page identity, author identity and filtered music metadata. Existing quick-download response fields and behavior remain compatible.
- `play_addr` is the watermark-free playback source contract. All bitrate entries are considered, the highest reliable candidate is preferred, and URL lists within a candidate are treated as CDN mirrors.
- Signed or short-lived media URLs remain in memory only. Persisted tasks store stable work identity and resolve current remote resources at execution or manual resume.
- SQLite resides in the current Windows user’s local application data area. It stores schema version, archive operations, source tasks, work tasks, works, authors, archive roots, archive items, artifacts, settings snapshots, current settings and recovery metadata.
- SQLite never stores authorization credentials, browser storage, signed media URLs, complete upstream responses, comments or downloaded media bytes.
- Database migrations are transactional and create a backup first. On the first startup of each local day, a database-only backup is created; the latest seven daily backups are retained.
- A corrupt database is moved aside. Recovery can restore a selected valid backup or rebuild the core archive library from filtered metadata JSON; full task history recovery is not guaranteed.
- The application stores one current default archive root. Each archive item retains its original root identity and relative directory. Changing the default does not move existing archives.
- An unavailable root is reported as location unavailable. It does not automatically convert its items to missing or corrupt.
- The fixed directory structure is author identity / publication year / work directory containing the work ID. The exact display-safe author segment can include nickname but must retain stable author identity.
- Users can configure only an artifact base-name template using a whitelist of stable fields. Templates cannot inject directory separators or arbitrary path expressions.
- Every write path is normalized and revalidated beneath an authorized archive root. Traversal, reserved device names, unsafe lengths, symlink escape and Windows reparse-point escape are rejected or safely normalized.
- The default archive profile requires MP4, cover and filtered metadata JSON. Optional artifacts are complete video soundtrack M4A and original description TXT.
- The audio artifact is produced from the archived video stream using FFmpeg stream copy without re-encoding. Platform-associated music is not substituted for the video soundtrack.
- No audio track is a valid artifact outcome and does not fail an otherwise complete archive.
- Metadata JSON includes schema and generation time; work identity, type, page, description, tags, publish time and duration; stable author metadata; public interaction-count snapshot with timestamp when available; stable music metadata; relative artifact paths, size, MIME and SHA-256; and discovery/operation provenance.
- Metadata JSON excludes secrets, signed media URLs, full upstream responses, comments and authorization data. Missing fields are null or omitted according to the schema.
- Each artifact is written to a `.part` path. Final names become visible only through an atomic replace after artifact-specific validation succeeds.
- MP4 validation checks trusted content type or signature, a video stream, duration, expected length when reliable and SHA-256. Cover validation decodes the image. Metadata validation checks the schema. Audio validation checks the audio stream and duration.
- An archive item becomes complete only when all requested artifacts have passed validation. Missing optional artifacts can be supplemented without redownloading valid required artifacts.
- Corrupt temporary files are deleted after bounded retries. Paused or interrupted partial files may remain if their provenance is sufficient for safe resume.
- Range resume is attempted only when validators or equivalent evidence show that the remote content matches the retained part. Otherwise the artifact restarts cleanly.
- Before work begins, required free space is estimated as known remaining bytes plus ten percent safety margin, while retaining at least one GiB free. Unknown size is shown as unknown and requires confirmation.
- Available space is monitored while running. Crossing the reserve pauses affected work with an actionable disk error.
- Download concurrency defaults to three and is configurable from one to five. This limit is shared by simultaneously active single-work operations.
- Transient connection, timeout, rate-limit and upstream server failures retry at most three times with jittered exponential backoff. Invalid input, missing/private content, permission, disk, path and deterministic integrity errors do not retry blindly.
- Operation, source and work progress include counts and byte totals. Percentage, speed and ETA are returned only when denominators and sampling are reliable.
- User pause stops at a safe chunk boundary and retains reusable parts. Stop-and-exit marks unfinished work interrupted. Manual continue re-resolves remote resources before deciding whether a part can be reused.
- Cancellation is terminal. Queued work is cancelled, active work stops safely, completed artifacts remain, and the user chooses whether unfinished parts are deleted or retained.
- Task history has no automatic time-to-live in this cycle. Users may clear terminal history; active, paused and interrupted records cannot be cleared. Clearing history never deletes archive items or artifacts.
- The local archive library has one current entry per work. It reports archive profile, artifact status, root, location availability and integrity state independently of task history.
- “打开所在文件夹” resolves the recorded work directory and delegates to Windows only after confirming it remains beneath the registered root.
- Deleting a local archive moves the work directory to the Windows Recycle Bin first and removes the archive record only after the move succeeds. Ordinary UI does not expose permanent delete or record-only delete.
- Manual file loss marks artifacts invalid and the archive item repairable. Users can repair requested artifacts or remove the invalid archive through the consistent delete flow.
- The application bundles audited FFmpeg and ffprobe executables that are compatible with LGPL distribution and do not enable GPL or nonfree components. Release material records source, version, build options, license and corresponding-source access.
- FFmpeg receives only application-generated local paths and fixed arguments. Work descriptions and filename templates never become commands.
- The control window preserves idle close behavior. With active archive work, close offers tray continuation, stop-and-exit and cancel. Tray continuation keeps the same application process alive.
- Tray actions include reopen, open task center, pause all and stop. The application is not installed as a Windows service and does not run after an explicit stop.
- Web routes remain thin and enforce existing Host, local session and same-origin protection. They translate inputs and snapshots but do not operate SQLite, FFmpeg or archive files directly.
- Media URLs are never returned to the frontend, persisted, or logged. Logs retain the established minimal operation/error/duration/byte shape.
- The application’s runtime and archive routes remain reachable only through `127.0.0.1` or the validated localhost authority for the current port. LAN and public binding remain prohibited.
- Every operation stores a settings snapshot containing archive profile, root, naming, concurrency and retry values. Later global setting changes affect only new operations.
- The existing three visual themes, brand identity, hero/card language, responsive layout and accessible keyboard interactions extend across the new workspaces.
- Source scanning, scan drafts, author homepage collection, public video collection, multi-source operations and isolated authorization are deliberately deferred to later specifications, while the module and three-level task model remain ready for those adapters.

## Testing Decisions

- Good tests observe behavior through a module interface or the local HTTP interface. They do not assert private class structure, SQL statement wording, internal helper calls or exact scheduling order unless order is part of the user-visible contract.
- The highest primary test seam is the managed archive module’s external interface. Tests start operations, issue controls and inspect returned snapshots and filesystem/database outcomes through that interface.
- The existing quick download test seam remains `ParseService` plus the local parse/download HTTP routes. Existing tests for one transient retry, anonymous logging, media URL redaction, stream behavior and token expiry remain prior art and mandatory regression coverage.
- External Douyin and media behavior is replaced with deterministic test adapters that can model multiple bitrate candidates, CDN mirror failure, missing content, timeouts, rate limiting, truncated streams, validator changes and Range support.
- SQLite is exercised as a real temporary local database rather than mocked. Filesystem behavior uses real temporary directories, with targeted Windows-specific tests for reparse points, reserved names, Recycle Bin integration and folder opening.
- The managed archive interface is tested for single archive success, already complete skip, optional supplement, repair, force rearchive, cancellation, pause, interruption, manual continue and retry.
- State-transition tests cover valid and rejected transitions across lifecycle, execution phase and result at operation, source and work levels.
- Fault-injection tests cover process interruption after each durable transition, ensuring restart produces either a valid final artifact or a resumable/interrupted record, never an unregistered final file.
- Artifact pipeline tests cover `.part` creation, safe cleanup, atomic promotion, MP4 probing, length mismatch, hash calculation, cover decode, metadata schema validation, audio stream copy, no-audio outcome and FFmpeg failure isolation.
- Resume tests verify that retained parts are reused only with trustworthy matching remote evidence and are discarded when validators, length or identity differ.
- Disk tests cover known and unknown estimates, ten-percent margin, one-GiB reserve, mid-download exhaustion and unavailable archive roots.
- Deduplication tests cover repeated archive requests for the same `aweme_id`, complete items, missing artifacts, corrupt artifacts, unavailable roots and explicit force rearchive.
- Database tests cover schema creation, transaction rollback, backup-before-migration, daily backup retention, corruption isolation, restore and archive-library rebuild from metadata JSON.
- Security tests extend existing URL, Host, Origin, session and CDN checks with archive-root traversal, unsafe template fields, reserved paths, symlink/reparse escape, forged artifact paths and unauthorized destructive requests.
- Sensitive-data tests scan logs, SQLite, metadata JSON, settings snapshots, backups and packaged artifacts for cookies, authorization tokens, launch tokens, signed media URLs and forbidden test fixtures.
- HTTP integration tests follow existing FastAPI ASGI test patterns with injected modules. They verify public response projections never expose media URLs, absolute archive paths beyond user-facing needs or internal errors.
- Launcher integration tests extend existing loopback, duplicate-start and cleanup coverage with active-task close choices, tray continuation, stop-and-exit interruption and resource release.
- Browser tests extend the current Playwright journey with five-workspace navigation, archive-state strip, first-root selection, operation progress, task controls, library folder action, settings persistence, themes, responsive behavior and keyboard focus.
- Packaged-resource tests verify that the application contains required static assets, FFmpeg, ffprobe, license notices and no forbidden credential/config files.
- The full test gate remains pytest, Ruff and strict mypy. The build additionally runs the sensitive-data gate before and after PyInstaller packaging.
- Real Windows UAT preserves the established single-video baseline and adds archive output verification, playback/probing, cover decode, metadata schema and SHA, optional M4A, description TXT, interruption/restart/manual continue, tray behavior, open folder, Recycle Bin deletion, root unavailability and database restore.
- Real watermark monitoring continues to use work identity, duration, valid MP4 structure, hash when stable and manual visual inspection when platform re-encoding changes bytes.

## Out of Scope

- Author homepage scanning.
- Public video collection scanning.
- Multiple source submission and cross-source discovery preview.
- Scan drafts and stale-draft refresh behavior.
- Multi-line import of single-video links.
- Reading or importing Edge, Chrome or other existing browser cookies.
- Application-managed isolated login or authorized pagination.
- Favorites, liked works, private content, followed accounts or account-personalized feeds.
- Image/gallery archive output.
- Platform subtitle download.
- Speech-to-text transcription.
- Comment collection or comment export.
- Downloading platform-associated music as a separate artifact.
- Live stream recording.
- Search, recommendations, hot lists or whole-site discovery.
- Whole-library automatic migration when the default archive root changes.
- Cloud synchronization, remote storage, LAN/public access, multi-user operation or remote administration.
- Automatic CAPTCHA solving or any bypass of platform access controls.
- Automatic updater, browser extension or mobile application.

## Further Notes

- This is the first of three approved specification cycles. Cycle 2 will add public author-homepage and public-video-collection scanning, structured source lists, scan drafts, preview, cross-source deduplication and incremental archive. Cycle 3 will add the application-managed isolated authorization adapter and complete scans when guest access is insufficient.
- The first cycle intentionally uses the final three-level task shape even for one work, preventing the later batch implementation from introducing a second task engine.
- `jiji262/douyin-downloader` is an MIT-licensed behavioral and algorithmic reference. Any selectively reused implementation must retain required notices and pass the project’s security, privacy and packaging gates; the project is not embedded as a second backend.
- Existing protected files and project artifacts remain preserved.
- Success requires the existing quick download, theme, single-instance and loopback security tests to remain green together with the new archive, task, library, recovery, FFmpeg, build and Windows UAT coverage.
