# Synthetic browser fixtures

`responses/` contains five authored visualization responses built with the backend
chart builders using invented study values and entities. The Playwright fixture
server selects a response by example question. The examples in the app submit a
query to the configured backend.

`citations.json` contains synthetic exact phase values and paths, absent/null/empty/
invalid source fields, aligned comparison zeros, and weighted network evidence.
Study IDs and citation URLs in these files are test values. The source label and
URL shape follow the API contract. The browser tests need neither an API key nor
access to OpenAI or ClinicalTrials.gov.
