# Repository Instructions

Version 1.2.5

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261001-120148

## Project Intent

Remote Watch provides standard-logging integration, asynchronous notification
delivery, and optional secure command routing for distributed Python
applications.

The private 0.1 baseline and the functional outbound-relay scope of 0.2 are complete.
The package version is 0.3.0.dev3; no stable 0.2.0 tag is implied.
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
source high-water marks after payload cleanup. No auth, command endpoints,
Telegram source or callback executor exists yet. See docs/COMMAND_STORAGE.md.
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
Existing command registration remains compatible; remote execution does not exist. The owner explicitly requires both status/check_load and resume_load/suspend_load
in the first usable version. All names/callbacks remain application-defined through
mapping/partial or CommandSpec. Read-only is the first vertical slice, not the whole
milestone. ADR 0009 records the overall scope; ADR 0010 defines the implemented contract.
Model duplicate delivery, persistent execution records, unknown outcomes, callback
thread/loop ownership and unsynchronized clocks before enabling mutations. Never
retry a possibly executed callback automatically. Outbound grants do not enable commands.

Real application migration remains deferred until this command path is ready.
Legacy fin-data TelegramBot API compatibility is not required. The target phone is
Android; ntfy uses a free account without topic reservation. Telegram is the first
command source; Matrix follows. MAX and public distribution remain out of scope.

Dev3 passed 808 local tests (2 opt-in skips), clean core/extras wheel checks,
and all 39 TimeAPI tests on Python 3.10.21 after fixing fractional-second parsing,
and the formatting audit of 83 Python files. A live TimeAPI probe accepted 3/3
samples through TrustedClock in the development environment. No new CI run or
command hub integration was performed. See docs/VALIDATION.md.

Production modules are grouped under notifications, commands, gateway, adapters and
diagnostics. Public root exports and existing CLI entry points are preserved.
Diagnostics ship in the wheel; developer tests and tooling remain outside it.
See ARCHITECTURE.md for migration paths and docs/COMMAND_TIME.md for the time probe.

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
