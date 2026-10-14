# Test report

## Automated checks run in the build environment

- `python -m pytest -q`: **21 passed**
- `python -m compileall -q app tests`: **passed**

Coverage includes health/OpenAPI, payload validation, punch-in success and duplicate handling, punch-out missing/closed/success paths, correction validation and audit event creation, attendance filtering/pagination, inverted date ranges, analytics outputs for complete/incomplete/empty data, and audit lookup.

## Important limitation

The tests use an in-memory Mongo-compatible test double. They test API and business logic, not MongoDB's actual BSON encoding, index enforcement, server concurrency, or aggregation engine. This environment has no installed PyMongo/MongoDB server and cannot download dependencies, so a live MongoDB 6.0+ integration run has **not** been performed. Before submission, install `requirements.txt`, run against MongoDB 6.0+, and compare the routes/data model with the original HROne kit. The kit was not available in this session, so the exact official contract cannot be certified.
