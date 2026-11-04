from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field, model_validator

from auth import require_staff
from database import get_connection


router = APIRouter(prefix="/staff/properties", tags=["Agent CRM"])


class PropertyDraft(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    title: str = Field(min_length=1, max_length=200)
    property_type: Literal["flat", "independent_house", "plot"]

    description: str | None = Field(default=None, max_length=10000)
    price: int | None = Field(
        default=None, gt=0, le=9223372036854775807
    )
    bedrooms: int | None = Field(default=None, ge=0, le=20)
    bathrooms: int | None = Field(default=None, ge=0, le=20)
    area_sqft: Decimal | None = Field(
        default=None, gt=0, max_digits=12, decimal_places=2
    )

    address: str | None = Field(default=None, max_length=2000)
    locality: str | None = Field(default=None, max_length=150)
    city: str | None = Field(default=None, max_length=100)
    pincode: str | None = Field(
        default=None, pattern=r"^[0-9]{6}$"
    )

    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

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


@router.post("", status_code=201)
def create_property(
    payload: PropertyDraft,
    user=Depends(require_staff),
):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO public.properties (
                    created_by,
                    title,
                    property_type,
                    description,
                    price,
                    bedrooms,
                    bathrooms,
                    area_sqft,
                    address,
                    locality,
                    city,
                    pincode,
                    latitude,
                    longitude
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s
                )
                RETURNING
                    id, title, property_type, status,
                    availability, created_at, updated_at;
                """,
                (
                    user["id"],
                    payload.title,
                    payload.property_type,
                    payload.description,
                    payload.price,
                    payload.bedrooms,
                    payload.bathrooms,
                    payload.area_sqft,
                    payload.address,
                    payload.locality,
                    payload.city,
                    payload.pincode,
                    payload.latitude,
                    payload.longitude,
                ),
            )
            property_record = cursor.fetchone()

            cursor.execute(
                """
                INSERT INTO public.property_private_details (
                    property_id,
                    owner_name,
                    owner_phone,
                    listing_consent,
                    consent_recorded_at,
                    agent_notes
                )
                VALUES (
                    %s, %s, %s, %s,
                    CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END,
                    %s
                );
                """,
                (
                    property_record["id"],
                    payload.owner_name,
                    payload.owner_phone,
                    payload.listing_consent,
                    payload.listing_consent,
                    payload.agent_notes,
                ),
            )

    return property_record


@router.get("")
def list_properties(user=Depends(require_staff)):
    # Agents see their own records. Admins see all records.
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    id, title, property_type, price,
                    locality, city, status, availability,
                    created_at, updated_at
                FROM public.properties
                WHERE created_by = %s OR %s = 'admin'
                ORDER BY created_at DESC, id DESC
                LIMIT 100;
                """,
                (user["id"], user["role"]),
            )
            return cursor.fetchall()