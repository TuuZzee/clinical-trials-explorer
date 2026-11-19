from app.clinical_trials import RetrievalResult


def retrieval(studies, **overrides):
    return RetrievalResult(
        studies=studies,
        total_matches=len({s.nct_id for s in studies}),
        retrieved_records=len(studies),
        pages_fetched=1,
        **overrides,
    )
