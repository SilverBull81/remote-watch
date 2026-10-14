# Repository Instructions

Version 1.6.8

Автор: Sergey Fundobny (silverbull@mail.ru) + GPT-6

Дата и время последнего изменения: 261014-203720

## Intent and current status

Remote Watch integrates standard Python logging with bounded asynchronous notifications
and optional authenticated command routing. Python 3.10+, Windows/Linux, optional provider extras.
The current package version is in pyproject.toml. The owner selected MIT; LICENSE and SPDX package metadata apply.
The owner authorized the first production release 1.0.0 and annotated tag v1.0.0.
Prepare and verify release artifacts, then publish the GitHub Release; no PyPI
publication or bundled EXE is requested. Releases and source tags ship ICO only;
the editable PNG remains ignored locally. Final 1.0.0 validation is pending.
Functional milestone 0.4 is complete: owner field acceptance plus all eight CI jobs passed.
CI run 38602660574 tested code commit 1c05538638156f04a0760b4bc714fe445de2358a,
package 0.4.5.dev6. Subsequent result-documentation edits do not change runtime code.

The owner accepted field notifications/commands/reconnect, deploy/doctor, Windows service,
tray, install/remove/reinstall, reboot, crash tests and delivery of gateway owner alerts.
These are conversation evidence, not a claim of independently inspected logs for every test.
Do not infer private ntfy command or real systemd acceptance. Notification-health recovery
field observation is explicitly deferred beyond 0.4. Current validation: docs/development/VALIDATION.md.

## Read relevant context

- User entry points: README.md and docs/README.md; pending work: docs/BACKLOG.md.
- Before changing runtime, protocol/security, ownership or package boundaries:
  docs/development/ARCHITECTURE.md and the corresponding source/tests.
- Before changing code/docs: docs/development/CODE_STYLE.md and README.md in that folder.
- Configuration, lifecycle and operations: docs/CLIENT.md, CONFIGURATION.md,
  COMMANDS.md, NOTIFICATIONS.md, GATEWAY.md, SERVICES.md, TLS.md and OPERATIONS.md.
- Audit and licensing: docs/development/AUDIT.md and LICENSING.md.
- Machine-specific runbooks and historical evidence belong only under ignored docs/private.
  Do not publish their contents in Git, build artifacts, diagnostics or PR descriptions.

## Runtime invariants

Constructors do not start I/O. Preserve external Logger levels/handlers/filters.
Use explicit Identity(service, environment, region, host, instance_id), never guessed routing.
Bound queues, outstanding work, retries, TTL, cleanup, startup and shutdown.
No durable notification outbox is promised; provider acceptance is not phone receipt.
Current health, historical errors/counters and lifecycle are distinct.

Commands retain actor/chat/target/scopes/session/epoch/claim checks. No implicit broadcast
or target selection by the sole currently live instance. User callbacks remain application-owned,
registered by mapping/partial or CommandSpec; resume/suspend need no second confirmation.
Never automatically retry a possibly executed callback. UNKNOWN/running slots, durable receipts,
owner generations and source high-water marks survive timeouts/restarts/cleanup.

Reconnect after successful initial start is automatic but never resets a journal or replays a
callback. Conflicting outcomes require operator action. Offline resolution requires stopped
owners and exact client COMPLETED versus hub UNKNOWN, preserving original evidence/fencing.
No HTTP admin endpoint or manual local ACK. Existing SQLite schema 1 migrates to 2; old
runtimes reject 2. Wire schemas/hashes must not change accidentally.

Trusted time uses explicit provider UTC/accuracy assumptions and monotonic budgets, no silent
fallback to OS UTC. TLS certificate and hostname verification are mandatory; do not mutate an SSLContext after handoff. Preserve safe
allowlisted diagnostics, never raw exception messages/tracebacks/provider payloads or secrets.
Command client and notification gateway credentials and ACL are independent.

Use current NotificationGateway names, no Relay legacy aliases. Public provider modules use
_notifications/_commands; gateway wire lives in notifications.gateway_protocol.
Direct/gateway is a per-notification-destination choice; built-in messenger commands use gateway.

Manager owns exact current process handles, not stale PIDs; finite recovery and startup rollback.
Control/log/state/CA ownership stays separate. Refuse foreign log entries before changing ACL.
External Caddy is not owned/probed by manager. Deploy is offline, preserves existing files/CA/
SQLite, and never installs/starts a service. Windows service is headless; tray is a separate
operator-session controller and never starts another manager. Preserve explicit operator ACL.

## Work and validation

Respect unrelated user edits. Never inspect or stage unrelated local files. Production processes,
credentials, journals, CA and other repositories are unchanged unless separately authorized.
Do not delete journals/locks, reset cursors/owners or weaken authorization to make tests pass.
User supplied unrelated local files are not part of this change.

Russian comments/documentation, English docstrings/identifiers. File versions/timestamps,
BOM/CRLF, full banners/closing order, vertical arguments and Russian argument/field comments
follow CODE_STYLE. Separate logical steps with readable blank lines. Check both formal style
and actual comment meaning; generic translations are not sufficient.

Run relevant behavioral tests, Ruff, tools/check_style.py and tools/check_docs.py.
Required release checks: tools/validate.py core/extras, installed CLIs and wheel-from-sdist.
GitHub Actions is manual-only; use full Windows/Linux × 3.10/3.12 × core/extras for agreed
major work, not every push. Tests are synthetic/offline unless integration explicitly requested.
Keep exact test/field limits and CI commit/result distinct. Record only current evidence,
not a growing version-by-version transcript in AGENTS or README.

docs/private is ignored AND pruned from sdist; never link its files publicly. Sanitize
examples/deploy templates and use generic domains/paths. A Git move does not erase history;
history rewriting needs separate authorization. Review staged diff/archive contents before commit.
