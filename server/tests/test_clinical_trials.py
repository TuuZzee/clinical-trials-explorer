import asyncio
from copy import deepcopy

import httpx
import pytest

from app.clinical_trials import ClinicalTrialsClient, compile_filters, normalize_study
from app.config import Settings
from app.errors import ServiceError
from app.schemas import StudyFilters


def study(nct_id="NCT00000001"):
    return {"protocolSection": {"identificationModule": {"nctId": nct_id}}}


def run_search(handler, filters=None, **config):
    async def run():
        delays = []

        async def sleep(delay):
            delays.append(delay)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            adapter = ClinicalTrialsClient(http, Settings(_env_file=None, **config), sleep)
            result = await adapter.search(filters or StudyFilters())
        return result, delays

    return asyncio.run(run())


def test_compile_filters():
    filters = StudyFilters(
        drug_name="pembrolizumab",
        condition="breast cancer",
        sponsor="National Cancer Institute",
        country="United States",
        trial_status="COMPLETED",
        start_year=2015,
        end_year=2025,
    )
    assert compile_filters(filters) == {
        "format": "json",
        "countTotal": "true",
        "query.intr": '"pembrolizumab"',
        "query.cond": '"breast cancer"',
        "query.spons": '"National Cancer Institute"',
        "filter.overallStatus": "COMPLETED",
        "filter.advanced": 'AREA[LocationCountry]"United States" AND '
        "AREA[StartDate]RANGE[2015-01-01,2025-12-31]",
    }
    assert compile_filters(StudyFilters(start_year=2020))["filter.advanced"].endswith(",MAX]")
    assert (
        "RANGE[MIN,2020-12-31]" in compile_filters(StudyFilters(end_year=2020))["filter.advanced"]
    )
    assert compile_filters(StudyFilters()) == {"format": "json", "countTotal": "true"}


def test_paging_preserves_filters_and_timeout():
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                200, json={"studies": [study()], "nextPageToken": "abc", "totalCount": 2}
            )
        return httpx.Response(200, json={"studies": [study("NCT00000002")]})

    result, _ = run_search(handler, StudyFilters(condition="asthma"), ctgov_page_size=1)
    assert len(result.studies) == 2
    assert result.total_matches == 2
    assert result.pages_fetched == 2 and result.complete_results
    assert requests[1].url.params["pageToken"] == "abc"
    assert requests[0].url.params["query.cond"] == requests[1].url.params["query.cond"]
    assert requests[0].extensions["timeout"]["read"] == 20


def test_empty_intermediate_page_and_deduplication():
    pages = iter(
        [
            {"studies": [study()], "nextPageToken": "a"},
            {"studies": [], "nextPageToken": "b"},
            {"studies": [study(), study("NCT00000002")]},
        ]
    )
    result, _ = run_search(lambda _: httpx.Response(200, json=next(pages)))
    assert len(result.studies) == 2 and result.retrieved_records == 3
    assert any("Duplicate" in warning for warning in result.warnings)


def test_empty_results():
    result, _ = run_search(lambda _: httpx.Response(200, json={"studies": [], "totalCount": 0}))
    assert result.studies == [] and result.complete_results


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2020", 2020),
        ("2020-02", 2020),
        ("2020-02-29", 2020),
        ("2020-99", None),
        ("2021-02-29", None),
        ("bad", None),
        (None, None),
    ],
)
def test_date_precision(value, expected):
    raw = study()
    raw["protocolSection"]["statusModule"] = {"startDateStruct": {"date": value}}
    assert normalize_study(raw).start_year == expected


def test_missing_and_multivalued_fields(synthetic_study):
    minimal = normalize_study(study())
    assert minimal.phases == () and minimal.start_year is None
    raw = deepcopy(synthetic_study)
    p = raw["protocolSection"]
    p["designModule"]["phases"] = ["PHASE1", "PHASE2", "PHASE1"]
    p["contactsLocationsModule"]["locations"] = [
        {"country": "US"},
        {"country": "US"},
        {"country": "Korea"},
        {},
        None,
    ]
    assert normalize_study(raw).phases == ("PHASE1", "PHASE2")
    assert normalize_study(raw).countries == ("Korea", "US")
    p.update(
        statusModule=None,
        designModule={"phases": None},
        armsInterventionsModule={"interventions": None},
    )
    assert normalize_study(raw).interventions == ()


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_retry(status):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(status, headers={"Retry-After": "9999"})
        return httpx.Response(200, json={"studies": []})

    result, delays = run_search(handler)
    assert result.complete_results and len(calls) == 2
    assert delays == [5]


def test_timeout_retry_budget():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("private upstream diagnostic")

    with pytest.raises(ServiceError) as exc:
        run_search(handler)
    assert len(calls) == 3
    assert exc.value.code == "upstream_unavailable"
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("status", [400, 401, 404, 302])
def test_no_retry_for_permanent_errors(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, text="do not expose body")

    with pytest.raises(ServiceError) as exc:
        run_search(handler)
    assert len(calls) == 1 and exc.value.code == "upstream_rejected_request"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"studies": None},
        {"studies": [None]},
        {"studies": [{}]},
        {"studies": [study("bad")]},
        {"studies": [], "totalCount": "1"},
        {"studies": [], "nextPageToken": ""},
        {"studies": [], "totalCount": -1},
    ],
)
def test_malformed_response(payload):
    with pytest.raises(ServiceError) as exc:
        run_search(lambda _: httpx.Response(200, json=payload))
    assert exc.value.code == "upstream_invalid_response"


def test_invalid_json():
    with pytest.raises(ServiceError) as exc:
        run_search(lambda _: httpx.Response(200, text="<html>unavailable</html>"))
    assert exc.value.code == "upstream_invalid_response"


@pytest.mark.parametrize(
    ("pages", "config", "code"),
    [
        (
            [{"studies": [study()], "nextPageToken": "a"}],
            {"ctgov_max_pages": 1},
            "incomplete_retrieval",
        ),
        (
            [{"studies": [study()], "nextPageToken": "a"}],
            {"ctgov_max_studies": 1},
            "incomplete_retrieval",
        ),
        ([{"studies": [], "totalCount": 2}], {"ctgov_max_studies": 1}, "incomplete_retrieval"),
        ([{"studies": [study()], "totalCount": 2}], {}, "incomplete_retrieval"),
        (
            [{"studies": [study(), study("NCT00000002")]}],
            {"ctgov_max_studies": 1},
            "incomplete_retrieval",
        ),
        (
            [{"studies": [], "nextPageToken": "a"}, {"studies": [], "nextPageToken": "a"}],
            {},
            "upstream_invalid_response",
        ),
        (
            [
                {"studies": [], "totalCount": 2, "nextPageToken": "a"},
                {"studies": [], "totalCount": 3},
            ],
            {},
            "incomplete_retrieval",
        ),
    ],
)
def test_incomplete_and_repeated_tokens(pages, config, code):
    responses = iter(pages)
    with pytest.raises(ServiceError) as exc:
        run_search(lambda _: httpx.Response(200, json=next(responses)), **config)
    assert exc.value.code == code


def test_retry_exhaustion_and_backoff():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(503)

    with pytest.raises(ServiceError) as exc:
        run_search(handler, ctgov_max_retries=1)
    assert len(calls) == 2 and exc.value.status_code == 503


def test_exact_cap_is_complete_without_next_token():
    result, _ = run_search(
        lambda _: httpx.Response(200, json={"studies": [study()], "totalCount": 1}),
        ctgov_max_studies=1,
    )
    assert result.complete_results


@pytest.mark.parametrize("stage", ["network", "backoff"])
def test_cancellation_stops_registry_requests_and_retries(stage):
    async def run():
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        calls = []

        async def handler(request):
            calls.append(request)
            if stage == "backoff":
                return httpx.Response(429)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async def sleep(_):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            adapter = ClinicalTrialsClient(http, Settings(_env_file=None), sleep)
            task = asyncio.create_task(adapter.search(StudyFilters()))
            await asyncio.wait_for(entered.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert cancelled.is_set() and len(calls) == 1
        assert http.is_closed

    asyncio.run(run())
