# Repository Instructions

Version 1.1.4

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260929-202056

## Project Intent

Remote Watch provides standard-logging integration, asynchronous notification
delivery, and optional secure command routing for distributed Python
applications.

The repository implements immutable event/delivery models, typed configuration,
local command registration and a logging-to-channel path with bounded queues and
a managed worker. Bounded retries, full jitter, retry-after, TTL, sync/async lifecycle,
per-destination counters and bounded atexit cleanup are implemented. Telegram and
ntfy outbound adapters use optional aiohttp; validation uses fake HTTP and loopback.
RemoteWatcher owns optional console/rotating-file handlers and notification lifecycle.
The private 0.1 baseline is complete. Version 0.2.0.dev1 adds a finite field-smoke CLI,
relay wire models and an optional relay client. Version 0.2.0.dev3 implements the
outbound gateway with exact credential/identity/alias grants, bounded admission,
per-principal/destination rate limits and one provider attempt. Loopback tests cover
the real gateway and mixed direct/relay delivery with fake provider HTTP endpoints.
Version 0.2.0.dev4 adds optional strict JSON server configuration and a four-application
example. JSON selects Telegram/ntfy from a fixed list and references environment secrets;
custom providers retain the explicitly trusted Python factory path. One gateway supports
multiple applications and destinations. Windows TLS deployment guidance is documented.
Actual deployment and regional relay delivery are pending; command execution is not implemented. Public
distribution is not planned. The user confirmed both Telegram and ntfy smoke messages on Android;
ntfy uses a free account without topic reservation. Short regional tests found Telegram
connect timeouts on both Russian hosts and ntfy long-message rejection on all three hosts.
Version 0.2.0.dev2 fixes ntfy JSON serialization and wire size limits. Two Russian
rechecks on dev2 still confirm only three of four messages: long text returns
http_temporary on all three attempts. Those reports do not retain exact HTTP status;
root cause remains unresolved and 24-hour tests are deferred. In dev5 the diagnostic
numbers are retained, a finite ntfy size diagnostic is available, relay enforces fixed
notification limits before sending, and local real TLS is tested. Relay schema 1 stays
the default for compatibility; schema 2 explicitly enables provider diagnostics.
Two Russian dev5 diagnostics now show identical boundaries for ASCII and Unicode:
4095 message bytes accepted; 4096 bytes return HTTP 500, provider code 50001.
Version dev6 caps ntfy text at 4095 UTF-8 bytes including the truncation marker;
the JSON cap remains 8192 bytes. Local HTTP regression covers the boundary.
Two dev6 rechecks each accepted 10/10; the user confirmed all 20 on Android.
The observed long-text failure is resolved for these runs; provider internals remain unknown.
Dev7 adds safe CLI error categories, shutdown statistics and a local stop file,
shared-alias coverage, and a mixed relay-Telegram/direct-ntfy field mode.
Maintained tools validate a wheel built from sdist in clean core/extras environments.
Windows Python 3.10/3.12 passed both modes; the GitHub Actions Linux jobs have not run.
See docs/CI.md and docs/GATEWAY_OPERATIONS.md; VM autostart/proxy deployment is unverified.
See docs/NTFY_DIAGNOSTIC.md for evidence and docs/REVIEW_0_1_0_2.md for remaining work.
Long field validation and actual Linux CI results remain pending. Real application migration is
deferred until minimal inbound commands are ready. Keep these boundaries explicit.
Do not describe registration metadata as enforced command authorization or execution.
Legacy fin-data TelegramBot compatibility is not a requirement.
The target phone is Android. Telegram and provisionally ntfy are the first
outbound adapters; Matrix follows for chat and commands. MAX is out of scope.

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
  Python 3.10+ is the declared target; runtime checks cover Windows/Python 3.10 and 3.12; Linux CI is prepared, not yet verified.
  Keep actual test results separate from unverified platforms and future features.
- Configuration starts with typed Python objects; no required file format yet.
- Follow Russian documentation/comments, English identifiers/docstrings, file
  versions and Moscow timestamps, UTF-8 BOM/CRLF for Markdown/Python as specified
  in CODE_STYLE.md. Do not copy neighboring projects' runtime dependencies.
