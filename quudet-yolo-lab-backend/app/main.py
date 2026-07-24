from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import admin, auth, dashboard, datasets, dispatch, experiments, health, jobs, nodes, options, provisioning, resources, uploads
from app.bootstrap import ensure_guest_user, ensure_superuser
from app.config import get_settings
from app.database import SessionLocal, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ = app
    init_db()
    db = SessionLocal()
    try:
        ensure_superuser(db)
        if get_settings().DISABLE_AUTH:
            ensure_guest_user(db)

        # Run reconciliation once on startup to recover any orphaned jobs
        # from a previous crash or restart.
        from app.services.reconciliation import reconcile_all
        rec_results = reconcile_all(db)
        total_fixed = sum(
            r.get("jobs_marked_failed", 0) + r.get("groups_recalculated", 0)
            for r in rec_results
        )
        if total_fixed:
            print(f"[startup] reconciliation fixed {total_fixed} stuck items: {rec_results}")
        else:
            print("[startup] reconciliation — nothing to fix")
    finally:
        db.close()
    yield


settings = get_settings()

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_list(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router, prefix=settings.API_V1_PREFIX)
app.include_router(datasets.router, prefix=settings.API_V1_PREFIX)
app.include_router(jobs.router, prefix=settings.API_V1_PREFIX)
app.include_router(dashboard.router, prefix=settings.API_V1_PREFIX)
app.include_router(options.router, prefix=settings.API_V1_PREFIX)
app.include_router(uploads.router, prefix=settings.API_V1_PREFIX)
app.include_router(nodes.router, prefix=settings.API_V1_PREFIX)
app.include_router(dispatch.router, prefix=settings.API_V1_PREFIX)
app.include_router(experiments.router, prefix=settings.API_V1_PREFIX)
app.include_router(admin.router, prefix=settings.API_V1_PREFIX)
app.include_router(resources.router, prefix=settings.API_V1_PREFIX)
app.include_router(provisioning.router, prefix=settings.API_V1_PREFIX)


@app.get(f"{settings.API_V1_PREFIX}/version")
def version():
    return {"version": "0.1.0", "name": settings.PROJECT_NAME}
