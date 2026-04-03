# Local Populated-Data Validation

This checklist is the safe validation pass for a real local File Fridge setup that already has a monitored path, cold storage, and file inventory configured.

## Safety rules

- Treat existing monitored paths, storage locations, and real files as protected data.
- Use read-only validation first.
- For any temporary create/update/delete flow, prefix test objects with `ff-test-`.
- Clean up every temporary tag, notifier, or rule before finishing.
- Only run freeze, thaw, or relocate on a disposable test file that can be restored safely.
- Do not store local credentials in committed files, fixtures, or shell history snippets in the repo.

## Browser smoke suite

Export credentials in your shell for the local run:

```bash
export FILE_FRIDGE_E2E_USERNAME='your-local-username'
export FILE_FRIDGE_E2E_PASSWORD='your-local-password'
```

Run the UI smoke suite against the local dev app:

```bash
npm run test:e2e
```

What it verifies:

- login and logout flow
- redirect to `/login` when auth is missing
- declared route/API contracts for core authenticated pages
- loading indicators resolve
- no uncaught browser errors
- no external CDN requests
- no horizontal overflow on desktop, tablet, and mobile widths

## Backend regression suite

Run the focused populated-flow API coverage:

```bash
uv run pytest tests/routers/test_populated_flow_contracts.py
```

Run the existing router coverage that overlaps the populated workflow:

```bash
uv run pytest \
  tests/routers/test_auth.py \
  tests/routers/test_paths.py \
  tests/routers/test_storage.py \
  tests/routers/test_files.py \
  tests/routers/test_tags.py \
  tests/routers/test_tag_rules.py \
  tests/routers/test_notifiers.py \
  tests/routers/test_stats.py
```

## Manual populated-data pass

Read-only checks:

- Log in through `/login` and confirm redirect to `/`.
- Verify `/health` populates version and connection status in the sidebar.
- Visit `/`, `/paths`, `/paths/{id}`, `/storage-locations`, `/files`, `/stats`, `/tags`, `/notifiers`, `/settings`, and `/migrations`.
- For each route, confirm expected API calls match `docs/UI_ROUTE_MATRIX.md`.
- Confirm no page is stuck on `Loading...`, `Checking connection`, or a spinner.
- Confirm text is readable on stat tiles, badges, alerts, path/code fields, and tables.
- Check the same pages at 1440px, 1024px, and 390px widths.

Real workflow checks:

- Trigger one scan from `/paths/{id}`.
- Confirm scan progress updates or a visible scan error state appears.
- Confirm dashboard, paths, and stats reflect the new scan state.
- Confirm the files page still loads filters, grid content, and file action menus.

Temporary write checks:

- Create one `ff-test-tag-*`, apply it to a safe test file, then remove it.
- Create one `ff-test-rule-*`, apply rules, verify the intended tag result on a safe test file, then delete the rule.
- Create one `ff-test-notifier-*`, run the test notification action, then delete it.
- If freeze, thaw, pin, unpin, or relocate are tested, use only a disposable test file and return it to the original state.

Failure and resilience checks:

- Clear `sessionStorage.auth_token` and confirm protected pages redirect to `/login`.
- Confirm failed API calls surface inline errors or alerts instead of permanent loaders.
- Confirm network activity stays local and does not reference CDNs.

## Route contract maintenance

When adding or changing a routed UI page, update both:

- `docs/UI_ROUTE_MATRIX.md`
- `e2e/route-contracts.ts`

Each route contract must declare:

- expected script(s)
- expected API calls
- loading selector(s)
- ready selector(s)
- empty selector when applicable
- error selector when applicable
