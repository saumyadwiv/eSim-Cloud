import asyncio
import os
import json
from fastapi.middleware.cors import CORSMiddleware
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from contextlib import asynccontextmanager
from session_store import (
    save_session, get_session,
    delete_session, refresh_session,
    get_all_sessions
)
from k8s_client import (
    create_simulation_pod,
    delete_simulation_pod,
    get_pod_status
)

# ✅ FIX: Top-level import HATAYA — ab sirf lifespan ke andar se import hoga
@asynccontextmanager
async def lifespan(app: FastAPI):
    from cleanup import run_cleanup_loop  # ✅ Andar ka import rakha
    task = asyncio.create_task(run_cleanup_loop())
    print("--- CLEANUP LOOP STARTED IN BACKGROUND ---")
    yield
    task.cancel()
    print("--- CLEANUP LOOP STOPPED ---")


app = FastAPI(title="eSim Session Manager", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartSessionRequest(BaseModel):
    user_id: str
    namespace: str = "default"


class SessionResponse(BaseModel):
    user_id: str
    pod_name: str
    namespace: str
    status: str


@app.post("/session/start", response_model=SessionResponse)
async def start_session(request: StartSessionRequest):
    print(f"DEBUG: Checking existing session for {request.user_id}")
    existing = get_session(request.user_id)
    if existing:
        print(f"DEBUG: Existing session found: {existing['pod_name']}")
        status = get_pod_status(existing["pod_name"], existing["namespace"])
        return SessionResponse(
            user_id=request.user_id,
            pod_name=existing["pod_name"],
            namespace=existing["namespace"],
            status=status or "Unknown"
        )

    try:
        print(f"DEBUG: Creating pod for {request.user_id}")
        pod_name = create_simulation_pod(
            user_id=request.user_id,
            namespace=request.namespace
        )
        print(f"DEBUG: Pod created successfully: {pod_name}")
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to create pod: {str(e)}")
        raise HTTPException(status_code=500, detail=f"K8s Pod Creation Failed: {str(e)}")

    try:
        print(f"DEBUG: Saving session for {request.user_id} -> {pod_name}")
        save_session(
            user_id=request.user_id,
            pod_name=pod_name,
            namespace=request.namespace
        )
        if get_session(request.user_id):
            print(f"DEBUG: SUCCESS! Session saved in Redis for {request.user_id}")
        else:
            print(f"DEBUG: FAILED! Session NOT saved in Redis for {request.user_id}")
    except Exception as e:
        print(f"CRITICAL ERROR: Failed to save to Redis: {str(e)}")
        delete_simulation_pod(pod_name, request.namespace)
        raise HTTPException(status_code=500, detail="Failed to save session state")

    return SessionResponse(
        user_id=request.user_id,
        pod_name=pod_name,
        namespace=request.namespace,
        status="Creating"
    )


@app.get("/session/{user_id}", response_model=SessionResponse)
async def get_session_status(user_id: str):
    print(f"DEBUG: Fetching session status for {user_id}")
    session = get_session(user_id)
    
    if not session:
        # ✅ Session Redis mein nahi — already expired/terminated
        raise HTTPException(
            status_code=404,
            detail="Session expired or terminated"
        )

    status = get_pod_status(session["pod_name"], session["namespace"])

    # ✅ Pod Terminated hai — Redis bhi clean karo
    if status == "Terminated":
        print(f"Pod terminated, cleaning Redis for {user_id}")
        delete_session(user_id)
        raise HTTPException(
            status_code=404,
            detail="Session terminated"
        )

    refresh_session(user_id)  # Sirf tab refresh karo jab pod alive ho
    
    return SessionResponse(
        user_id=user_id,
        pod_name=session["pod_name"],
        namespace=session["namespace"],
        status=status  # "Terminated" frontend ko jayega
    )

@app.delete("/session/{user_id}")
async def end_session(user_id: str):
    session = get_session(user_id)
    if not session:
        raise HTTPException(status_code=404, detail="No active session found")

    delete_simulation_pod(session["pod_name"], session["namespace"])
    delete_session(user_id)
    return {"message": f"Session ended for user {user_id}"}


@app.get("/health")
async def health_check():
    return {"status": "healthy"}


@app.get("/sessions/all")
async def list_all_sessions():
    sessions = get_all_sessions()
    return {"active_sessions": len(sessions), "sessions": sessions}