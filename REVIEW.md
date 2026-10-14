# Existing-code review

The email mentions a short existing-code review, but the referenced kit's code snippet was not included in the email text and could not be retrieved here. This file therefore records the review checklist to apply to that snippet rather than inventing findings about unseen code.

## Review checklist
- **Correctness:** Are punch-in/out transitions explicit? Are duplicate and out-of-order requests rejected?
- **Concurrency:** Could simultaneous requests create duplicate attendance records or overwrite a newer value? Prefer unique indexes and conditional updates.
- **Data integrity:** Are timestamps timezone-aware and consistently normalized? Is duration derived from validated timestamps?
- **Auditability:** Do manual changes preserve old values, actor, timestamp, and reason in an append-only audit log?
- **MongoDB use:** Are date filters pushed into `$match` early? Are grouping and calculations done in aggregation pipelines? Are indexes aligned with query patterns?
- **API behaviour:** Are errors mapped to suitable HTTP status codes? Are request payloads validated and list endpoints bounded/paginated?
- **Security:** Is employee identity/authorization checked in production? Are secrets excluded from source control?

Replace this checklist with concrete, line-specific findings after reviewing the official snippet.
