"""Staff-only intake for Digital Studio source footage and contact details."""

import json
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, ConfigDict, Field

from auth import require_admin, require_staff
from database import get_connection
from property_media import NO_CACHE, Version, bump_version, lock_draft

router = APIRouter(prefix="/staff/marketing", tags=["Digital Studio"])
VIDEO_LIMIT = 100 * 1024 * 1024
BUNDLED_BIN = Path(__file__).resolve().parent / "bin"


def use_bundled_tools(bin_dir=BUNDLED_BIN):
    """Put the build-installed ffprobe (backend/bin) first on PATH when present."""
    if (Path(bin_dir) / "ffprobe").is_file():
        entries = os.environ.get("PATH", "").split(os.pathsep)
        if str(bin_dir) not in entries:
            os.environ["PATH"] = os.pathsep.join([str(bin_dir), *filter(None, entries)])


use_bundled_tools()


class VerifiedContact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phone: str | None = Field(pattern=r"^\+[1-9][0-9]{7,14}$")


@router.put("/agents/{agent_id}/verified-contact", status_code=204)
def set_verified_contact(agent_id: UUID, payload: VerifiedContact, user=Depends(require_admin)):
    """Record a number the admin has confirmed with the agent, or revoke it."""
    with get_connection() as connection:
        row = connection.execute(
            """UPDATE public.users SET studio_phone = %s,
                      studio_phone_verified_at = CASE WHEN %s THEN clock_timestamp() END,
                      studio_phone_verified_by = CASE WHEN %s THEN %s::uuid END
               WHERE id = %s AND role IN ('agent', 'admin') RETURNING id;""",
            (payload.phone, payload.phone is not None, payload.phone is not None,
             user["id"], agent_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "Staff member not found.")
    return Response(status_code=204, headers=NO_CACHE)


@router.get("/studio/status")
def studio_status(user=Depends(require_admin)):
    """Readiness flags only: never returns the bucket name, endpoint or credentials."""
    return Response(
        content=json.dumps({
            "ffprobe": shutil.which("ffprobe") is not None,
            "storage_configured": bool(os.getenv("STUDIO_S3_BUCKET")),
        }),
        media_type="application/json", headers=NO_CACHE,
    )


def validate_video(path):
    """Probe a local MP4 with other FFmpeg demuxers and protocols disabled."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-protocol_whitelist", "file",
             "-f", "mov", "-enable_drefs", "0", "-show_streams", "-show_format",
             "-of", "json", str(path)],
            capture_output=True, check=True, timeout=15,
        )
        metadata = json.loads(result.stdout)
        duration = float(metadata["format"]["duration"])
    except FileNotFoundError:
        raise HTTPException(503, "Walkthrough inspection is unavailable.") from None
    except (subprocess.SubprocessError, ValueError, KeyError):
        raise HTTPException(422, "Choose a valid MP4 walkthrough.") from None
    if not (0 < duration <= 600) or not any(
        stream["codec_type"] == "video" and not stream.get("disposition", {}).get("attached_pic")
        for stream in metadata.get("streams", [])
    ):
        raise HTTPException(422, "Choose an MP4 video of up to 10 minutes.")


def save_walkthrough(property_id, version, user, path, size, bucket):
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError

    video_id = uuid4()
    with get_connection() as connection, connection.cursor() as cursor:
        lock_draft(cursor, property_id, version, user)
        cursor.execute(
            """INSERT INTO public.studio_walkthroughs (id, property_id, uploaded_by, bytes)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (property_id) DO UPDATE SET id = EXCLUDED.id,
                   uploaded_by = EXCLUDED.uploaded_by, bytes = EXCLUDED.bytes,
                   uploaded_at = clock_timestamp();""",
            (video_id, property_id, user["id"], size),
        )
        new_version = bump_version(cursor, property_id)
        try:
            storage = boto3.client(
                "s3", endpoint_url=os.getenv("STUDIO_S3_ENDPOINT_URL"),
                config=Config(connect_timeout=5, read_timeout=60, retries={"total_max_attempts": 1}),
            )
            with path.open("rb") as video:
                storage.put_object(
                    Bucket=bucket, Key=f"walkthroughs/{video_id}.mp4", Body=video,
                    ContentLength=size, ContentType="video/mp4",
                )
        except (BotoCoreError, ClientError):
            raise HTTPException(503, "Walkthrough storage is unavailable.") from None
    return {"id": video_id, "version": new_version}


def check_draft(property_id, version, user):
    with get_connection() as connection, connection.cursor() as cursor:
        lock_draft(cursor, property_id, version, user)


@router.put("/properties/{property_id}/walkthrough")
async def upload_walkthrough(
    property_id: UUID, version: Version, request: Request, response: Response,
    user=Depends(require_staff),
):
    bucket = os.getenv("STUDIO_S3_BUCKET")
    if not bucket:
        raise HTTPException(503, "Walkthrough storage is unavailable.")
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "video/mp4":
        raise HTTPException(415, "Send an MP4 video.")
    await run_in_threadpool(check_draft, property_id, version, user)
    size = 0
    with TemporaryDirectory() as directory:
        path = Path(directory) / "walkthrough.mp4"
        with path.open("wb") as video:
            async for chunk in request.stream():
                size += len(chunk)
                if size > VIDEO_LIMIT:
                    raise HTTPException(413, "Walkthroughs are limited to 100 MiB.")
                await run_in_threadpool(video.write, chunk)
        await run_in_threadpool(validate_video, path)
        result = await run_in_threadpool(save_walkthrough, property_id, version, user, path, size, bucket)
    response.headers.update(NO_CACHE)
    return result
