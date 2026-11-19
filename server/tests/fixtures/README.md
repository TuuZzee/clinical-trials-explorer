# Backend test fixtures

`study.json` is a synthetic ClinicalTrials.gov-shaped record. Its identifier,
sponsor, interventions, conditions, enrollment count, and dates are test values.
Repeated location entries exercise country deduplication. Reported intervention
types, dates, and enrollment fields exercise normalization and exact source evidence.

`planner_reliability.json` and `semantic_evaluation.json` contain authored queries
and mock plans for offline validation tests. They contain no registry responses.
