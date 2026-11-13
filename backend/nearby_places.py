
import json
import os
from typing import Literal
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response

from auth import get_current_user
from database import get_connection
from rate_limits import limit_maps


router = APIRouter(
    prefix="/properties",
    tags=["Nearby places"],
)

Category = Literal[
    "schools",
    "bus_stops",
    "groceries",
    "hospitals",
    "temples",
]

PLACE_TYPES: dict[Category, list[str]] = {
    "schools": [
        "school",
        "primary_school",
        "secondary_school",
    ],
    "bus_stops": [
        "bus_stop",
        "bus_station",
    ],
    "groceries": [
        "grocery_store",
        "supermarket",
    ],
    "hospitals": ["hospital"],
    "temples": ["hindu_temple"],
}


# Only these two Google API endpoints are permitted.
ALLOWED_GOOGLE_ENDPOINTS = frozenset({
    "https://places.googleapis.com/v1/places:searchNearby",
    "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix",
})


class NoRedirectHandler(HTTPRedirectHandler):
    """Prevent Google API requests from following redirects."""

    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        return None


def google_maps_request(
    url: str,
    payload: dict,
    api_key: str,
    field_mask: str,
):
    """
    Send a JSON POST request to an approved Google API.

    Security:
    - Exact HTTPS endpoint allowlist
    - Redirects disabled
    - Five-second timeout
    - API key supplied through request headers
    """

    if url not in ALLOWED_GOOGLE_ENDPOINTS:
        raise ValueError(
            "Google Maps endpoint is not allowed."
        )

    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Goog-Api-Key": api_key,
            "X-Goog-FieldMask": field_mask,
        },
        method="POST",
    )

    opener = build_opener(NoRedirectHandler())

    with opener.open(request, timeout=5) as upstream:
        return json.load(upstream)


def find_places(
    latitude: float,
    longitude: float,
    category: Category,
    api_key: str,
) -> list[dict]:
    """Find nearby places within a 5 km radius."""

    try:
        result = google_maps_request(
            "https://places.googleapis.com/v1/places:searchNearby",
            {
                "includedTypes": PLACE_TYPES[category],
                "maxResultCount": 5,
                "rankPreference": "DISTANCE",
                "locationRestriction": {
                    "circle": {
                        "center": {
                            "latitude": latitude,
                            "longitude": longitude,
                        },
                        "radius": 5000,
                    },
                },
            },
            api_key,
            (
                "places.id,"
                "places.displayName,"
                "places.formattedAddress,"
                "places.attributions"
            ),
        )

    except (URLError, OSError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail=(
                "Google Maps could not load nearby places. "
                "Try again."
            ),
        ) from exc

    if not isinstance(result, dict):
        raise HTTPException(
            status_code=502,
            detail="Invalid Google Maps response.",
        )

    places = []

    for place in result.get("places", []):
        name = place.get(
            "displayName", {}
        ).get("text")

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
            "directions_url": (
                "https://www.google.com/maps/dir/"
                f"?{directions}"
            ),
            "attributions": place.get(
                "attributions", []
            ),
        })

    return places


def shortest_nearby_walk(
    latitude: float,
    longitude: float,
    category: Category,
) -> str:
    """
    Find the shortest walking route to a nearby place.
    """

    places_key = os.getenv(
        "GOOGLE_PLACES_API_KEY", ""
    ).strip()

    routes_key = os.getenv(
        "GOOGLE_ROUTES_API_KEY", ""
    ).strip()

    if not places_key or not routes_key:
        return (
            "Walking estimates are unavailable right now. "
            "You can still check the listing's Nearby places."
        )

    unavailable = (
        "I couldn't get a walking estimate from "
        "Google Maps. Please try again."
    )

    category_name = (
        "Hindu temples"
        if category == "temples"
        else category.replace("_", " ")
    )

    try:
        places = find_places(
            latitude,
            longitude,
            category,
            places_key,
        )

        if not places:
            return (
                f"Google Maps found no {category_name} "
                "within 5 km of this listing."
            )

        routes = google_maps_request(
            "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix",
            {
                "origins": [
                    {
                        "waypoint": {
                            "location": {
                                "latLng": {
                                    "latitude": latitude,
                                    "longitude": longitude,
                                }
                            }
                        }
                    }
                ],
                "destinations": [
                    {
                        "waypoint": {
                            "placeId": place["id"]
                        }
                    }
                    for place in places
                ],
                "travelMode": "WALK",
            },
            routes_key,
            (
                "destinationIndex,"
                "status,"
                "condition,"
                "distanceMeters,"
                "duration"
            ),
        )

        if not isinstance(routes, list) or not routes:
            return unavailable

        walks = []
        failed = False

        for route in routes:
            if route.get(
                "status", {}
            ).get("code", 0):
                failed = True
                continue

            if route.get("condition") == "ROUTE_NOT_FOUND":
                continue

            if route.get("condition") != "ROUTE_EXISTS":
                return unavailable

            distance = route.get(
                "distanceMeters", 0
            )

            seconds = float(
                route["duration"].removesuffix("s")
            )

            index = route.get(
                "destinationIndex", 0
            )

            if (
                not isinstance(index, int)
                or not 0 <= index < len(places)
                or not isinstance(distance, int)
                or distance < 0
                or not 0 <= seconds < float("inf")
            ):
                return unavailable

            walks.append((
                distance,
                seconds,
                index,
            ))

    except (
        HTTPException,
        URLError,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
    ):
        return unavailable

    if not walks:
        if failed:
            return unavailable

        return (
            f"Google Maps found nearby {category_name} "
            "but couldn't find a walking route to any of them."
        )

    distance, seconds, index = min(walks)

    place = places[index]

    distance_label = (
        f"{distance:,} m"
        if distance < 1000
        else f"{distance / 1000:.1f} km"
    )

    minutes = max(
        1,
        round(seconds / 60),
    )

    minute_label = (
        "minute"
        if minutes == 1
        else "minutes"
    )

    credits = "".join(
        (
            f"\n{credit['provider']} "
            f"{credit.get('providerUri', '')}"
        ).rstrip()
        for credit in place["attributions"]
        if credit.get("provider")
    )

    return (
        f"Shortest walking route found: "
        f"{place['name']} · "
        f"{distance_label} · "
        f"about {minutes} {minute_label} on foot."
        f"\n\nGoogle Maps{credits}"
        "\nWalking routes may be missing "
        "sidewalks or pedestrian paths."
    )


@router.get("/{property_id}/nearby")
def nearby_places(
    property_id: UUID,
    category: Category,
    response: Response,
    _user=Depends(get_current_user),
):
    """
    Return nearby places for an approved,
    available property.

    Requires an authenticated user.
    """

    limit_maps(_user)
    response.headers["Cache-Control"] = "no-store"

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT latitude, longitude
                FROM public.properties
                WHERE id = %s
                  AND status = 'approved'
                  AND availability = 'available';
                """,
                (property_id,),
            )

            property_pin = cursor.fetchone()

    if property_pin is None:
        raise HTTPException(
            status_code=404,
            detail="This listing is no longer available.",
        )

    latitude = property_pin["latitude"]
    longitude = property_pin["longitude"]

    if latitude is None or longitude is None:
        return {
            "status": "missing_pin",
            "places": [],
        }

    api_key = os.getenv(
        "GOOGLE_PLACES_API_KEY", ""
    ).strip()

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Nearby places are not configured yet.",
        )

    return {
        "status": "ready",
        "places": find_places(
            latitude,
            longitude,
            category,
            api_key,
        ),
    }
