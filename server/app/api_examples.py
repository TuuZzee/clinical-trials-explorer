"""Named OpenAPI request examples; query is primary and shared filters bound retrieval."""

NARROW_FILTERS = {
    "sponsor": "National Cancer Institute",
    "country": "United States",
    "trial_status": "COMPLETED",
    "start_year": 2015,
    "end_year": 2025,
}
REQUEST_EXAMPLES = {
    "phase_distribution": {
        "summary": "Phase distribution with source citations",
        "description": "Completed, US-location NCI cohort with optional exact source evidence.",
        "value": {
            "query": "Show phases of breast cancer trials involving pembrolizumab",
            "include_citations": True,
            **NARROW_FILTERS,
        },
    },
    "annual_trends": {
        "summary": "Annual study-start trends",
        "description": "Years refer to study start dates; only observed years are returned.",
        "value": {
            "query": "How many breast cancer trials involving pembrolizumab started each year?",
            **NARROW_FILTERS,
        },
    },
    "two_drug_comparison": {
        "summary": "Two-drug phase comparison",
        "description": "Two independent intervention searches; studies may match both cohorts.",
        "value": {
            "query": "Compare phases of lung cancer trials involving nivolumab versus ipilimumab",
            **NARROW_FILTERS,
        },
    },
    "enrollment_histogram": {
        "summary": "Enrollment-size histogram",
        "description": "Study counts by enrollment size; types are disclosed.",
        "value": {
            "query": "Show breast cancer enrollment histogram involving pembrolizumab",
            **NARROW_FILTERS,
        },
    },
    "sponsor_drug_network": {
        "summary": "Lead sponsor–drug network",
        "description": "Reported DRUG types only; edges represent co-occurrence.",
        "value": {
            "query": "Show sponsor-drug network for breast cancer trials involving pembrolizumab",
            **NARROW_FILTERS,
        },
    },
}
