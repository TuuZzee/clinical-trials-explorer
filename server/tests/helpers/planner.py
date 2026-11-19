import asyncio
import json

import httpx
from openai import AsyncOpenAI
from pydantic import SecretStr

from app.config import Settings
from app.planner import OpenAIPlanner
from app.schemas import VisualizationRequest


def plan_data(**overrides):
    return {
        "intent": "distribution",
        "measure": "study_count",
        "filters": {},
        "group_by": "phase",
        "chart_type": "bar_chart",
        "comparison": None,
        **overrides,
    }


def output_response(
    plan=None, decision="supported", *, text=None, status="completed", refusal=False
):
    content = (
        {"type": "refusal", "refusal": "private refusal diagnostic"}
        if refusal
        else {
            "type": "output_text",
            "annotations": [],
            "text": text if text is not None else json.dumps({"status": decision, "plan": plan}),
        }
    )
    return {
        "id": "resp_test",
        "object": "response",
        "created_at": 0,
        "status": status,
        "model": "gpt-5.4-mini",
        "output": [
            {
                "id": "msg_test",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [content],
            }
        ],
        "parallel_tool_calls": False,
        "tools": [],
        "tool_choice": "auto",
    }


def run_planner(payload, request=None, *, handler=None, settings_overrides=None):
    calls = []

    def transport(request):
        calls.append(request)
        return handler(request) if handler else httpx.Response(200, json=payload)

    async def run():
        settings = Settings(
            _env_file=None, openai_api_key=SecretStr("unit-test-key"), **(settings_overrides or {})
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            async with AsyncOpenAI(api_key="unit-test-key", http_client=http) as sdk:
                result = await OpenAIPlanner(settings, sdk).plan(
                    request or VisualizationRequest(query="Show trial phases")
                )
        return result

    return asyncio.run(run()), calls
