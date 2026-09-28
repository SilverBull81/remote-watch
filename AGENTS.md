# Repository Instructions

Version 1.0.6

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 260928-184554

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
Private version 0.1.0 is implemented; public distribution is not planned. Gateway is
still planned. The user confirmed both Telegram and ntfy smoke messages on Android;
ntfy uses a free account without topic reservation. Regional/background validation
and Python 3.10/Linux runtime checks remain pending. Real application migration is
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
  Python 3.10+ is the declared target; the current runtime check is Windows/Python 3.12.
  Keep actual test results separate from unverified platforms and future features.
- Configuration starts with typed Python objects; no required file format yet.
- Follow Russian documentation/comments, English identifiers/docstrings, file
  versions and Moscow timestamps, UTF-8 BOM/CRLF for Markdown/Python as specified
  in CODE_STYLE.md. Do not copy neighboring projects' runtime dependencies.
