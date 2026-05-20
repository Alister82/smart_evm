"""
main.py – FastAPI application for Smart EVM Command Center.

Responsibilities:
  • Serve the web dashboard (Jinja2 templates)
  • Expose hardware API endpoints (Arduino polls /api/evm/poll every 2 s)
  • Manage the global EVM state machine
  • Enforce Genesis Mode bootstrapping
  • Enforce JWT / HTTPOnly cookie authentication on all dashboard routes
"""

import asyncio
import hashlib
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import (
    Depends, FastAPI, Form, HTTPException, Request, status
)
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.orm import Session
from sqlalchemy import func

from auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from models import (
    Admin,
    ArchivedElection,
    ArchivedVote,
    Constituency,
    Vote,
    Voter,
    WebUser,
    create_tables,
    get_db,
)

# ============================================================================
# Global EVM State Machine
# ============================================================================

VALID_STATES = {
    "IDLE",
    "GENESIS",           # Waiting for first hardware admin fingerprint
    "ENROLL_ADMIN",      # Waiting for new admin fingerprint (after AUTH_ADMIN)
    "AUTH_ADMIN",        # Waiting for existing admin to authorise a web action
    "ENROLL_VOTER",      # Waiting for new voter fingerprint
    "VOTING",            # Voting session active
    "POLL_CLOSED",       # Stopped accepting votes, results readable
}


class EVMState:
    """
    Thread-safe singleton that tracks what the Arduino should be doing.
    `payload` carries context needed by hardware (e.g. constituency_id).
    `pending_action` stores the web action to execute after AUTH_ADMIN.
    """

    def __init__(self):
        self._lock = asyncio.Lock()
        self.state: str   = "IDLE"
        self.payload: dict = {}
        self.pending_action: Optional[dict] = None   # action to run after auth

    async def set(self, state: str, payload: Optional[dict] = None, pending_action: Optional[dict] = None):
        async with self._lock:
            self.state          = state
            self.payload        = payload or {}
            self.pending_action = pending_action

    async def get(self) -> dict:
        # Grab the data inside the lock
        async with self._lock:
            current_state = self.state
            current_payload = self.payload
            
        # Return it outside the lock to keep the strict linter happy
        return {
            "state":   current_state,
            "payload": current_payload,
        }


evm = EVMState()


# ============================================================================
# Lifespan – initialise DB and Genesis Mode on startup
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    # Check if we need Genesis Mode
    from models import SessionLocal
    db = SessionLocal()
    try:
        has_admins    = db.query(Admin).first()   is not None
        has_web_users = db.query(WebUser).first() is not None
        if not has_admins:
            if has_web_users:
                await evm.set("ENROLL_ADMIN", payload={"role": "superadmin", "genesis": True})
            else:
                await evm.set("GENESIS")
    finally:
        db.close()
    yield


# ============================================================================
# App
# ============================================================================

app = FastAPI(title="Smart EVM Command Center", lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


# ============================================================================
# Helper: redirect unwanted requests gracefully
# ============================================================================

def redirect_to_login():
    return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)


def redirect_to_dashboard():
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)


# ============================================================================
# Root redirect
# ============================================================================

@app.get("/", include_in_schema=False)
async def root(request: Request):
    token = request.cookies.get("access_token")
    if token:
        return redirect_to_dashboard()
    return redirect_to_login()


# ============================================================================
# Auth Routes
# ============================================================================

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    token = request.cookies.get("access_token")
    admin_count = db.query(Admin).count()
    has_web_users = db.query(WebUser).first() is not None

    if admin_count == 0:
        desired_state = "ENROLL_ADMIN" if has_web_users else "GENESIS"
        payload = {"role": "superadmin", "genesis": True} if desired_state == "ENROLL_ADMIN" else None
        state_info = await evm.get()
        if state_info["state"] != desired_state:
            await evm.set(desired_state, payload=payload)

    if token:
        # Check if we have admins. If not, don't allow dashboard bypass
        if admin_count == 0:
            return RedirectResponse(url="/setup", status_code=status.HTTP_303_SEE_OTHER)
        return redirect_to_dashboard()

    if admin_count == 0:
        return RedirectResponse(url="/setup", status_code=status.HTTP_303_SEE_OTHER)

    return templates.TemplateResponse(request, "login.html", {"request": request, "error": None})


@app.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(WebUser).filter(WebUser.username == username).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html",
            {"request": request, "error": "Invalid username or password."},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )
    
    # If no admins exist yet, transition to ENROLL_ADMIN state so Arduino prompts for fingerprint
    admin_count = db.query(Admin).count()
    if admin_count == 0:
        await evm.set("ENROLL_ADMIN", payload={"role": "superadmin", "genesis": True})
    
    token = create_access_token({"sub": user.username})
    response = redirect_to_dashboard()
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,   # set True in production with HTTPS
    )
    return response


@app.get("/logout")
async def logout():
    response = redirect_to_login()
    response.delete_cookie("access_token")
    return response


# ============================================================================
# Genesis / Setup Route
# ============================================================================

@app.get("/setup", response_class=HTMLResponse)
async def setup_page(request: Request, db: Session = Depends(get_db)):
    admin_count = db.query(Admin).count()
    if admin_count > 0:
        return redirect_to_dashboard()

    has_web_users = db.query(WebUser).first() is not None
    state_info = await evm.get()
    if has_web_users and state_info["state"] != "ENROLL_ADMIN":
        await evm.set("ENROLL_ADMIN", payload={"role": "superadmin", "genesis": True})
        state_info = await evm.get()
    if state_info["state"] == "ENROLL_ADMIN":
         return templates.TemplateResponse(request, "setup.html", {"request": request, "error": None, "awaiting_fingerprint": True})
          
    return templates.TemplateResponse(request, "setup.html", {"request": request, "error": None})


@app.post("/setup", response_class=HTMLResponse)
async def setup_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    state_info = await evm.get()
    if state_info["state"] != "GENESIS":
        return redirect_to_dashboard()

    if not username.strip() or len(password) < 6:
        return templates.TemplateResponse(
            request, "setup.html",
            {"request": request, "error": "Username required and password must be ≥ 6 characters."},
        )
    existing = db.query(WebUser).filter(WebUser.username == username).first()
    if existing:
        if not verify_password(password, existing.password_hash):
            return templates.TemplateResponse(
                request, "setup.html",
                {"request": request, "error": "Username already exists and password incorrect."},
            )
        # Auth success, allow proceeding to hardware enrol
    else:
        web_user = WebUser(username=username, password_hash=hash_password(password))
        db.add(web_user)
        db.commit()

    # Transition: waiting for first hardware admin fingerprint
    await evm.set("ENROLL_ADMIN", payload={"role": "superadmin", "genesis": True})

    return templates.TemplateResponse(
        request, "setup.html",
        {
            "request": request,
            "error": None,
            "awaiting_fingerprint": True,
            "username": username,
        },
    )


# ============================================================================
# Dashboard Route
# ============================================================================

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    db: Session = Depends(get_db),
    current_user: WebUser = Depends(get_current_user),
):
    constituencies = db.query(Constituency).all()
    admins         = db.query(Admin).all()
    history        = db.query(ArchivedElection).order_by(ArchivedElection.id.desc()).all()
    state_info     = await evm.get()
    return templates.TemplateResponse(
        request, "dashboard.html",
        {
            "request":        request,
            "user":           current_user,
            "constituencies": constituencies,
            "admins":         admins,
            "history":        history,
            "evm_state":      state_info["state"],
            "evm_payload":    state_info["payload"],
        },
    )


# ============================================================================
# Dashboard Stats API (JS polls this for live updates)
# ============================================================================

@app.get("/api/dashboard/stats")
async def dashboard_stats(
    request: Request,
    db: Session = Depends(get_db),
    current_user: WebUser = Depends(get_current_user),
):
    state_info    = await evm.get()
    total_voters  = db.query(Voter).count()
    total_admins  = db.query(Admin).count()
    total_const   = db.query(Constituency).count()
    total_votes   = db.query(Vote).count()
    voted_count   = db.query(Voter).filter(Voter.has_voted == True).count()

    return JSONResponse({
        "state":               state_info["state"],
        "payload":             state_info["payload"],
        "total_voters":        total_voters,
        "total_admins":        total_admins,
        "total_constituencies": total_const,
        "total_votes":         total_votes,
        "voters_who_voted":    voted_count,
    })


# ============================================================================
# Constituency Management
# ============================================================================

@app.post("/constituency/add")
async def add_constituency(
    name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: WebUser = Depends(get_current_user),
):
    name = name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty.")
    existing = db.query(Constituency).filter(Constituency.name == name).first()
    if existing:
        raise HTTPException(status_code=409, detail="Constituency already exists.")
    db.add(Constituency(name=name))
    db.commit()
    return redirect_to_dashboard()


# ============================================================================
# Admin Management
# ============================================================================

@app.post("/admin/add")
async def trigger_add_admin(
    db: Session = Depends(get_db),
    current_user: WebUser = Depends(get_current_user),
):
    """
    Step 1: Request to add a new admin.
    Sets state → AUTH_ADMIN so an existing admin must scan to authorise.
    After auth succeeds, state → ENROLL_ADMIN to capture new admin's finger.
    """
    admin_count = db.query(Admin).count()
    if admin_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No admins registered yet. Complete Genesis Mode first.",
        )
    await evm.set(
        "AUTH_ADMIN",
        payload={"action": "add_admin"},
        pending_action={"type": "add_admin"},
    )
    return redirect_to_dashboard()


@app.post("/admin/delete/{admin_id}")
async def trigger_delete_admin(
    admin_id: int,
    db: Session = Depends(get_db),
    current_user: WebUser = Depends(get_current_user),
):
    """
    Step 1: Request to delete an admin.
    Requires physical admin auth first.
    """
    admin = db.query(Admin).filter(Admin.id == admin_id).first()
    if not admin:
        raise HTTPException(status_code=404, detail="Admin not found.")
    total = db.query(Admin).count()
    if total <= 1:
        raise HTTPException(status_code=400, detail="Cannot delete the last admin.")
    await evm.set(
        "AUTH_ADMIN",
        payload={"action": "delete_admin", "target_id": admin_id},
        pending_action={"type": "delete_admin", "target_id": admin_id},
    )
    return redirect_to_dashboard()


# ============================================================================
# Voter Enrollment
# ============================================================================

@app.post("/voter/enroll")
async def trigger_enroll_voter(
    constituency_id: int = Form(...),
    db: Session = Depends(get_db),
    current_user: WebUser = Depends(get_current_user),
):
    constituency = db.query(Constituency).filter(Constituency.id == constituency_id).first()
    if not constituency:
        raise HTTPException(status_code=404, detail="Constituency not found.")
    await evm.set(
        "ENROLL_VOTER",
        payload={"constituency_id": constituency_id, "constituency_name": constituency.name},
    )
    return redirect_to_dashboard()


# ============================================================================
# Hardware API – Arduino endpoints
# ============================================================================

# ---------- Poll (every 2 s from Arduino) -----------------------------------

@app.get("/api/evm/poll")
async def evm_poll(db: Session = Depends(get_db)):
    """Arduino calls this every 2 seconds to know its current instruction."""
    state_info = await evm.get()
    has_admins = db.query(Admin).first() is not None
    has_web_users = db.query(WebUser).first() is not None

    if not has_admins:
        desired_state = "ENROLL_ADMIN" if has_web_users else "GENESIS"
        payload = {"role": "superadmin", "genesis": True} if desired_state == "ENROLL_ADMIN" else {}
        if state_info["state"] != desired_state:
            await evm.set(desired_state, payload=payload)
        return await evm.get()

    if state_info["state"] == "GENESIS":
        await evm.set("IDLE")
        return await evm.get()
    return state_info


# ---------- Fingerprint dispatch --------------------------------------------

class FingerprintPayload(BaseModel):
    fingerprint_hash: str


@app.post("/api/evm/fingerprint")
async def evm_fingerprint(body: FingerprintPayload, db: Session = Depends(get_db)):
    """
    Central fingerprint handler. Behaviour depends on current state:
      GENESIS / ENROLL_ADMIN  → register new Admin row
      AUTH_ADMIN              → verify existing admin, then execute pending action
      ENROLL_VOTER            → register new Voter row
    """
    fp_hash    = body.fingerprint_hash.strip()
    state_info = await evm.get()
    current    = evm.state   # direct read (already locked above)

    # Re-fetch authoritative state
    async with evm._lock:
        current = evm.state
        payload = dict(evm.payload)
        pending = evm.pending_action

    # --- GENESIS / ENROLL_ADMIN ---
    if current in ("GENESIS", "ENROLL_ADMIN"):
        existing = db.query(Admin).filter(Admin.fingerprint_hash == fp_hash).first()
        if existing:
            return JSONResponse({"status": "error", "message": "Fingerprint already registered."}, status_code=409)
        role = payload.get("role", "admin")
        db.add(Admin(fingerprint_hash=fp_hash, role=role))
        db.commit()
        await evm.set("IDLE")
        return JSONResponse({"status": "ok", "message": "Admin enrolled."})

    # --- AUTH_ADMIN ---
    if current == "AUTH_ADMIN":
        admin = db.query(Admin).filter(Admin.fingerprint_hash == fp_hash).first()
        if not admin:
            return JSONResponse({"status": "error", "message": "Unrecognised fingerprint."}, status_code=403)

        # Execute the pending web action
        if pending and pending.get("type") == "add_admin":
            await evm.set("ENROLL_ADMIN", payload={"role": "admin"})
            return JSONResponse({"status": "ok", "message": "Authorised. Ready to enrol new admin."})

        if pending and pending.get("type") == "delete_admin":
            target = db.query(Admin).filter(Admin.id == pending["target_id"]).first()
            if target:
                db.delete(target)
                db.commit()
            await evm.set("IDLE")
            return JSONResponse({"status": "ok", "message": "Admin deleted."})

        await evm.set("IDLE")
        return JSONResponse({"status": "ok", "message": "Authorised."})

    # --- ENROLL_VOTER ---
    if current == "ENROLL_VOTER":
        constituency_id = payload.get("constituency_id")
        if not constituency_id:
            return JSONResponse({"status": "error", "message": "No constituency set."}, status_code=400)
        existing = db.query(Voter).filter(Voter.fingerprint_hash == fp_hash).first()
        if existing:
            return JSONResponse({"status": "error", "message": "Voter already enrolled."}, status_code=409)
        db.add(Voter(fingerprint_hash=fp_hash, constituency_id=constituency_id, has_voted=False))
        db.commit()
        await evm.set("IDLE")
        return JSONResponse({"status": "ok", "message": "Voter enrolled."})

    return JSONResponse({"status": "idle", "message": "No action pending."})


# ---------- Voter verification (before casting vote) ------------------------

class VoterVerifyPayload(BaseModel):
    fingerprint_hash: str
    constituency_id:  int


@app.post("/api/evm/verify_voter")
async def verify_voter(body: VoterVerifyPayload, db: Session = Depends(get_db)):
    """
    Arduino sends voter fingerprint + active constituency.
    Returns {status: "unlock"} only if all three conditions met:
      1. Fingerprint exists in Voters
      2. Voter's constituency matches EVM's active constituency
      3. has_voted == False
    """
    voter = db.query(Voter).filter(Voter.fingerprint_hash == body.fingerprint_hash).first()
    if not voter:
        return JSONResponse({"status": "reject", "message": "Not a registered voter.", "authorized": False})
    if voter.constituency_id != body.constituency_id:
        return JSONResponse({"status": "reject", "message": "Wrong constituency.", "authorized": False})
    if voter.has_voted:
        return JSONResponse({"status": "reject", "message": "Already voted.", "authorized": False})
    return JSONResponse({"status": "unlock", "authorized": True})


# ---------- Admin verification (EVM power-on unlock) -----------------------

class AdminVerifyPayload(BaseModel):
    fingerprint_hash: str


@app.post("/api/evm/verify_admin")
async def verify_admin(body: AdminVerifyPayload, db: Session = Depends(get_db)):
    """
    Called on EVM power-on. Returns unlock token if fingerprint matches an admin.
    """
    admin = db.query(Admin).filter(Admin.fingerprint_hash == body.fingerprint_hash).first()
    if not admin:
        return JSONResponse({"status": "reject", "message": "Unrecognised admin.", "authorized": False}, status_code=403)
    # Issue a short-lived hardware session token
    hw_token = create_access_token({"sub": f"hw_admin_{admin.id}", "role": admin.role}, )
    return JSONResponse({"status": "unlock", "token": hw_token, "admin_id": admin.id, "authorized": True})


# ---------- Cast Vote (Split for Strict Anonymity) ----------------------

class MarkVotedPayload(BaseModel):
    fingerprint_hash: str

@app.post("/api/evm/mark_voted")
async def mark_voted(body: MarkVotedPayload, db: Session = Depends(get_db)):
    """
    Request 1: Identity Check. 
    Finds the voter and marks them as having voted. NO ballot data here.
    """
    voter = db.query(Voter).filter(Voter.fingerprint_hash == body.fingerprint_hash).first()
    if not voter:
        return JSONResponse({"status": "error", "message": "Not a registered voter."}, status_code=403)
    if voter.has_voted:
        return JSONResponse({"status": "error", "message": "Already voted."}, status_code=409)

    voter.has_voted = True
    db.commit()
    return JSONResponse({"status": "ok", "message": "Voter marked as voted."})


class CastVotePayload(BaseModel):
    constituency_id:  int
    candidate_id:     int

@app.post("/api/evm/cast_vote")
async def cast_vote(body: CastVotePayload, db: Session = Depends(get_db)):
    """
    Request 2: The Anonymous Ballot.
    Records the vote. NO identity data (fingerprint) is present here.
    """
    try:
        vote = Vote(constituency_id=body.constituency_id, candidate_id=body.candidate_id)
        db.add(vote)
        db.commit()
    except Exception:
        db.rollback()
        return JSONResponse({"status": "error", "message": "Database error. Vote not cast."}, status_code=500)

    return JSONResponse({"status": "ok", "message": "Vote cast successfully."})


# ============================================================================
# Election Lifecycle
# ============================================================================

@app.post("/api/election/close")
async def close_election(
    request: Request,
    db: Session = Depends(get_db),
    current_user: WebUser = Depends(get_current_user),
):
    await evm.set("POLL_CLOSED")
    return redirect_to_dashboard()


@app.post("/api/election/start_new")
async def start_new_election(
    request: Request,
    election_name: str = Form(...),
    db: Session = Depends(get_db),
    current_user: WebUser = Depends(get_current_user),
):
    if not election_name.strip():
        raise HTTPException(status_code=400, detail="Election name required")
    
    try:
        # Create history record
        archived_election = ArchivedElection(name=election_name.strip())
        db.add(archived_election)
        db.flush()
        
        # Move votes to archive
        votes = db.query(Vote).all()
        for v in votes:
            db.add(ArchivedVote(
                election_id=archived_election.id,
                constituency_id=v.constituency_id,
                candidate_id=v.candidate_id
            ))
            
        # Clear active votes
        db.query(Vote).delete()
        
        # Reset voters
        db.query(Voter).update({"has_voted": False})
        
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to start new election: {str(e)}")

    await evm.set("IDLE")
    return redirect_to_dashboard()


@app.get("/api/evm/results")
async def get_evm_results(db: Session = Depends(get_db)):
    """
    Called by Arduino during POLL_CLOSED state to get active tallies.
    """
    results = db.query(Vote.candidate_id, func.count(Vote.id).label('v')).group_by(Vote.candidate_id).all()
    # Format: [{"c": 1, "v": 10}, ...]
    payload = [{"c": r[0], "v": r[1]} for r in results]
    return JSONResponse(payload)


@app.post("/api/admin/reset_database")
async def reset_database(
    request: Request,
    db: Session = Depends(get_db),
    current_user: WebUser = Depends(get_current_user),
):
    """
    Reset the entire database and recreate tables.
    Used after adding new Arduino code or schema changes.
    """
    try:
        from models import engine, Base
        
        # Close all connections
        db.close()
        
        # Drop all tables
        Base.metadata.drop_all(bind=engine)
        
        # Recreate all tables
        Base.metadata.create_all(bind=engine)
        
        # Reset EVM state
        await evm.set("IDLE", payload={})
        
        return JSONResponse({"status": "ok", "message": "Database reset successfully. Genesis mode enabled."})
    except Exception as e:
        return JSONResponse(
            {"status": "error", "message": f"Failed to reset database: {str(e)}"},
            status_code=500
        )


# ============================================================================
# Exception handlers – turn 303 exceptions into actual redirects
# ============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if exc.status_code == status.HTTP_303_SEE_OTHER:
        location = exc.headers.get("Location", "/login")
        return RedirectResponse(url=location, status_code=status.HTTP_303_SEE_OTHER)
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
