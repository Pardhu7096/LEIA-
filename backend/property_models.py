"""Validated staff draft fields. Unknown is different from no/zero."""

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

Answer = Literal["unknown", "yes", "no", "not_applicable"]
Source = Literal["unknown", "observed", "owner_reported", "document_checked"]


FacingDirection = Literal[
    "north", "northeast", "east", "southeast",
    "south", "southwest", "west", "northwest",
]

# GEO-M1 coordinate provenance. "gps": one on-demand foreground fix from the Agent CRM;
# "manual": typed or edited by the agent.
LocationSource = Literal["gps", "manual"]
MAX_GPS_ACCURACY_M = 100.0  # the Agent CRM refuses poorer fixes too
MAX_LOCATION_ACCURACY_M = 5000.0  # storage bound (database CHECK)
# Device clock handling (GEO-M1.1): a capture time is the phone's own timestamp and is
# stored exactly as sent, never rewritten. One clearly ahead of the server, or
# implausibly old, is rejected with guidance instead.
GPS_CLOCK_SKEW_TOLERANCE = timedelta(minutes=10)
GPS_EARLIEST_VALID = datetime(2020, 1, 1, tzinfo=timezone.utc)


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
    facing: FacingDirection | None = None
    # GEO-M1: how the coordinates were obtained. Staff-only; never in public payloads
    # or Digital Studio snapshots. Null source = recorded before GEO-M1 or by an older app.
    location_source: LocationSource | None = None
    location_accuracy_m: float | None = Field(default=None, ge=0, le=MAX_LOCATION_ACCURACY_M)
    location_captured_at: datetime | None = None

    @model_validator(mode="after")
    def validate_coordinates(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError(
                "Provide both latitude and longitude, or leave both empty."
            )
        has_meta = (
            self.location_source is not None
            or self.location_accuracy_m is not None
            or self.location_captured_at is not None
        )
        if self.latitude is None and has_meta:
            raise ValueError("Location details need coordinates.")
        if self.location_source == "gps":
            if self.location_accuracy_m is None or self.location_captured_at is None:
                raise ValueError("A GPS location needs its accuracy and capture time.")
            if self.location_accuracy_m > MAX_GPS_ACCURACY_M:
                raise ValueError(
                    f"GPS accuracy must be {MAX_GPS_ACCURACY_M:g} m or better; enter the coordinates manually instead."
                )
            captured = self.location_captured_at
            if captured.tzinfo is None:
                # Ambiguous without an offset; do not guess (GEO-M1.1).
                raise ValueError("The GPS capture time must include a time zone.")
            if captured < GPS_EARLIEST_VALID:
                raise ValueError("The GPS capture time is not valid. Recapture the location or enter the coordinates manually.")
            if captured > datetime.now(timezone.utc) + GPS_CLOCK_SKEW_TOLERANCE:
                raise ValueError(
                    "This phone's clock is ahead of the server, so the GPS capture time cannot be trusted. "
                    "Set the phone's date and time to automatic, then recapture the location, "
                    "or enter the coordinates manually."
                )
        elif self.location_source == "manual":
            if self.location_accuracy_m is not None or self.location_captured_at is not None:
                raise ValueError("Manually entered coordinates have no GPS accuracy or capture time.")
        elif self.location_accuracy_m is not None or self.location_captured_at is not None:
            raise ValueError("Set the location source when sending accuracy or capture time.")
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
