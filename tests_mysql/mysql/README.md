# NewsRepository MySQL integration tests

## News API read-only smoke suite (S2-7B-1)

`test_news_api_readonly_mysql.py` uses the real FastAPI app, query Repository,
AsyncSession, and the dedicated MySQL test target. It never seeds records.
Only after separate approval for **reads**, and only when `news_event` is
empty, run this file from the project root:

```powershell
& 'C:\Users\<WindowsUser>\anaconda3\envs\Aipulse\python.exe' -m pytest tests_mysql/mysql/test_news_api_readonly_mysql.py --run-aipulse-mysql-readonly -q
```

This switch does not enable any write suite. Do not set
`AIPULSE_ALLOW_TEST_DB_WRITE=1` for the read-only suite. Without its own switch,
the file is skipped before opening a connection. Each physical connection is
configured with `SET SESSION TRANSACTION READ ONLY` before its first query;
the test Session also rejects commit and flush, and an SQL guard accepts only
SELECT and SHOW statements. The actual API connection verifies the test
database, runtime account, MySQL version, eight expected tables, Alembic
`20261016_0002`, and server-side read-only mode before the Repository runs.
The fixture checks all seven business-table row counts and eight table
definitions before and after each test. A nonempty `news_event` blocks this
empty-event smoke suite without deleting existing data.

## Populated news API suite (S2-7B-2A; prepared offline)

`test_news_api_data_mysql.py` contains 11 cases for event-level totals,
score/latest ordering, filters, pagination, two original providers in one
event, unscored details, and UTC response fields. Each case prepares six
synthetic events and seven originals under a fresh UUID namespace. The suite
requires an initially empty `news_event` table so global list totals are
deterministic; it does not remove pre-existing rows to satisfy that condition.
**This suite has only been prepared offline and must not run until a separate
test-database write approval.**

After that approval, run this file once from the project root:

```powershell
$env:AIPULSE_ALLOW_TEST_DB_WRITE = '1'
try {
    & 'C:\Users\<WindowsUser>\anaconda3\envs\Aipulse\python.exe' -m pytest tests_mysql/mysql/test_news_api_data_mysql.py --run-aipulse-mysql -q
} finally {
    Remove-Item Env:AIPULSE_ALLOW_TEST_DB_WRITE -ErrorAction SilentlyContinue
}
```

The two opt-ins are checked before loading credentials or constructing an
Engine. The fixture uses a dedicated guarded write Engine only for setup and
exact UUID cleanup. The real FastAPI app uses a separate Engine whose MySQL
sessions default to read-only; each API request checks its actual connection
and cannot commit or flush. Cleanup uses the existing item → event → source
selectors, refuses to delete an event with unexpected references, and runs in
fixture teardown even after a failed assertion. It then checks that its UUID
has no remaining rows, all seven business-table counts match their original
values, and table definitions match after ignoring only MySQL's expected
AUTO_INCREMENT counter advance. Any ambiguity is reported as
`CLEANUP_INCOMPLETE`; selectors are never broadened. Do not run this suite
concurrently with the empty-event read-only smoke suite.

This suite lives outside `tests/`. The daily command `python -m pytest tests -q`
collects only offline tests. The write suites in this directory skip unless
both write opt-ins are present. Their CLI switch without the environment
permit fails before a test fixture opens a connection.

Run **only after separate approval for test database writes** from
`D:\Projects\AIPulse`, using the existing Conda interpreter:

```powershell
$env:AIPULSE_ALLOW_TEST_DB_WRITE = '1'
try {
    & 'C:\Users\<WindowsUser>\anaconda3\envs\Aipulse\python.exe' -m pytest tests_mysql/mysql -q --run-aipulse-mysql
} finally {
    Remove-Item Env:AIPULSE_ALLOW_TEST_DB_WRITE
}
```

The suite loads the existing ignored `.env` without displaying credentials.
`create_test_engine` limits the target to `127.0.0.1:3306/aipulse_test_db`
and the `aipulse_test` runtime account. Before **each** Session's first write,
the same connection verifies `DATABASE()`, `CURRENT_USER()`, MySQL
`8.0.44`, the eight expected tables, and Alembic `20261016_0002`.
No migration or schema initialization runs.

## NewsIngestionService service-level suite (S2-6B)

`test_news_ingestion_mysql.py` adds 9 service-level cases. It injects fixed
FakeCollectors, then uses the real NewsIngestionService, NewsRepository,
SQLAlchemy AsyncSession, and MySQL test schema. No external news API is called.
The daily `python -m pytest tests -q` command never collects this file; selecting
it without `--run-aipulse-mysql` skips all 9 cases. The CLI flag alone is
rejected before a database connection. **Run only after separate test-database
write approval**:

```powershell
$env:AIPULSE_ALLOW_TEST_DB_WRITE = '1'
try {
    & 'C:\Users\<WindowsUser>\anaconda3\envs\Aipulse\python.exe' -m pytest tests_mysql/mysql/test_news_ingestion_mysql.py -q --run-aipulse-mysql
} finally {
    Remove-Item Env:AIPULSE_ALLOW_TEST_DB_WRITE -ErrorAction SilentlyContinue
}
```

The existing `create_test_engine` limits host, port, database, and runtime
account. The test-only guarded Session adapter checks both opt-ins and runs
the existing read-only identity, eight-table, and Alembic preflight on each
transaction's **actual connection**, before the Repository can write. The
service closes source registration before invoking its FakeCollector and uses
a second short transaction for the accepted news page.

Each case uses a fresh UUID-derived identity under the process namespace.
The source configuration is tracked before collection because the service
commits it even if collection later fails. Cleanup checks exact provider and
external ID for each item and source, plus event key and first title; it
deletes item → event → source only when those exact rows exist. The fixture
checks no owned UUID rows remain and the seven business-table counts match
their pre-test values. Unowned rows are never included in cleanup. Cleanup
failure reports the run namespace for manual inspection; no broad DELETE,
TRUNCATE, DROP, or automatic migration is attempted.

| Service case | Database behavior to verify |
| --- | --- |
| Single GitHub source | Linked source, event, and item rows match service counts. |
| Repeated collection | All three identities remain unique; processed count means attempted identity. |
| arXiv + HN | Two original items link to one deterministic paper event. |
| Similar titles | Distinct ordinary URLs remain distinct events. |
| Older fetch | Stale source content cannot replace newer stored content. |
| Partial source | Valid candidate commits and rate-limit diagnostics remain visible. |
| Failed source | No item is fabricated; the next source still commits. |
| Injected persistence failure | Real UPSERT rolls back that source's item/event transaction; neighbors remain committed. |
| Repeated three-source batch | Source, item, and event row counts stay stable. |

## NewsScoringService persistence suite (S2-6C-3C-1)

`test_news_scoring_mysql.py` contains 12 opt-in cases. It uses fixed
FakeCollectors, the production ingestion and scoring services, real
NewsRepository calls, and independent SQLAlchemy AsyncSessions. It does not
call a news API or an LLM. The default `python -m pytest tests -q` command
does not collect this directory; selecting the scoring file without the CLI
switch skips all cases. The CLI switch alone is rejected before any Engine
or database connection is created. **The suite has only been prepared
offline; do not run it without separate approval for test database writes.**

After separate approval, from `D:\Projects\AIPulse` in PowerShell:

```powershell
$env:AIPULSE_ALLOW_TEST_DB_WRITE = '1'
try {
    & 'C:\Users\<WindowsUser>\anaconda3\envs\Aipulse\python.exe' -m pytest tests_mysql/mysql/test_news_scoring_mysql.py --run-aipulse-mysql -q
} finally {
    Remove-Item Env:AIPULSE_ALLOW_TEST_DB_WRITE -ErrorAction SilentlyContinue
}
```

The fixture checks both opt-ins before loading the ignored local settings or
constructing the test Engine. `create_test_engine` restricts its target to
`127.0.0.1:3306/aipulse_test_db` as `aipulse_test`. The existing
`GuardedSessionFactory` checks both opt-ins and runs the read-only preflight
on **each transaction's actual connection**, before the Repository writes.
It requires MySQL `8.0.44`, eight exact tables, and Alembic
`20261016_0002`. Direct test read and cleanup Sessions run that same
preflight on their pinned connection before any statement.

Each case uses UUID-scoped source, item, and event identities. The fixture
records seven business-table row counts before and after. After all test
Sessions and locks have closed, cleanup deletes only recorded item identities,
checks that the owned event has no other item references, then deletes the
exact event key **and original title**, followed by exact source identities.
It verifies zero owned rows remain and the business-table counts are
unchanged. If an event has an unowned reference or cleanup fails, it stops
and reports the UUID scope for manual review. It never broadens a selector,
disables foreign keys, truncates, drops, or migrates a table.

| Scoring case | MySQL behavior to verify |
| --- | --- |
| Single event | All four score fields persist; raw identity and event key remain. |
| Same score twice | Second outcome is unchanged, with no event UPDATE or new rows. |
| New rule config | Same event ID receives a versioned changed score. |
| arXiv + HN | Two original rows share one scored event and an independent-source signal. |
| Historical member | A previously committed paper contributes to the later HN-triggered score. |
| Injected score failure | Failure after real SQLAlchemy UPDATE rolls back scoring only; other event succeeds. |
| Rolled-back source | Failed item UPSERT is absent from score input; source registration remains trackable. |
| Partial source | Committed valid item is scored while partial status stays visible. |
| Unscored event | Four nullable fields remain NULL until first score. |
| Two connections | Event `FOR UPDATE` causes bounded lock waiting until first transaction releases. |
| Old RR snapshot | Locking current reads see a later committed member despite an earlier ordinary snapshot. |
| Repeated three-source batch | Item/event counts and deterministic scores remain stable. |

The two-connection test uses distinct MySQL connection IDs, a SQL execution
hook and `asyncio.Event` to confirm the second locking query started, and
bounded waits. It releases the first lock and rolls back both Sessions even
on failure. This checks event-row serialization; it does **not** prove
cross-instance consistency when another writer changes `news_item` without
following the event-lock protocol. The snapshot test verifies one specific
`REPEATABLE READ` sequence and is not a general distributed-consistency claim.

## NewsRepository case details

Each pytest process creates a UUID namespace; every test adds its own stable
case suffix. Ordinary tests execute inside caller-owned transactions and roll
back. Cleanup first checks whether any exact test identities remain, so
rolled-back cases issue no DELETE. Commit and concurrency tests use separate
Sessions and are cleaned with exact recorded identities, deleting
`news_item` before `news_event` and `news_source`. The cleanup has no
unqualified table delete. If it fails, the
test reports its namespace and case key for manual review; do not run a
global cleanup or downgrade.

The two-worker concurrency test obtains different MySQL connections, starts
both UPSERTs together, and has a 15-second bound. It does not retry deadlocks
or unique-key errors. A failure here identifies a Repository or locking
behavior to investigate in the next phase, not permission to change the
business contract automatically.

| Cases | Database behavior checked |
| --- | --- |
| Source creation, repeat, same display name | Identity is `(provider, external_id)`; names may match. |
| Event repeat | `event_key` reuses one ID and preserves first title. |
| Item creation and repeat | Returned ID identifies one row for one platform identity. |
| Newer, equal, older fetches | Only strictly newer observation changes source content and hash. |
| First source and timestamps | Later configuration hits preserve `source_id` and `created_at`. |
| JSON metadata | Approved fields round-trip and update; unknown fields are discarded. |
| Event link and missing FK | Existing association is protected; invalid FK rolls back. |
| Caller commit and rollback | Commit is visible in another Session; rollback discards all rows. |
| Concurrent insert | Independent Sessions converge on one item identity. |
| Failed write cleanup | Session releases its connection and leaves no item behind. |

S2-5D-1 only prepared and collected these tests. It did not enable either
write opt-in together or execute the test suite against MySQL.
