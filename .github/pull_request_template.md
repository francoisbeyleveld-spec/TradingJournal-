## What this changes

<!-- What and why, in a few sentences. Link the issue if there is one. -->

## How it was tested

<!-- Commands run, and what you checked by hand. -->

- [ ] `python -m pytest backend/tests -q` passes
- [ ] `npx craco test --watchAll=false` and `npm run build` pass in `frontend/`
- [ ] Money logic changed? Tests with hand-worked expected values are included

## Worth knowing for review

- [ ] New dependency (which, and why)
- [ ] New network call, open port, or background job
- [ ] File access outside the app folder
- [ ] Database change (existing databases keep working)
- [ ] None of the above

No real trades, account numbers, `.env` files or databases are included.
