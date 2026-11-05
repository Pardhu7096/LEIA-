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


def change_media(property_id, kind, version, user, content):
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """SELECT status, version FROM public.properties
                   WHERE id = %s AND (created_by = %s OR %s = 'admin')
                   FOR UPDATE;""",
                (property_id, user["id"], user["role"]),
            )
            property_row = cursor.fetchone()
            if property_row is None:
                raise HTTPException(404, "Property not found.")
            if property_row["status"] != "draft" or property_row["version"] != version:
                raise HTTPException(409, "This listing changed. Refresh the list and open the current draft.")
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
                       ON CONFLICT (property_id, kind) DO UPDATE
                       SET id = EXCLUDED.id, content = EXCLUDED.content;""",
                    (media_id, property_id, kind, content),
                )
            cursor.execute(
                """UPDATE public.properties SET version = version + 1,
                       updated_at = clock_timestamp() WHERE id = %s RETURNING version;""",
                (property_id,),
            )
            return {"id": media_id, "version": cursor.fetchone()["version"]}


@router.put("/staff/properties/{property_id}/media/{kind}")
async def upload_media(
    property_id: UUID, kind: MediaKind, version: Version, request: Request,
    response: Response, user=Depends(require_staff),
):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > LIMITS[kind]:
            raise HTTPException(413, "The image exceeds the upload size limit.")
        data.extend(chunk)
    content = await run_in_threadpool(prepare_image, data, kind)
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
