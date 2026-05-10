from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hive.manager import SessionManager
from hive.session import Session
from hive.watcher import StateWatcher


class CreateSessionRequest(BaseModel):
    project_path: str
    name: str


def session_to_dict(s: Session) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "project_path": str(s.project_path),
        "state": s.state.value,
        "claude_session_id": s.claude_session_id,
        "created_at": s.created_at.isoformat(),
    }


def build_router(manager: SessionManager, watcher: StateWatcher) -> APIRouter:
    router = APIRouter()

    @router.get("/sessions")
    def list_sessions():
        return {"sessions": [session_to_dict(s) for s in manager.list()]}

    @router.post("/sessions", status_code=201)
    def create_session(req: CreateSessionRequest):
        project = Path(req.project_path)
        if not project.is_dir():
            raise HTTPException(
                status_code=400, detail=f"not a directory: {project}"
            )
        try:
            session = manager.spawn(project_path=project, name=req.name)
        except ValueError as e:
            raise HTTPException(status_code=409, detail=str(e))
        except FileNotFoundError as e:
            raise HTTPException(
                status_code=400,
                detail=f"claude binary not found: {e}",
            )
        manager.save_state()
        return session_to_dict(session)

    @router.delete("/sessions/{session_id}", status_code=204)
    def delete_session(session_id: str):
        s = manager.get(session_id)
        if s is None:
            raise HTTPException(status_code=404, detail="unknown session")
        watcher.unbind(session_id)
        manager.kill(session_id)
        manager.save_state()
        return None

    @router.get("/healthz")
    def healthz():
        return {"sessions": len(manager.list()), "ok": True}

    @router.get("/projects")
    def list_projects():
        projects = []
        if manager.projects_root.exists():
            for entry in sorted(manager.projects_root.iterdir()):
                if entry.is_dir():
                    projects.append({
                        "name": entry.name,
                        "path": str(entry),
                    })
        return {"projects": projects}

    return router
