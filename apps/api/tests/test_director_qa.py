import asyncio
from contextlib import asynccontextmanager
import json
import subprocess
import sys
from types import ModuleType
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.core.config import get_settings
from app.core.db import Base, SessionLocal, get_db
from app.core.errors import register_error_handlers
from app.director import review
from app.director.providers import ColabAIProvider, OpenAIProvider, Reply, get_brain_provider
from app.director.service import accept_copilot_proposal, create_copilot_proposal, plan_project_storyboard
from app.director.tools import COPILOT_TOOL_SPECS
from app.jobs.service import enqueue_job
from app.mcp import server
from app.models import AIRun, Asset, Job, QAReview, Scene
from app.projects.router import router
from app.projects.schemas import ProjectCreateRequest
from app.projects.service import create_project_with_initial_timeline, project_qa_analytics
from app.qa import consumer
from app.qa import service as qa
from app.qa.media import sample_proxy_frames
from app.qa.schemas import QAScores
from app.timeline.schemas import SceneAssetRefs
from app.timeline.service import get_latest_timeline, save_timeline_document


@pytest.fixture
def studio(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'studio.db'}")
    Base.metadata.create_all(engine)
    for key in ("openai_api_key", "gemini_api_key"):
        monkeypatch.setattr(get_settings(), key, "")
    with SessionLocal(bind=engine) as db:
        project = create_project_with_initial_timeline(
            db, ProjectCreateRequest(title="Test draft", topic="Solar energy")
        )
        yield db, project
    engine.dispose()


@pytest.fixture(scope="module")
def proxy_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("video") / "proxy.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=96x64:r=8:d=1",
            "-c:v",
            "mpeg4",
            "-threads",
            "1",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


def complete_clip(db, project, job, *, with_proxy=True):
    doc = get_latest_timeline(db, project.id)
    scene = doc.tracks.visual[0]
    refs = SceneAssetRefs()
    for kind in ("master", "proxy") if with_proxy else ("master",):
        asset = Asset(
            project_id=project.id, scene_id=scene.id, kind=kind, storage_key=f"{kind}/{job.id}", sync_state="synced"
        )
        db.add(asset)
        db.flush()
        setattr(refs, kind, asset.id)
    scene.status = "ready"
    scene.assets = refs
    job.result = {"asset_ids": refs.model_dump(exclude_none=True)}
    job.status = "succeeded"
    save_timeline_document(db, project, doc.revision, doc, author="ai")
    assert get_latest_timeline(db, project.id).tracks.visual[0].status == "qa_checking"
    assert db.get(Scene, scene.id).status == "qa_checking"


def render_job(db, project, job_type):
    scene = get_latest_timeline(db, project.id).tracks.visual[0]
    return enqueue_job(
        db,
        project_id=project.id,
        scene_id=scene.id,
        job_type=job_type,
        capability=job_type,
        payload={"seed": scene.seed},
    )


@pytest.mark.parametrize(
    ("job_type", "artifact_score", "expected"),
    [
        ("i2v_ltx25", 7.0, "needs_review"),
        ("i2v_wan22", 9.0, "ready"),
    ],
)
def test_four_frame_clip_pipeline(studio, proxy_video, monkeypatch, job_type, artifact_score, expected):
    db, project = studio
    frames = sample_proxy_frames(proxy_video)
    assert len(set(frames)) == 4
    assert all(image.startswith(b"\xff\xd8") for image in frames)
    provider = AsyncMock()
    provider.vision_score_clip.return_value = QAScores(
        narration_match=10,
        artifact_free=artifact_score,
        style_consistency=10,
        safe_zone=10,
        reason="Compared geometry and flicker in frames 1–4",
    )
    monkeypatch.setattr(qa, "get_brain_provider", lambda *a, **kw: provider)
    monkeypatch.setattr(qa, "read_media", AsyncMock(return_value=proxy_video))
    job = render_job(db, project, job_type)
    complete_clip(db, project, job)
    asyncio.run(qa.evaluate_completed_clip(db, job))
    assert provider.vision_score_clip.await_args.kwargs["frames"] == frames
    asyncio.run(qa.evaluate_completed_clip(db, job))
    assert provider.vision_score_clip.await_count == 1
    if expected == "needs_review":
        retry = db.query(Job).filter(Job.id != job.id).one()
        assert retry.depends_on == [job.id]
        assert get_latest_timeline(db, project.id).tracks.visual[0].status == "generating_clip"
        complete_clip(db, project, retry)
        asyncio.run(qa.evaluate_completed_clip(db, retry))
        assert db.query(Job).count() == 2
    assert qa.pending_clip_jobs(db).count() == 0
    assert get_latest_timeline(db, project.id).tracks.visual[0].status == expected
    assert db.get(Scene, job.scene_id).status == expected
    metrics = project_qa_analytics(db, project.id)["models"][qa.CLIP_MODELS[job_type]]
    assert metrics == (
        {"reviews": 2, "passed": 0, "pass_rate": 0.0, "retry_count": 1}
        if expected == "needs_review"
        else {"reviews": 1, "passed": 1, "pass_rate": 1.0, "retry_count": 0}
    )


def test_missing_proxy_requires_human_even_in_full_auto(studio):
    db, project = studio
    project.qa_policy = "full_auto"
    job = render_job(db, project, "i2v_ltx25")
    complete_clip(db, project, job, with_proxy=False)
    asyncio.run(qa.evaluate_completed_clip(db, job))
    assert db.get(QAReview, job.id).verdict == "needs_review"
    assert db.query(Job).count() == 1
    assert get_latest_timeline(db, project.id).tracks.visual[0].qa.score is None


def test_colab_ai_plans_without_keys(studio, monkeypatch):
    db, project = studio
    colab = ModuleType("google.colab")
    colab.ai = ModuleType("ai")
    plan = {
        "palette": [],
        "look": "warm",
        "negative": "",
        "hook": "Hello",
        "seo_title": "Test",
        "description": "",
        "hashtags": [],
        "scenes": [{"kind": "diagram", "narration": "Colab wrote this."}],
    }
    colab.ai.generate_text = lambda prompt: json.dumps(plan)
    monkeypatch.setitem(sys.modules, "google.colab", colab)
    assert isinstance(get_brain_provider("colab_ai"), ColabAIProvider)
    doc = asyncio.run(plan_project_storyboard(db, project, brain_mode="colab_ai"))
    assert doc.tracks.visual[0].narration == "Colab wrote this."
    assert db.query(AIRun).one().provider == "colab_ai"


def test_openai_key_routes_images_and_tools(studio, monkeypatch):
    db, project = studio
    monkeypatch.setattr(get_settings(), "openai_api_key", "test-only")
    provider = get_brain_provider("openai_api")
    assert isinstance(provider, OpenAIProvider)
    requests = []
    score = {"narration_match": 8, "artifact_free": 9, "style_consistency": 8, "safe_zone": 9, "reason": "Stable"}

    def respond(request):
        requests.append(json.loads(request.content))
        assert request.headers["authorization"] == "Bearer test-only"
        if "tools" in requests[-1]:
            msg = {"tool_calls": [{"function": {"name": "review_draft", "arguments": "{}"}}]}
        else:
            msg = {"content": json.dumps(score)}
        return httpx.Response(200, json={"choices": [{"message": msg}], "usage": {"prompt_tokens": 12}})

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    asyncio.run(
        provider.vision_score_clip(
            db,
            project_id=project.id,
            frames=[b"\xff\xd8x"] * 4,
            narration="Solar",
            style_look="warm",
            safe_zone="16x9_standard",
        )
    )
    assert len(requests[0]["messages"][0]["content"]) == 5
    _, calls = asyncio.run(
        provider.copilot_propose(db, project_id=project.id, timeline_summary={}, user_message="Review")
    )
    assert calls == [{"name": "review_draft", "args": {}}]
    assert db.query(AIRun).filter_by(provider="openai", input_tokens=12).count() == 2


def test_review_draft_copilot_and_mcp_are_read_only(studio, monkeypatch):
    db, project = studio
    revision = project.current_revision
    proposal = asyncio.run(create_copilot_proposal(db, project, message="Review the pacing and diagram density"))
    assert proposal.commands == []
    for dimension in ("Pacing", "Hook retention", "Diagram density"):
        assert dimension in proposal.assistant_message
    accept_copilot_proposal(
        db, project_id=project.id, proposal_id=proposal.proposal_id, expected_revision=revision, confirm_gpu_spend=False
    )
    assert project.current_revision == revision
    assert any(spec["name"] == "review_draft" for spec in COPILOT_TOOL_SPECS)
    monkeypatch.setattr(server, "SessionLocal", lambda: SessionLocal(bind=db.bind))
    mcp = server.create_mcp_server()

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    app = FastAPI(lifespan=lifespan)
    app.mount("/mcp", mcp.streamable_http_app())
    headers = {"Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
    with TestClient(app, base_url="http://localhost:8000", headers=headers) as client:
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        listing = client.post("/mcp/", json=request)
        assert listing.is_success and "review_draft" in listing.text
        result = client.post(
            "/mcp/",
            json={
                **request,
                "method": "tools/call",
                "params": {"name": "review_draft", "arguments": {"project_id": project.id}},
            },
        )
        assert result.is_success and "diagram_density" in result.text
    assert db.get(type(project), project.id).current_revision == revision


def test_analytics_endpoint_legacy_keyframes_and_empty_models(studio):
    db, project = studio
    asset = Asset(project_id=project.id, scene_id="s", kind="keyframe", storage_key="keyframe.png")
    db.add(asset)
    db.flush()
    for verdict, fix in [("auto_healing", "rewrite_prompt_and_reseed"), ("passed", None)]:
        db.add(
            QAReview(
                project_id=project.id,
                scene_id="s",
                asset_id=asset.id,
                verdict=verdict,
                fix_action=fix,
                reason="fixture",
                score=7,
                narration_match=7,
                artifact_free=7,
                style_consistency=7,
                safe_zone=7,
            )
        )
    db.commit()
    app = FastAPI()
    app.include_router(router)
    register_error_handlers(app)
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    response = client.get(f"/v1/projects/{project.id}/qa-analytics")
    assert response.status_code == 200
    models = response.json()["models"]
    assert models["qwen_image"] == {"reviews": 2, "passed": 1, "pass_rate": 0.5, "retry_count": 1}
    assert models["wan22_i2v"]["pass_rate"] is None
    other = create_project_with_initial_timeline(db, ProjectCreateRequest(title="Another video", topic="Tides"))
    assert all(
        stat["reviews"] == 0 for stat in client.get(f"/v1/projects/{other.id}/qa-analytics").json()["models"].values()
    )
    assert client.get("/v1/projects/absent/qa-analytics").status_code == 404


def test_router_lifespan_recovers_persisted_clip(studio, monkeypatch):
    db, project = studio
    job = render_job(db, project, "i2v_wan22")
    complete_clip(db, project, job, with_proxy=False)
    monkeypatch.setattr(consumer, "SessionLocal", lambda: SessionLocal(bind=db.bind))
    app = FastAPI()
    app.include_router(router)

    async def restart():
        async with app.router.lifespan_context(app):
            async with asyncio.timeout(5):
                while not db.get(QAReview, job.id):
                    db.rollback()
                    await asyncio.sleep(0.02)

    asyncio.run(restart())
    db.expire_all()
    assert db.get(Scene, job.scene_id).status == "needs_review"
    assert db.get(Job, job.id).lease_until is None


def test_draft_review_includes_every_scene_and_labels_sampled_images(studio, monkeypatch):
    db, project = studio
    doc = get_latest_timeline(db, project.id)
    scenes = doc.tracks.visual
    for scene in scenes:
        keyframe = Asset(project_id=project.id, scene_id=scene.id, kind="keyframe", storage_key=scene.id)
        db.add(keyframe)
        db.flush()
        scene.assets = SceneAssetRefs(keyframe=keyframe.id)
    provider = AsyncMock()
    provider.supports_images = True
    provider.run.return_value = Reply(
        json.dumps(
            {"pacing": "Vary cuts", "hook_retention": "Show an example", "diagram_density": "Let the chart breathe"}
        )
    )
    monkeypatch.setattr(review, "read_media", AsyncMock(return_value=b"image"))
    result = asyncio.run(review.review_draft(db, project, doc, provider))
    prompt, images = provider.run.await_args.args[-2:]
    assert all(scene.id in prompt and scene.narration in prompt for scene in scenes)
    assert len(images) == len(result["sampled_scenes"]) == 4
    assert result["sampled_scenes"][0] == scenes[0].id
    assert result["sampled_scenes"][-1] == scenes[-1].id
