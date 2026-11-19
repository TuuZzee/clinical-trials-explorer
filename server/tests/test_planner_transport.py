import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI

from app.config import Settings
from app.errors import ServiceError
from app.planner import OpenAIPlanner
from app.schemas import VisualizationRequest
from tests.helpers.planner import output_response, plan_data, run_planner


def test_real_sdk_structured_output_request_and_configuration():
    result, calls = run_planner(
        output_response(plan_data()),
        settings_overrides={
            "openai_model": "gpt-4.1-mini",
            "openai_timeout_seconds": 12,
            "openai_max_output_tokens": 1500,
        },
    )
    body = json.loads(calls[0].content)
    assert body["model"] == "gpt-4.1-mini"
    assert body["store"] is False
    assert body["input"] == "Show trial phases"
    assert body["max_output_tokens"] == 1500
    assert "unit-test-key" not in calls[0].content.decode()
    assert "trial counts" not in result.model_dump_json()
    text_format = body["text"]["format"]
    assert text_format["type"] == "json_schema" and text_format["strict"] is True
    assert calls[0].extensions["timeout"]["read"] == 12
    assert calls[0].url == "https://api.openai.com/v1/responses"

    def verify_objects(schema):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                assert schema["additionalProperties"] is False
                assert set(schema["required"]) == set(schema["properties"])
            for value in schema.values():
                verify_objects(value)
        elif isinstance(schema, list):
            for value in schema:
                verify_objects(value)

    verify_objects(text_format["schema"])


@pytest.mark.parametrize("secret", [None, "", "   "])
def test_missing_credentials(secret):
    async def run():
        settings = Settings(_env_file=None, openai_api_key=secret)
        return await OpenAIPlanner(settings).plan(VisualizationRequest(query="Show trial phases"))

    with pytest.raises(ServiceError) as exc:
        asyncio.run(run())
    assert exc.value.code == "planner_not_configured" and exc.value.status_code == 503


@pytest.mark.parametrize(
    "status,code,http_status",
    [
        (400, "planner_request_failed", 502),
        (401, "planner_request_failed", 502),
        (403, "planner_request_failed", 502),
        (404, "planner_request_failed", 502),
        (429, "planner_unavailable", 503),
        (500, "planner_unavailable", 503),
        (503, "planner_unavailable", 503),
    ],
)
def test_openai_errors_have_no_raw_details_and_no_retry(status, code, http_status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            json={
                "error": {
                    "message": "private diagnostic unit-test-key",
                    "type": "test_error",
                    "code": "test",
                }
            },
        )

    with pytest.raises(ServiceError) as exc:
        run_planner(None, handler=handler)
    assert exc.value.code == code and exc.value.status_code == http_status
    assert "unit-test-key" not in str(exc.value) and "private" not in str(exc.value)
    assert len(calls) == 1  # SDK defaults would otherwise retry 429/5xx.


@pytest.mark.parametrize(
    "kind,code,status",
    [
        (httpx.ReadTimeout, "planner_timeout", 504),
        (httpx.ConnectError, "planner_unavailable", 503),
    ],
)
def test_transport_errors(kind, code, status):
    def handler(request):
        raise kind("private diagnostic", request=request)

    with pytest.raises(ServiceError) as exc:
        run_planner(None, handler=handler)
    assert exc.value.code == code and exc.value.status_code == status


def test_outer_wall_clock_timeout():
    async def run():
        async def handler(request):
            await asyncio.sleep(0.1)
            return httpx.Response(200, json=output_response(plan_data()))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            async with AsyncOpenAI(api_key="unit-test-key", http_client=http) as sdk:
                settings = Settings(
                    _env_file=None, openai_api_key="unit-test-key", openai_timeout_seconds=0.01
                )
                await OpenAIPlanner(settings, sdk).plan(VisualizationRequest(query="Show phases"))

    with pytest.raises(ServiceError) as exc:
        asyncio.run(run())
    assert exc.value.code == "planner_timeout"


def test_refusal_does_not_expose_raw_model_text():
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(refusal=True))
    assert exc.value.code == "planner_refused" and exc.value.status_code == 422
    assert "private" not in str(exc.value)


def test_incomplete_model_response():
    with pytest.raises(ServiceError) as exc:
        run_planner(output_response(plan_data(), status="incomplete"))
    assert exc.value.code == "planner_invalid_response"


def test_missing_output_text():
    payload = output_response()
    payload["output"] = []
    with pytest.raises(ServiceError) as exc:
        run_planner(payload)
    assert exc.value.code == "planner_invalid_response"


@pytest.mark.parametrize("payload", [{}, [], {"output": None}, {"output": [{}]}])
def test_malformed_provider_envelope(payload):
    with pytest.raises(ServiceError) as exc:
        run_planner(payload)
    assert exc.value.code == "planner_invalid_response"
