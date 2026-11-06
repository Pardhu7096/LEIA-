from io import BytesIO
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from PIL import Image, ImageOps, UnidentifiedImageError

from auth import require_staff
from database import get_connection

router = APIRouter(tags=["Property media"])
MediaKind = Literal["cover", "panorama"]
Version = Annotated[int, Query(ge=1, le=2147483647)]
LIMITS = {"cover": 5 * 1024 * 1024, "panorama": 12 * 1024 * 1024}
GALLERY_LIMIT = 60
NO_CACHE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def prepare_image(data, kind):
    """Decode and re-encode a single still image without uploaded metadata."""
    try:
        with Image.open(BytesIO(data), formats=["JPEG", "PNG", "WEBP"]) as source:
            if source.width * source.height > 34_000_000:
                raise HTTPException(422, "Image resolution is too large.")
            if getattr(source, "is_animated", False):
                raise HTTPException(422, "Choose a still image.")
            image = ImageOps.exif_transpose(source).convert("RGB")
            if kind == "panorama":
                if image.width != image.height * 2 or image.width < 1024:
                    raise HTTPException(422, "Use a 2:1 equirectangular panorama at least 1024 pixels wide.")
                image.thumbnail((4096, 2048))
            else:
                image.thumbnail((1600, 1600))
            output = BytesIO()
            image.save(output, format="JPEG", quality=88)
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise HTTPException(422, "Choose a valid JPEG, PNG or WebP image.") from None
    if output.tell() > LIMITS[kind]:
        raise HTTPException(413, "The processed image is too large.")
    return output.getvalue()


def lock_draft(cursor, property_id, version, user):
    row = cursor.execute(
        """SELECT status, version FROM public.properties
           WHERE id = %s AND (created_by = %s OR %s = 'admin') FOR UPDATE;""",
        (property_id, user["id"], user["role"]),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "Property not found.")
    if row["status"] != "draft" or row["version"] != version:
        raise HTTPException(409, "This listing changed. Refresh the list and open the current draft.")


def bump_version(cursor, property_id):
    return cursor.execute(
        """UPDATE public.properties SET version = version + 1,
           updated_at = clock_timestamp() WHERE id = %s RETURNING version;""",
        (property_id,),
    ).fetchone()["version"]


def change_media(property_id, kind, version, user, content):
    with get_connection() as connection, connection.cursor() as cursor:
        lock_draft(cursor, property_id, version, user)
        media_id = None
        if content is None:
            cursor.execute(
                "DELETE FROM public.property_media WHERE property_id = %s AND kind = %s RETURNING id;",
                (property_id, kind),
            )
            if cursor.fetchone() is None:
                raise HTTPException(404, "Media not found.")
        else:
            media_id = uuid4()
            cursor.execute(
                """INSERT INTO public.property_media (id, property_id, kind, content)
                   VALUES (%s, %s, %s, %s)
                   ON CONFLICT (property_id, kind) WHERE kind IN ('cover', 'panorama') DO UPDATE
                   SET id = EXCLUDED.id, content = EXCLUDED.content;""",
                (media_id, property_id, kind, content),
            )
        return {"id": media_id, "version": bump_version(cursor, property_id)}


async def read_image(request, kind):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > LIMITS[kind]:
            raise HTTPException(413, "The image exceeds the upload size limit.")
        data.extend(chunk)
    return await run_in_threadpool(prepare_image, data, kind)


@router.put("/staff/properties/{property_id}/media/{kind}")
async def upload_media(
    property_id: UUID, kind: MediaKind, version: Version, request: Request,
    response: Response, user=Depends(require_staff),
):
    content = await read_image(request, kind)
    response.headers.update(NO_CACHE)
    return await run_in_threadpool(change_media, property_id, kind, version, user, content)


@router.delete("/staff/properties/{property_id}/media/{kind}")
def remove_media(
    property_id: UUID, kind: MediaKind, version: Version, response: Response,
    user=Depends(require_staff),
):
    response.headers.update(NO_CACHE)
    return change_media(property_id, kind, version, user, None)


@router.get("/staff/properties/{property_id}/media/{kind}")
def staff_media(property_id: UUID, kind: MediaKind, user=Depends(require_staff)):
    with get_connection() as connection:
        row = connection.execute(
            """SELECT m.content FROM public.property_media AS m
               JOIN public.properties AS p ON p.id = m.property_id
               WHERE p.id = %s AND m.kind = %s AND (p.created_by = %s OR %s = 'admin');""",
            (property_id, kind, user["id"], user["role"]),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Media not found.", headers=NO_CACHE)
    return Response(row["content"], media_type="image/jpeg", headers=NO_CACHE)


@router.get("/property-media/{media_id}")
def public_media(media_id: UUID):
    with get_connection() as connection:
        row = connection.execute(
            """SELECT m.content FROM public.property_media AS m
               JOIN public.properties AS p ON p.id = m.property_id
               WHERE m.id = %s AND p.status = 'approved' AND p.availability = 'available';""",
            (media_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Media not found.", headers=NO_CACHE)
    return Response(row["content"], media_type="image/jpeg", headers=NO_CACHE)


@router.get("/property-media/{media_id}/tour", response_class=HTMLResponse)
def property_tour(media_id: UUID):
    with get_connection() as connection:
        row = connection.execute(
            """SELECT 1 FROM public.property_media AS m
               JOIN public.properties AS p ON p.id = m.property_id
               WHERE m.id = %s AND m.kind = 'panorama'
                 AND p.status = 'approved' AND p.availability = 'available';""",
            (media_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Tour not found.", headers=NO_CACHE)
    # The only interpolated value has already been parsed as a UUID.
    return HTMLResponse(
        f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LEIA property tour</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/pannellum@2.5.7/build/pannellum.css">
<style>body {{margin:0}} #tour {{height:100dvh}}</style>
<div id="tour" aria-label="360-degree property tour">Loading property tour…</div>
<script src="https://cdn.jsdelivr.net/npm/pannellum@2.5.7/build/pannellum.js"></script>
<script>
  if (window.pannellum) {{
    document.getElementById('tour').textContent = '';
    pannellum.viewer('tour', {{
      type: 'equirectangular', panorama: '../{media_id}', autoLoad: true
    }});
  }} else {{
    document.getElementById('tour').textContent = 'The tour viewer could not load. Please try again.';
  }}
</script>
</html>""",
        headers={
            **NO_CACHE,
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                "default-src 'none'; script-src https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: blob:; "
                "connect-src 'self'; base-uri 'none'; form-action 'none'"
            ),
        },
    )


def gallery_entries(property_id, user=None):
    with get_connection() as connection:
        if user is None:
            allowed = connection.execute(
                """SELECT 1 FROM public.properties
                   WHERE id = %s AND status = 'approved' AND availability = 'available'
                   FOR SHARE;""",
                (property_id,),
            ).fetchone()
        else:
            allowed = connection.execute(
                "SELECT 1 FROM public.properties WHERE id = %s AND (created_by = %s OR %s = 'admin');",
                (property_id, user["id"], user["role"]),
            ).fetchone()
        if allowed is None:
            raise HTTPException(404, "Property not found.", headers=NO_CACHE)
        photos = connection.execute(
            """SELECT id, room, is_gallery_cover FROM public.property_media
               WHERE property_id = %s AND kind = 'photo' ORDER BY uploaded_at, id;""",
            (property_id,),
        ).fetchall()
    return photos


@router.get("/properties/{property_id}/gallery")
def public_gallery(property_id: UUID, response: Response):
    response.headers.update(NO_CACHE)
    return [
        {"id": photo["id"], "room": photo["room"], "image": f"/property-media/{photo['id']}"}
        for photo in gallery_entries(property_id)
    ]


@router.get("/staff/properties/{property_id}/gallery")
def staff_gallery(property_id: UUID, response: Response, user=Depends(require_staff)):
    response.headers.update(NO_CACHE)
    return gallery_entries(property_id, user)


@router.get("/staff/properties/{property_id}/gallery/{photo_id}")
def staff_gallery_photo(property_id: UUID, photo_id: UUID, user=Depends(require_staff)):
    with get_connection() as connection:
        row = connection.execute(
            """SELECT m.content FROM public.property_media AS m
               JOIN public.properties AS p ON p.id = m.property_id
               WHERE p.id = %s AND m.id = %s AND m.kind = 'photo'
                 AND (p.created_by = %s OR %s = 'admin');""",
            (property_id, photo_id, user["id"], user["role"]),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Photo not found.", headers=NO_CACHE)
    return Response(row["content"], media_type="image/jpeg", headers=NO_CACHE)


def add_gallery_photo(property_id, room, version, user, content):
    with get_connection() as connection, connection.cursor() as cursor:
        lock_draft(cursor, property_id, version, user)
        photos = cursor.execute(
            "SELECT room, is_gallery_cover FROM public.property_media WHERE property_id = %s AND kind = 'photo';",
            (property_id,),
        ).fetchall()
        if len(photos) >= GALLERY_LIMIT:
            raise HTTPException(422, f"Each property can have up to {GALLERY_LIMIT} gallery photos.")
        room = next((photo["room"] for photo in photos if photo["room"].casefold() == room.casefold()), room)
        photo_id = uuid4()
        cursor.execute(
            """INSERT INTO public.property_media (id, property_id, kind, room, content, is_gallery_cover)
               VALUES (%s, %s, 'photo', %s, %s, %s);""",
            (photo_id, property_id, room, content, not photos),
        )
        return {"id": photo_id, "version": bump_version(cursor, property_id)}


@router.post("/staff/properties/{property_id}/gallery", status_code=201)
async def upload_gallery_photo(
    property_id: UUID, room: Annotated[str, Query(max_length=60)], version: Version,
    request: Request, response: Response, user=Depends(require_staff),
):
    room = " ".join(room.split())
    if not room:
        raise HTTPException(422, "Enter a room or area name.")
    content = await read_image(request, "cover")
    response.headers.update(NO_CACHE)
    return await run_in_threadpool(add_gallery_photo, property_id, room, version, user, content)


@router.put("/staff/properties/{property_id}/gallery/{photo_id}/cover")
def set_gallery_cover(property_id: UUID, photo_id: UUID, version: Version, user=Depends(require_staff)):
    with get_connection() as connection, connection.cursor() as cursor:
        lock_draft(cursor, property_id, version, user)
        cursor.execute(
            "UPDATE public.property_media SET is_gallery_cover = FALSE WHERE property_id = %s AND is_gallery_cover;",
            (property_id,),
        )
        cursor.execute(
            """UPDATE public.property_media SET is_gallery_cover = TRUE
               WHERE property_id = %s AND id = %s AND kind = 'photo';""",
            (property_id, photo_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "Photo not found.")
        return {"version": bump_version(cursor, property_id)}


@router.delete("/staff/properties/{property_id}/gallery/{photo_id}")
def remove_gallery_photo(property_id: UUID, photo_id: UUID, version: Version, user=Depends(require_staff)):
    with get_connection() as connection, connection.cursor() as cursor:
        lock_draft(cursor, property_id, version, user)
        removed = cursor.execute(
            """DELETE FROM public.property_media WHERE property_id = %s AND id = %s
               AND kind = 'photo' RETURNING is_gallery_cover;""",
            (property_id, photo_id),
        ).fetchone()
        if removed is None:
            raise HTTPException(404, "Photo not found.")
        if removed["is_gallery_cover"]:
            cursor.execute(
                """UPDATE public.property_media SET is_gallery_cover = TRUE WHERE id = (
                       SELECT id FROM public.property_media WHERE property_id = %s AND kind = 'photo'
                       ORDER BY uploaded_at, id LIMIT 1
                   );""",
                (property_id,),
            )
        return {"version": bump_version(cursor, property_id)}
