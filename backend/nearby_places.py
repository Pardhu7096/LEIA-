import json
import os
from typing import Literal
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from auth import get_current_user
from database import get_connection

router = APIRouter(prefix="/properties", tags=["Nearby places"])

Category = Literal["schools", "bus_stops", "groceries", "hospitals", "temples"]
PLACE_TYPES: dict[Category, list[str]] = {
    "schools": ["school", "primary_school", "secondary_school"],
    "bus_stops": ["bus_stop", "bus_station"],
    "groceries": ["grocery_store", "supermarket"],
    "hospitals": ["hospital"],
    "temples": ["hindu_temple"],
}


def find_places(latitude: float, longitude: float, category: Category, api_key: str) -> list[dict]:
    request = Request(
        "https://places.googleapis.com/v1/places:searchNearby",
        data=json.dumps({
            "includedTypes": PLACE_TYPES[category],
            "maxResultCount": 5,
            "rankPreference": "DISTANCE",
            "locationRestriction": {
                "circle": {
                    "center": {"latitude": latitude, "longitude": longitude},
                    "radius": 5000,
                },
            },
        }).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": "places.id,places.displayName,places.formattedAddress,places.attributions",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=5) as upstream:
            result = json.load(upstream)
    except (URLError, OSError, ValueError) as exc:
        raise HTTPException(502, "Google Maps could not load nearby places. Try again.") from exc

    places = []
    for place in result.get("places", []):
        name = place.get("displayName", {}).get("text")
        place_id = place.get("id")
        address = place.get("formattedAddress")
        if not name or not place_id:
            continue
        directions = urlencode({
            "api": "1",
            "origin": f"{latitude},{longitude}",
            "destination": address or name,
            "destination_place_id": place_id,
        })
        places.append({
            "id": place_id,
            "name": name,
            "address": address,
            "directions_url": f"https://www.google.com/maps/dir/?{directions}",
            "attributions": place.get("attributions", []),
        })
    return places


@router.get("/{property_id}/nearby")
def nearby_places(
    property_id: UUID,
    category: Category,
    response: Response,
    _user=Depends(get_current_user),
):
    response.headers["Cache-Control"] = "no-store"
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT latitude, longitude FROM public.properties
                   WHERE id = %s AND status = 'approved' AND availability = 'available';""",
                (property_id,),
            )
            property_pin = cursor.fetchone()

    if property_pin is None:
        raise HTTPException(404, "This listing is no longer available.")
    latitude, longitude = property_pin["latitude"], property_pin["longitude"]
    if latitude is None or longitude is None:
        return {"status": "missing_pin", "places": []}
    api_key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(503, "Nearby places are not configured yet.")
    return {"status": "ready", "places": find_places(latitude, longitude, category, api_key)}
