"""Validated staff draft fields. Unknown is different from no/zero."""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Answer = Literal["unknown", "yes", "no", "not_applicable"]
Source = Literal["unknown", "observed", "owner_reported", "document_checked"]


class PropertyDraft(BaseModel):
    model_config = ConfigDict(
        extra="forbid", str_strip_whitespace=True, allow_inf_nan=False
    )

    title: str = Field(min_length=1, max_length=200)
    property_type: Literal["flat", "independent_house", "plot"]
    description: str | None = Field(default=None, max_length=10000)
    price: int | None = Field(default=None, gt=0, le=9007199254740991)
    bedrooms: int | None = Field(default=None, ge=0, le=20)
    bathrooms: int | None = Field(default=None, ge=0, le=20)
    area_sqft: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )
    address: str | None = Field(default=None, max_length=2000)
    locality: str | None = Field(default=None, max_length=150)
    city: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(default=None, pattern=r"^[0-9]{6}$")
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    car_parking_spaces: int | None = Field(default=None, ge=0, le=1000)
    bike_parking_spaces: int | None = Field(default=None, ge=0, le=1000)
    parking_type: Literal[
        "unknown", "none", "open", "covered", "both", "not_applicable"
    ] = "unknown"
    parking_allocation: Literal["unknown", "reserved", "shared", "not_applicable"] = (
        "unknown"
    )
    parking_included: Answer = "unknown"
    parking_extra_cost: int | None = Field(default=None, ge=0, le=9007199254740991)
    parking_notes: str | None = Field(default=None, max_length=2000)
    parking_source: Source = "unknown"
    parking_checked_on: date | None = None

    electricity_connection: Answer = "unknown"
    electricity_meter: Literal["unknown", "separate", "shared", "not_applicable"] = (
        "unknown"
    )
    electricity_phase: Literal["unknown", "single", "three", "not_applicable"] = (
        "unknown"
    )
    power_backup: Literal[
        "unknown", "none", "common_areas", "partial_home", "full_home", "not_applicable"
    ] = "unknown"
    electricity_notes: str | None = Field(default=None, max_length=2000)
    electricity_source: Source = "unknown"
    electricity_checked_on: date | None = None

    water_municipal: Answer = "unknown"
    water_borewell: Answer = "unknown"
    water_tanker: Answer = "unknown"
    water_supply_frequency: Literal[
        "unknown", "daily", "alternate_days", "weekly", "irregular", "not_applicable"
    ] = "unknown"
    water_storage_litres: int | None = Field(default=None, ge=0, le=2147483647)
    water_connection: Literal["unknown", "separate", "shared", "not_applicable"] = (
        "unknown"
    )
    water_monthly_charge: int | None = Field(default=None, ge=0, le=9007199254740991)
    maintenance_monthly: int | None = Field(default=None, ge=0, le=9007199254740991)
    water_notes: str | None = Field(default=None, max_length=2000)
    water_source: Source = "unknown"
    water_checked_on: date | None = None

    owner_name: str | None = Field(default=None, max_length=150)
    owner_phone: str | None = Field(default=None, max_length=30)
    listing_consent: bool = False
    agent_notes: str | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def validate_coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "Provide both latitude and longitude, or leave both empty."
            )
        return self


class PropertyUpdate(PropertyDraft):
    version: int = Field(ge=1, le=2147483647)


class PropertySummary(BaseModel):
    id: UUID
    title: str
    property_type: str
    price: int | None
    locality: str | None
    city: str | None
    status: str
    availability: str
    created_at: datetime
    updated_at: datetime
    version: int


class PropertyDetails(PropertyDraft):
    id: UUID
    created_by: UUID
    status: str
    availability: str
    created_at: datetime
    updated_at: datetime
    version: int
