from uuid import UUID

from auth import require_staff
from database import get_connection
from fastapi import APIRouter, Depends, HTTPException, Response
from property_models import (
    PropertyDetails,
    PropertyDraft,
    PropertySummary,
    PropertyUpdate,
)
from psycopg import sql

router = APIRouter(prefix="/staff/properties", tags=["Agent CRM"])
PRIVATE_FIELDS = ("owner_name", "owner_phone", "listing_consent", "agent_notes")
# Field names come exclusively from our model, never from request-controlled SQL.
PUBLIC_FIELDS = tuple(
    name for name in PropertyDraft.model_fields if name not in PRIVATE_FIELDS
)
SUMMARY_FIELDS = (
    "id",
    "title",
    "property_type",
    "price",
    "locality",
    "city",
    "status",
    "availability",
    "created_at",
    "updated_at",
    "version",
)
DETAIL_FIELDS = (
    "id",
    "created_by",
    *PUBLIC_FIELDS,
    "status",
    "availability",
    "created_at",
    "updated_at",
    "version",
)


def columns(names, alias=None):
    return sql.SQL(", ").join(
        sql.Identifier(alias, name) if alias else sql.Identifier(name) for name in names
    )


def save_private_details(cursor, property_id, payload):
    cursor.execute(
        """
        INSERT INTO public.property_private_details AS current (
            property_id, owner_name, owner_phone, listing_consent,
            consent_recorded_at, agent_notes
        ) VALUES (%s, %s, %s, %s,
            CASE WHEN %s THEN CURRENT_TIMESTAMP ELSE NULL END, %s)
        ON CONFLICT (property_id) DO UPDATE SET
            owner_name = EXCLUDED.owner_name,
            owner_phone = EXCLUDED.owner_phone,
            listing_consent = EXCLUDED.listing_consent,
            consent_recorded_at = CASE
                WHEN NOT EXCLUDED.listing_consent THEN NULL
                WHEN current.listing_consent THEN current.consent_recorded_at
                ELSE CURRENT_TIMESTAMP END,
            agent_notes = EXCLUDED.agent_notes;
        """,
        (
            property_id,
            payload.owner_name,
            payload.owner_phone,
            payload.listing_consent,
            payload.listing_consent,
            payload.agent_notes,
        ),
    )


def read_details(cursor, property_id, user):
    cursor.execute(
        sql.SQL("""
            SELECT {}, d.owner_name, d.owner_phone,
                COALESCE(d.listing_consent, FALSE) AS listing_consent, d.agent_notes
            FROM public.properties AS p
            LEFT JOIN public.property_private_details AS d ON d.property_id = p.id
            WHERE p.id = %s AND (p.created_by = %s OR %s = 'admin');
        """).format(columns(DETAIL_FIELDS, "p")),
        (property_id, user["id"], user["role"]),
    )
    record = cursor.fetchone()
    if record is None:
        # Do not disclose whether another agent's property exists.
        raise HTTPException(status_code=404, detail="Property not found.")
    return record


@router.post("", status_code=201, response_model=PropertySummary)
def create_property(
    payload: PropertyDraft, response: Response, user=Depends(require_staff)
):
    response.headers["Cache-Control"] = "no-store"
    with get_connection() as connection:
        with connection.cursor() as cursor:
            names = ("created_by", *PUBLIC_FIELDS)
            cursor.execute(
                sql.SQL(
                    "INSERT INTO public.properties ({}) VALUES ({}) RETURNING {};"
                ).format(
                    columns(names),
                    sql.SQL(", ").join(sql.Placeholder() for _ in names),
                    columns(SUMMARY_FIELDS),
                ),
                (user["id"], *(getattr(payload, name) for name in PUBLIC_FIELDS)),
            )
            record = cursor.fetchone()
            save_private_details(cursor, record["id"], payload)
    return record


@router.get("", response_model=list[PropertySummary])
def list_properties(response: Response, user=Depends(require_staff)):
    response.headers["Cache-Control"] = "no-store"
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("""SELECT {} FROM public.properties
                    WHERE created_by = %s OR %s = 'admin'
                    ORDER BY created_at DESC, id DESC LIMIT 100;""").format(
                    columns(SUMMARY_FIELDS)
                ),
                (user["id"], user["role"]),
            )
            return cursor.fetchall()


@router.get("/{property_id}", response_model=PropertyDetails)
def get_property(property_id: UUID, response: Response, user=Depends(require_staff)):
    response.headers["Cache-Control"] = "no-store"
    with get_connection() as connection:
        with connection.cursor() as cursor:
            return read_details(cursor, property_id, user)


@router.put("/{property_id}", response_model=PropertyDetails)
def update_property(
    property_id: UUID,
    payload: PropertyUpdate,
    response: Response,
    user=Depends(require_staff),
):
    response.headers["Cache-Control"] = "no-store"
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT status, version FROM public.properties
                   WHERE id = %s AND (created_by = %s OR %s = 'admin')
                   FOR UPDATE;""",
                (property_id, user["id"], user["role"]),
            )
            existing = cursor.fetchone()
            if existing is None:
                raise HTTPException(status_code=404, detail="Property not found.")
            if existing["status"] != "draft":
                raise HTTPException(
                    status_code=409, detail="Only draft properties can be edited."
                )
            if existing["version"] != payload.version:
                raise HTTPException(
                    status_code=409,
                    detail="This draft changed since you opened it. Reopen it to load the latest version before saving.",
                )
            cursor.execute(
                sql.SQL("""UPDATE public.properties SET {},
                    version = version + 1, updated_at = clock_timestamp()
                    WHERE id = %s;""").format(
                    sql.SQL(", ").join(
                        sql.SQL("{} = %s").format(sql.Identifier(name))
                        for name in PUBLIC_FIELDS
                    )
                ),
                (*(getattr(payload, name) for name in PUBLIC_FIELDS), property_id),
            )
            save_private_details(cursor, property_id, payload)
            return read_details(cursor, property_id, user)
