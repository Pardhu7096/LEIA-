
"""Permanent regression tests for AI conversation filter retention."""

import os
import sys
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault(
    "JWT_SECRET",
    "isolated-chat-test-secret-never-use-in-production-12345",
)
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")


@pytest.fixture
def chat_setup(monkeypatch):
    import main

    previous = main.SearchFilters(
        bedrooms=3,
        min_price=5000000,
        max_price=8000000,
        location="Visakhapatnam",
        property_type="independent_house",
        facing="northeast",
    ).model_dump()

    ai = Mock()
    search = Mock(return_value=[])

    monkeypatch.setattr(
        main.client.chat.completions, "create", ai
    )
    monkeypatch.setattr(main, "filter_properties", search)

    return main, previous, ai, search


def set_ai_response(ai, content):
    ai.return_value = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content)
            )
        ]
    )


@pytest.mark.parametrize(
    "message,payload,changes,should_search",
    [
        (
            "Increase budget to 1 crore",
            {"intent": "search", "max_price": 10000000},
            {"max_price": 10000000, "min_price": None},
            True,
        ),
        (
            "Show properties in Pendurthi",
            {"intent": "search", "location": "Pendurthi"},
            {"location": "Pendurthi"},
            True,
        ),
        (
            "Show flats instead",
            {"intent": "search", "property_type": "flat"},
            {"property_type": "flat"},
            True,
        ),
        (
            "Make it 4 BHK",
            {"intent": "search", "bedrooms": 4},
            {"bedrooms": 4},
            True,
        ),
        (
            "Make it east facing",
            {"intent": "search", "facing": "east"},
            {"facing": "east"},
            True,
        ),
        (
            "Any facing is fine",
            {"intent": "search", "facing": None},
            {"facing": None},
            True,
        ),
        (
            "Above 90 lakh",
            {"intent": "search", "min_price": 9000000},
            {"min_price": 9000000, "max_price": None},
            True,
        ),
        (
            "Between 90 and 60 lakh",
            {
                "intent": "search",
                "min_price": 9000000,
                "max_price": 6000000,
            },
            {},
            False,
        ),
        (
            "East or north facing",
            {
                "intent": "clarify",
                "question": "Which facing do you prefer?",
            },
            {},
            False,
        ),
        (
            "Hello LEIA",
            {"intent": "conversation"},
            {},
            False,
        ),
        (
            "Start over",
            {"intent": "reset"},
            None,
            False,
        ),
        (
            "Show properties",
            {"intent": "search"},
            {},
            True,
        ),
        (
            "No budget limit",
            {
                "intent": "search",
                "min_price": None,
                "max_price": None,
            },
            {"min_price": None, "max_price": None},
            True,
        ),
        (
            "Any location",
            {"intent": "search", "location": None},
            {"location": None},
            True,
        ),
        (
            "Any bedrooms",
            {"intent": "search", "bedrooms": None},
            {"bedrooms": None},
            True,
        ),
        (
            "Any property type",
            {"intent": "search", "property_type": None},
            {"property_type": None},
            True,
        ),
        (
            "Show all properties",
            {
                "intent": "search",
                "bedrooms": None,
                "min_price": None,
                "max_price": None,
                "location": None,
                "property_type": None,
                "facing": None,
            },
            None,
            True,
        ),
        (
            "Between 60 and 90 lakh",
            {
                "intent": "search",
                "min_price": 6000000,
                "max_price": 9000000,
            },
            {"min_price": 6000000, "max_price": 9000000},
            True,
        ),
    ],
)
def test_conversation_filter_updates(
    chat_setup, message, payload, changes, should_search
):
    main, previous, ai, search = chat_setup

    set_ai_response(ai, json.dumps(payload))

    expected = (
        main.SearchFilters().model_dump()
        if changes is None
        else {**previous, **changes}
    )

    request = main.ChatRequest(
        message=message,
        filters=previous,
    )

    result = main.generate_chat_reply(request)

    assert result["filters"] == expected
    assert result["properties"] == []
    assert request.filters.model_dump() == previous
    assert ai.call_count == 1
    assert search.call_count == int(should_search)

    ai_input = json.loads(
        ai.call_args.kwargs["messages"][-1]["content"]
    )

    assert ai_input["current_filters"] == previous
    assert ai_input["message"] == message

    if should_search:
        assert search.call_args.args[0].model_dump() == expected
        assert result["reply"] == main.NO_MATCHES_REPLY

    elif payload["intent"] == "clarify":
        assert result["reply"] == payload["question"]

    elif payload["intent"] == "reset":
        assert result["reply"] == main.RESET_REPLY


@pytest.mark.parametrize(
    "content",
    [
        "not valid JSON",
        "",
        "{}",
        "[]",
        '{"intent":"unsupported"}',
        '{"intent":"search","facing":"invalid"}',
        '{"intent":"search","facing":"north-east"}',
        '{"intent":"search","max_price":-1}',
        '{"intent":"search","bedrooms":0}',
    ],
)
def test_invalid_ai_response_is_rejected(chat_setup, content):
    main, previous, ai, search = chat_setup

    set_ai_response(ai, content)

    request = main.ChatRequest(
        message="Update my search",
        filters=previous,
    )

    with pytest.raises(HTTPException) as exc:
        main.generate_chat_reply(request)

    assert exc.value.status_code == 502
    assert request.filters.model_dump() == previous
    search.assert_not_called()
