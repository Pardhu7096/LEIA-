"""The standalone Digital Studio site is served by the API at /studio/."""
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("JWT_SECRET", "isolated-studio-test-secret-never-use-in-production-123")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")


def test_studio_site_and_assets_are_served():
    import main

    client = TestClient(main.app)  # no lifespan: static files need no database
    page = client.get("/studio/")
    assert page.status_code == 200
    assert "text/html" in page.headers["content-type"]
    assert 'id="sign-in"' in page.text
    for asset, kind in (("studio.js", "javascript"), ("studio.css", "text/css")):
        response = client.get(f"/studio/{asset}")
        assert response.status_code == 200, asset
        assert kind in response.headers["content-type"]


def test_contact_form_reads_owner_from_draft_details():
    # /staff/properties returns summaries without created_by, so the Studio must
    # read the draft itself before saving the agent's verified phone.
    script = (Path(__file__).resolve().parents[1] / "studio_web" / "studio.js").read_text(encoding="utf-8")
    assert "dataset.agent" not in script
    assert "/staff/properties/${encodeURIComponent(draftId)}" in script


def test_bundled_ffprobe_directory_goes_first_on_path(tmp_path, monkeypatch):
    import studio_inputs

    monkeypatch.setenv("PATH", os.pathsep.join(["/usr/bin", "/bin"]))
    studio_inputs.use_bundled_tools(tmp_path)  # no ffprobe there: PATH unchanged
    assert os.environ["PATH"].split(os.pathsep)[0] == "/usr/bin"
    (tmp_path / "ffprobe").write_bytes(b"")
    studio_inputs.use_bundled_tools(tmp_path)
    studio_inputs.use_bundled_tools(tmp_path)  # idempotent
    entries = os.environ["PATH"].split(os.pathsep)
    assert entries[0] == str(tmp_path) and entries.count(str(tmp_path)) == 1


def test_real_ffprobe_rejects_a_non_video(tmp_path):
    # Runs in CI after scripts/render/install-ffprobe.sh puts the pinned binary on PATH.
    import shutil

    import pytest
    from fastapi import HTTPException

    import studio_inputs

    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe is not installed here")
    fake = tmp_path / "walkthrough.mp4"
    fake.write_bytes(b"not a video" * 100)
    with pytest.raises(HTTPException) as error:
        studio_inputs.validate_video(fake)
    assert error.value.status_code == 422
