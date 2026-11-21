"""Permanent regression tests for LEIA property facing."""

import os
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault(
    "JWT_SECRET",
    "isolated-facing-test-secret-never-use-in-production-12345"
)

from property_models import PropertyDraft, PropertyUpdate


DIRECTIONS = (
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
)

INVALID_DIRECTIONS = (
    "invalid",
    "unknown",
    "north-east",
    "north east",
    "East",
    "N",
    "",
    123,
    ["north", "east"],
)


@pytest.fixture(params=[
    "PropertyDraft",
    "PropertyUpdate",
    "SearchFilters",
    "SearchRequirements",
])
def model_case(request, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-groq-key")

    from main import SearchFilters, SearchRequirements

    cases = {
        "PropertyDraft": (
            PropertyDraft,
            {"title": "Test Home", "property_type": "flat"},
        ),
        "PropertyUpdate": (
            PropertyUpdate,
            {
                "title": "Test Home",
                "property_type": "flat",
                "version": 1,
            },
        ),
        "SearchFilters": (SearchFilters, {}),
        "SearchRequirements": (
            SearchRequirements,
            {"intent": "search"},
        ),
    }

    return cases[request.param]


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_accepts_all_eight_directions(model_case, direction):
    model, payload = model_case

    result = model.model_validate({
        **payload,
        "facing": direction,
    })

    assert result.facing == direction
    assert result.model_dump()["facing"] == direction


@pytest.mark.parametrize("direction", INVALID_DIRECTIONS)
def test_rejects_invalid_directions(model_case, direction):
    model, payload = model_case

    with pytest.raises(ValidationError) as exc:
        model.model_validate({
            **payload,
            "facing": direction,
        })

    assert any(
        error["loc"] == ("facing",)
        for error in exc.value.errors()
    )


def test_missing_facing_defaults_to_none(model_case):
    model, payload = model_case

    result = model.model_validate(payload)

    assert result.facing is None


def test_explicit_null_facing_is_allowed(model_case):
    model, payload = model_case

    result = model.model_validate({
        **payload,
        "facing": None,
    })

    assert result.facing is None


@pytest.mark.parametrize("direction", DIRECTIONS)
def test_facing_json_roundtrip(model_case, direction):
    model, payload = model_case

    original = model.model_validate({
        **payload,
        "facing": direction,
    })

    restored = model.model_validate_json(
        original.model_dump_json()
    )

    assert restored.facing == direction
    assert restored == original
