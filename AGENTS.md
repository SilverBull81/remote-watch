# Repository Instructions

Version 1.4.4

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261007-120324

## Project Intent

Remote Watch provides standard-logging integration, asynchronous notification
delivery, and optional secure command routing for distributed Python
applications.

The private 0.1 baseline and the functional outbound-relay scope of 0.2 are complete.
The package version is 0.4.1.dev11; no stable 0.2.0 tag is implied.
Dev11 adds DailyFileConfig/DailyFileHandler for YYYY/MM. EnglishMonth/prefix_log_YYMMDD.txt.
RemoteWatcher(file=...) supports it alongside the unchanged RotatingFileConfig.
Record.created selects the day in the configured tzinfo or OS local timezone;
the default formatter uses that timezone too. Relative roots freeze at configuration.
One stream per handler, thread-safe switching, append, no archive deletion and no reopen
after close. Files are per-process; no multi-process coordination is promised.
See docs/LOCAL_LOGGING.md before changing local calendar logging.
Dev10 retains safe local TLS error_kind/verify_code/tls_reason in CommandError,
sync startup errors, current StageHealth and DeliveryResult. Existing command codes
and relay wire schemas are unchanged. Never infer a failing chain depth from code 10.
Read docs/CADDY_RECOVERY.md for the recurring Windows Caddy incident and evidence;
console selection was confirmed; Esc restored API and renewed leaf/intermediate
without changing PID/root. RemoteWatchCaddy now runs on LV as LocalService with
automatic startup, recovery and runtime file logging; verified TLS still uses the
original root. On 2026-10-06 the owner rebooted LV: boot 16:58:02.500 UTC,
Caddy serving 16:58:24.378 UTC, Auto/LocalService, unchanged root and verified TLS.
The owner manually started both Python gateways; local listeners and credential-free
HTTPS GET probes to both routes returned 405/Allow: POST. This is not command-session
or RU/provider acceptance. The old observation contains 123 successful samples over
two hours, with no renewal; a new bounded 25-hour observation started at 17:26 UTC.
Scheduled renewal remains pending. See docs/CADDY_RECOVERY.md for observation limits.
On 2026-10-06 the owner accepted the first real-application pilot: notifications and
commands work as intended. This is conversation evidence, not a newly inspected report;
do not infer acceptance of the subsequent dev9 presentation changes or ntfy commands.
Dev9 adds English messenger notices, -> /command help lines and built-in /help.
command_display_mode is independent of outbound display_mode. Explicit client mode
is registered and persisted in the original request; results never look up a newer session's mode.
Command wire schema 2 extends only registration/request/grant with explicit mode;
absent mode retains byte-identical schema 1 and old hashes. Results/receipts are unchanged.
Source JSON defaults to full; client override needs dev9 gateway/client, with no downgrade retry.
Config JSON schema remains 1. Retain existing journals/cursors on upgrade; old code cannot
read new schema 2 records. See docs/COMMAND_SOURCES.md and docs/COMMAND_STORAGE.md.
Dev9 passed 1299 local tests (2 live deselections) on Windows Python 3.12.2;
clean core/extras wheels passed 902/1299 tests. Ruff/style: 123 Python files, no issues.
Real local TLS, source-journal recovery and independent two-client presentation are covered.
No new CI/Linux/live-provider run; exact evidence is in docs/VALIDATION.md.
Dev8 adds client RelayConfig display_mode/display_fields, carried only by relay schema 3.
Absent mode inherits gateway presentation; explicit mode replaces it only for that delivery.
The owner chose client config changes with application restart, not live config reload.
Upgrade notification gateway once; later client display changes do not restart it.
Schemas 1/2 retain their exact envelope; no downgrade/repeated POST fallback is allowed.
Identity, routing, credentials, ACL and execution contracts are unchanged. See docs/RELAY.md.
Dev8 passed 1235 local tests (2 live deselections) on Windows Python 3.12.2;
clean core/extras wheel checks passed 842/1235 tests. Ruff/style: 121 Python files,
no issues. No CI/Linux/live-provider rerun; schema 3 field acceptance is pending.
Exact evidence is recorded in docs/VALIDATION.md.
Dev7 implements the first-pilot wishes: Telegram Help and implicit-target slash commands
use the statically configured actor/chat ACL, never the sole currently live instance.
Ambiguity requires explicit /rw alias; original session binding and replay protection remain.
Telegram/ntfy notification destinations support full/compact/text and fixed display field
groups. The gateway provider renders relay output, with optional dev8 client presentation overrides.
CommandClient.health and DispatcherStats.ready separate current poll/heartbeat/storage/result
failures from last_error history. Successful callbacks cannot hide heartbeat or result failure.
No implicit command session re-registration or callback retry is introduced.
See docs/COMMAND_SOURCES.md, docs/ADAPTERS.md and docs/COMMAND_EXECUTION.md.
Dev7 passed 1213 local tests (2 live deselections), plus clean Windows Python 3.12.2
core/extras wheel checks: 823/1213 passed. Ruff/style: 119 Python files, no issues.
No CI/Linux/live providers were rerun. Owner pilot acceptance of these new features
remains pending; exact evidence is in docs/VALIDATION.md.
RelayConfig now accepts either a private literal token or token_env, exclusively.
Both use the gateway credential validator; neither value nor reference is exposed in repr.
Local validation: 1099 passed, 2 live deselected; 33 relay contract cases, no live providers.
Synchronous command startup now copies only a safe known exception category into
CommandError.__cause__, never the original message, traceback, notes or chain.
Exact CommandError causes retain validated code/http_status. HTTP errors accept only
a bounded single-code JSON envelope matching the status; stale_session survives HTTPS.
Unknown bodies keep the status fallback, and body failures retain an observed http_status.
See docs/COMMAND_EXECUTION.md. SpamBot's field root cause remains unconfirmed.
Dev6 passed 1148 local tests (2 live deselections), plus clean Windows Python 3.12.2
core/extras wheel checks: 768/1148 passed. Ruff/style: 116 Python files, no issues.
Those dev6 checks did not rerun CI/Linux/live providers; dev7 validation is recorded separately.
Core provides standard logging integration, bounded queues/retries/TTL, independent
destinations, sync/async lifecycle, local handlers and polling statistics. Telegram,
ntfy, relay and gateway have isolated optional dependencies. JSON and Python server
configuration support literal token or token_env with private diagnostics.

On 2026-09-30 the owner accepted direct delivery after about 12 hours: RU ntfy 26/26,
LV Telegram and ntfy 26/26 each, all first-attempt provider acceptance. The first 25
samples per host were confirmed on Android; full 24 hours and powered-off phone were
waived. The ntfy long-text issue was fixed and confirmed in two 10-message rechecks.
The owner then confirmed RU -> HTTPS gateway on LV -> Telegram -> phone after
updating the LV venv to dev9: provider_accepted and immediate phone receipt. This
last evidence comes from the conversation, not a newly inspected field report.
Do not infer the exact clock allowance, wire schema or provider HTTP status from it.

Dev9 explicitly allows bounded UTC clock skew for outbound relay; relative request
budgets remain bounded. It weakens server UTC age checks and is not command replay
protection. Actual VM clocks differ by more than a minute. See ADR 0008.
Long regional mixed delivery, autostart/reboot and operational recovery remain
unverified and do not block starting 0.3. Scope/evidence: docs/REVIEW_0_1_0_2.md.
Dev9 passed 576 local tests and clean core/extras wheel installation on Windows
Python 3.12.2. The eight Windows/Linux Python 3.10/3.12 CI jobs passed for dev7;
do not claim those matrix runs were repeated for dev9. See docs/VALIDATION.md.

Stages 0.3.1/0.3.2 provide command wire/state contracts and bounded SQLite journals.
SQLite atomically persists source decisions/cursors, compares revisions before
STARTED, recovers old generations, retains unknown running executions and keeps
source high-water marks after payload cleanup. See docs/COMMAND_STORAGE.md.
Later stages add authenticated endpoints and callback execution as described below.
The owner requires resume/suspend in a single message, without confirmation.
TrustedClock uses an explicit trusted UTC interval, max age 120 seconds and
monotonic remaining budgets. Optional HttpsDateTimeSource requires an explicitly
trusted HTTPS origin and declared accuracy. In dev3 the owner selected TimeAPI.io;
TimeApiTimeSource reads its UTC JSON with an explicit one-second accuracy assumption.
LV HTTPS reachability was verified; this is not a provider clock-accuracy guarantee.
No alternate public source or automatic fallback is enabled.
See docs/COMMAND_TIME.md and ADR 0011. Never reuse a journal generation after process
restart, reset source cursors to replay events, or release unknown execution merely
because its timeout elapsed. The old confirmation helper is an unused prototype.
Dev2 passed 769 local tests and clean core/extras wheel checks on Windows Python
3.12.2. Real local TLS and abrupt subprocess crashes were tested; no live time
origin, real command source or new CI matrix was exercised. See docs/VALIDATION.md.
Stage 0.3.3 now provides command-only ACL, durable hub/client handshakes, heartbeat
and HTTPS long polling. Fake callback execution over real local TLS is verified.
Stage 0.3.4 now provides CommandDispatcher and explicit RemoteWatcher(command_client=...).
Sync callbacks/validators use one dedicated worker; async callbacks use the application's
astart loop. Timed-out work retains its slot until actual termination; only result
delivery is retried. Durable release proof and pre-grant rejection extend hub operations.
Old unknown executions remain pinned for review. Stage 0.3.5 now provides Telegram
and private-topic ntfy sources, strict command JSON, a separate gateway.commands CLI
and diagnostics.command_smoke. Source decisions bind the original session before
hub submission. Source SQLite retains opaque provider IDs, a pending decision and
an irreversible pruning cutoff. Local OS locks prevent a second deployment owner.
The owner explicitly selected ntfy commands for private topics; the existing free
unreserved notification topic must not authorize commands. Provider write ACL is
an operator prerequisite; anonymous read probes alone do not prove it.
Read docs/COMMAND_SOURCES.md and docs/REVIEW_0_1_0_3.md before changing this boundary.
On 2026-10-02 the owner confirmed Telegram command smoke with one and two
concurrent synthetic applications via RU -> LV Caddy:8443. Evidence is the chat,
not newly inspected report files; do not infer exact counts, exit codes or recovery
scenarios. Live private-topic ntfy command testing is deferred indefinitely by the
owner and does not block Telegram deployment; outbound ntfy acceptance is unchanged.
Real application migration and unattended recovery testing remain pending.
Read docs/COMMAND_HUB.md and docs/COMMAND_EXECUTION.md before changing these boundaries.
The old root smoke/field_smoke/ntfy_diagnostic launchers are removed; diagnostics
are launched only under remote_watch.diagnostics. Existing callback registration remains compatible. The owner explicitly requires both status/check_load and resume_load/suspend_load
in the first usable version. All names/callbacks remain application-defined through
mapping/partial or CommandSpec. Read-only is the first vertical slice, not the whole
milestone. ADR 0009 records the overall scope; ADR 0010 defines the implemented contract.
Model duplicate delivery, persistent execution records, unknown outcomes, callback
thread/loop ownership and unsynchronized clocks before enabling mutations. Never
retry a possibly executed callback automatically. Outbound grants do not enable commands.

Real application migration remains deferred until this command path is ready.
Legacy fin-data TelegramBot API compatibility is not required. The target phone is
Android; ntfy uses a free account without topic reservation. Telegram and explicitly private ntfy topics are command sources; Matrix follows. MAX and public distribution remain out of scope.

Dev3 passed 808 local tests (2 opt-in skips), clean core/extras wheel checks,
and all 39 TimeAPI tests on Python 3.10.21 after fixing fractional-second parsing,
and the formatting audit of 83 Python files. A live TimeAPI probe accepted 3/3
samples through TrustedClock in the development environment. No new CI run or
command hub integration was performed. See docs/VALIDATION.md.

Production modules are grouped under notifications, commands, gateway, adapters and
diagnostics. Public root exports and existing CLI entry points are preserved.
Diagnostics ship in the wheel; developer tests and tooling remain outside it.
See ARCHITECTURE.md for migration paths and docs/COMMAND_TIME.md for the time probe.

Dev 0.3.4.dev1 passed 892 tests (2 live skips) on Windows Python 3.10.21;
clean Windows Python 3.12.2 core/extras wheel checks passed 592/892 tests.
All 93 Python files passed style checks. CI/Linux/live providers were not rerun.
Exact results and command-execution limits: docs/VALIDATION.md and docs/COMMAND_EXECUTION.md.

Dev 0.3.5.dev1 passed 947 tests (2 live skips) on Windows Python 3.10.21;
clean Windows Python 3.12.2 core/extras wheel checks passed 626/947 tests.
All 107 Python files passed style checks. CI/Linux/live command providers were
not exercised. Source replay and transport-log isolation regressions are covered.
Exact results: docs/VALIDATION.md. Templates live under docs/examples and ship
in sdist; no real credentials or runtime journals belong in distribution artifacts.

Dev 0.3.5.dev2 passed all eight Windows/Linux, Python 3.10/3.12, core/extras
jobs in GitHub Actions run 36899958770, tested code commit 6bee643.
Each core job passed 630 tests (30 optional-dependency skips, 2 live deselections);
each extras job passed 951 tests (2 live deselections), without warnings.
CI exposed and fixed Windows CLI encoding, floating-point grant-bound comparisons,
and timing/observation problems in contract tests. Live command-provider smoke
remains owner-run; CI does not read credentials or send provider messages.
See docs/VALIDATION.md for exact Python versions, run link and local reports.

A fresh review on 2026-10-02 found open operational gaps: hub maintenance task
failure is not directly supervised, trusted-time readiness is absent from gateway
stats, and command configuration errors are too coarse. Synthetic fault injection
confirmed the first two; no runtime fix was made in that documentation change.
The current source suite passed 951 tests (2 live deselections) on Windows Python
3.12.2; Ruff/style passed (107 files). No new CI/build was run. See the updated
docs/REVIEW_0_1_0_3.md and docs/VALIDATION.md. Proposed step 0.3.6 fixes observability;
proposed 0.4 adds gateway_server, deployment resources, doctor and OS service support.
Read docs/GATEWAY_OPERATIONS_PLAN.md before implementing these proposals. They are
not existing CLIs or settled wire schemas. Preserve current command journal/CA state,
separate component credentials and single-owner provider semantics during migration.

Stage 0.3.6.dev1 now closes review R1/R2/R3 and the noted code-comment part of R6:
hub/source task failures, unexpected cancellation and early returns are observed;
subsequent hub calls fail closed after maintenance failure. Local health exposes
trusted-time readiness and maintenance storage errors without network/storage probes.
The command CLI emits readiness transitions and exits on fatal failure; no automatic
restart or public health endpoint is added. Config errors expose fixed codes, schema
paths/indices and Russian hints, never supplied values. Old schemas and UNKNOWN
semantics remain intact. Read docs/COMMAND_SOURCES.md for these local health APIs;
R4/R5 and the proposed service/deploy/doctor remain 0.4 work. Validation below is
historical unless explicitly recorded for dev1 in docs/VALIDATION.md.

Dev 0.3.6.dev1 passed 985 local tests (2 live deselections) on Windows Python
3.12.2. Clean wheel-from-sdist validation passed core 648 tests (46 optional skips,
2 live deselections) and extras 985 tests (2 live deselections), without pytest
warnings. Ruff and all 108 Python files passed style checks. No new CI, Linux,
Python 3.10 or live-provider run was performed. See docs/VALIDATION.md and
build/validation-0361-core / build/validation-0361-extras for exact results.

Stage 0.4.1 provides typed deployment configuration, read-only linked-file validation
through remote_watch.gateway_server --check-config and a finite per-component
RestartBudget. Real process supervision/start/stop/status is the next step 0.4.2;
deploy, doctor and OS service installation are not implemented. Read
docs/GATEWAY_DEPLOYMENT.md and ADR 0012 before changing ownership/recovery.
Keep existing component config paths, journals and Caddy CA storage. External
Caddy is never owned or stopped. Configuration checks do not open journals,
resolve environment credentials, launch processes or query providers.
Current command clients do not automatically re-register after a hub restart;
server readiness does not prove restored application command routing. Preserve
new-session lifecycle and UNKNOWN records; do not silently replay callbacks.
Dev 0.4.1.dev1 passed 1058 local tests (2 live deselections) on Windows Python
3.12.2. Clean core/extras wheels passed 721/1058 tests; all 114 Python files
passed style checks and all eight installed CLIs passed help/encoding checks.
No new CI/Linux/Python 3.10 or live provider checks were run. Actual process
supervision and recovery remain unimplemented; see docs/VALIDATION.md.

Dev 0.4.1.dev2 improves command configuration and CLI diagnostics: safe JSON
line/column and fixed syntax explanations, missing-field/schema paths, distinct
TLS/listener/storage hints and --version. It does not print JSON excerpts,
unknown keys, exception payloads, arbitrary class names or credentials. Existing
config schemas and command execution guarantees are unchanged. See
docs/COMMAND_SOURCES.md for operator steps; source_id is a stable local source
name, not a provider actor/chat ID. Do not rename an existing journal owner to
bypass recovery or create a second reader for the same bot.
Dev2 passed 1092 tests (2 live deselections), clean core/extras wheels with
754/1092 passed and style checks of all 115 Python files on Windows Python
3.12.2. No CI/Linux/Python 3.10 or live provider runs were added. The owner's
configuration was checked read-only; neither deployment files nor its venv
were modified. See docs/VALIDATION.md for exact evidence and limits.

## Read the Relevant Design Context

- Read `PROJECT_BRIEF.md` when planning features, milestones, or product scope.
- Read `ARCHITECTURE.md` when changing public APIs, logging integration,
  concurrency, delivery guarantees, channels, routing, commands, security, or
  package boundaries.
- Do not reread both documents for unrelated mechanical edits.
- Read `docs/CODE_STYLE.md` and `docs/DEVELOPMENT.md` before creating code or
  changing documentation conventions. See `docs/VALIDATION.md` for required checks.
- Read `docs/GATEWAY.md` for relay/command boundaries and `docs/CHANNELS.md` for
  the provider selection rationale. Keep planned and verified behavior distinct.
- Read `docs/COMMANDS.md` before changing application-owned command registration.
  Functions, partials and explicit CommandSpec declarations are supported.
- Read `docs/RUNTIME.md` for the implemented lifecycle, counters and stage limitations.
- Read `docs/ADAPTERS.md` for provider configuration, HTTP behavior and opt-in live tests.
- Read `docs/RELAY.md`, `docs/GATEWAY_SERVER.md` and ADRs 0005/0006 for relay behavior,
  server admission/lifecycle and the external TCP/TLS ingress limits required for deployment.
- Read `docs/FIELD_SMOKE.md` for finite field runs, reports and manual phone observations.
- Read `docs/NTFY_DIAGNOSTIC.md` for bounded size probes and `docs/GATEWAY_TLS.md`
  for deployment on company-owned VM infrastructure without physical-host access.
- Read `docs/SMOKE.md` for explicit one-message checks. Local credentials.local.json
  is ignored and must never be displayed, committed or included in build artifacts.

## Architectural Constraints

- Preserve `logging.Logger` as the primary application-facing logging API; do
  not introduce a required `Logger` subclass.
- Do not perform network I/O on application logging threads.
- Keep outbound notifications separate from inbound commands.
- Core modules depend on transport protocols, never concrete provider classes.
- Keep provider dependencies optional and isolated in adapters.
- Use a central gateway when multiple instances share a bidirectional bot or
  conversation.
- Never provide arbitrary remote shell or Python evaluation as a built-in
  command.
- Prevent internal transport failures from recursively entering the notification
  pipeline.
- Never commit real credentials or require them for unit tests.
- Keep remote delivery independent of local handlers and bound all outstanding
  work, including retries and loop wakeups. Do not promise at-least-once attempts
  for events that can be dropped before their first attempt.
- Make direct/relay a per-destination setting. Outbound relay and inbound commands
  are separate capabilities; neither enables the other implicitly.

## Development Expectations

- Prefer small typed interfaces and composition over inheritance.
- Keep application identity explicit: service, environment, region, host, and
  `instance_id` must not be inferred from chat display text.
- Make retries, timeouts, queue bounds, shutdown behavior, and failure semantics
  explicit and testable.
- Unit tests use fake transports and deterministic time; live-provider tests are
  opt-in integration tests.
- When a design decision materially changes `ARCHITECTURE.md`, update the
  document or add an Architecture Decision Record in the same change.
- Tooling is setuptools/build, pytest and Ruff; see docs/DEVELOPMENT.md and pyproject.toml.
  GitHub Actions is manual-only by owner decision on 2026-09-30. Reserve CI runs
  for substantial changes, not every push/commit; keep appropriate local checks.
  Python 3.10+ is the declared target; runtime checks cover Windows/Linux and Python 3.10/3.12 in successful CI jobs.
  Keep actual test results separate from unverified platforms and future features.
- Configuration starts with typed Python objects; no required file format yet.
- Follow Russian documentation/comments, English identifiers/docstrings, file
  versions and Moscow timestamps, UTF-8 BOM/CRLF for Markdown/Python as specified
  in CODE_STYLE.md. Do not copy neighboring projects' runtime dependencies.

Dev5 adds RelayConfig.ssl_context for an explicit verifying client TLS context.
No environment mutation, wire change or gateway upgrade is needed for this API.
CERT_REQUIRED and hostname checks remain mandatory; contexts must not be mutated
after handoff. Full local extras suite: 1102 passed, 2 opt-in live skips. Targeted
relay/HTTP/TLS: 76 passed, including trusted/untrusted/expired/name/default-trust.
Application field acceptance of explicit contexts is still pending.
