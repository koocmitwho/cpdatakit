# v0.6 local UI security and operation boundary

The default UI is a local tool. It binds to loopback, opens a browser, and keeps project data on the
local filesystem. The server has no cloud account, no telemetry, and no outbound requests in its
normal workflow.

The first implemented vertical slice is available through `cpdatakit.web.create_app(workspace)`:
health and home pages, local project creation, and bounded upload-to-inspect flow. It uses the
application service result envelope and stores uploaded sources under the selected workspace.

## Network and session boundary

The host validation rule, path containment rule, and job cancellation rule are explicit contracts for
the implementation and its tests.

- Bind only to `127.0.0.1` by default. A caller must explicitly opt into another interface.
- Check the `Host` header against the bound host and configured port. Reject unexpected hosts.
- Create a random session token when the UI starts. Store it in a SameSite, HttpOnly cookie and
  require it on state-changing requests.
- Add a per-session CSRF token to forms and verify it for every state-changing route.
- Do not load scripts, fonts, CSS, analytics, or API data from a CDN or remote endpoint.

## Workspace and file boundary

Path containment is checked after resolving every path.

- Give each project a dedicated workspace. Resolve every path and verify it stays under that workspace
  before reading or writing.
- Normalize uploaded file names and reject empty names, symbolic-link escapes, and archive traversal
  entries such as `../secret`.
- Enforce an upload size and a bounded preview size before parsing. Large-file operations state when
  they will materialize records or arrays.
- Write artifacts through the existing atomic replacement path. Existing outputs require an explicit
  overwrite confirmation and force flag.
- Keep registered versions under the reserved `.artifacts` directory. Registration verifies the
  produced digest; changed download content is rejected. Recovery never replaces a detected
  concurrent output and retains the old backup with a recovery manifest.
- Store source bytes by reference or copy according to the project setting. Catalog removal does not
  remove source files unless the user selects a separate file-removal action.

## Jobs and cancellation

Job cancellation is cooperative and must leave the workspace in a readable state.

The in-process job manager records an operation ID, start/end times, status, input/output basenames,
and sanitized errors. Job cancellation reaches the owning reader or writer and leaves no partial
artifact. Solver processes are outside v0.6, so the UI does not execute arbitrary commands yet.

The workbench admits a job only after its catalog row exists. Failed admission cancels the worker
and releases its gate. Completed results are persisted before memory retirement. Default limits
are 64 pending jobs, 256 retained completed jobs, and 256 progress entries of 512 characters.
Historical details remain in the catalog and are loaded on demand.

## SQLite catalog

SQLite stores project, dataset, schema, artifact, and job metadata. It stores relative paths and
hashes rather than credentials or raw secrets. Schema migrations run in numbered transactions and
make a backup before changing a non-empty catalog. Database corruption is reported as a catalog
error and never silently recreated over the old file.

Catalog schema v4 adds job result JSON and project indexes. Migration uses SQLite's backup API
and a transaction so committed WAL data is included in the backup and failed DDL is rolled back.

## Logging and failure responses

Service responses sanitize input paths and expose a stable code and user action. Local recovery
diagnostics identify retained staging or backup paths so operators can inspect them. Unexpected
exceptions receive a correlation ID while the browser sees a generic failure message.

## Explicit network policy

The default local workflow makes no outbound requests. Remote stores, cloud catalogs, AI providers,
and team accounts are separate capabilities that are disabled until the user configures and confirms
them. Capability discovery reports them as unavailable rather than attempting a connection.
