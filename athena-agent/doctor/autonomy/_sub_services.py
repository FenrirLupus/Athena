"""Services test — runtime guarantees doctor-weekly + curator-daily at boot."""
from __future__ import annotations

import tempfile
from pathlib import Path


def run() -> list[dict]:
    from autonomy import scheduler
    from autonomy.scheduler import list_jobs, ensure_services, SERVICES

    checks = []
    original_db = scheduler.SCHEDULER_DB
    with tempfile.TemporaryDirectory() as td:
        scheduler.SCHEDULER_DB = Path(td) / "scheduler.db"
        try:
            # First boot registers all standing services.
            added = ensure_services()
            names = {j["name"] for j in list_jobs()}
            checks.append({
                "name": "boot registers standing services",
                "status": "ok" if {"doctor", "curator", "restart", "backup"} <= names else "fail",
                "detail": f"added={added}",
            })
            # Services carry TYPE labels: doctor=hourly, custodian=hourly,
            # curator=daily, restart=daily.
            types = {j["name"]: j.get("type", "") for j in list_jobs()}
            checks.append({
                "name": "service types labeled",
                "status": "ok" if types.get("doctor") == "hourly"
                and types.get("custodian") == "hourly"
                and types.get("curator") == "daily"
                and types.get("restart") == "daily" else "fail",
                "detail": f"{types}",
            })
            # Schedules: doctor + custodian hourly (the Operator's 08-12 spec:
            # the FREE tiers run at boot + every hour), curator daily.
            scheds = {j["name"]: j.get("schedule", "") for j in list_jobs()}
            checks.append({
                "name": "doctor hourly schedule (free tier)",
                "status": "ok" if scheds.get("doctor") == "17 * * * *" else "fail",
                "detail": scheds.get("doctor", "?"),
            })
            checks.append({
                "name": "custodian hourly schedule (free tier)",
                "status": "ok" if scheds.get("custodian") == "27 * * * *" else "fail",
                "detail": scheds.get("custodian", "?"),
            })
            checks.append({
                "name": "curator daily schedule",
                "status": "ok" if scheds.get("curator") == "0 3 * * *" else "fail",
                "detail": scheds.get("curator", "?"),
            })
            # Custom H/M/S interval type works.
            r = scheduler.add_job("custom-test-job", "every 2h 30m", "backup",
                                  job_type="custom")
            row = [j for j in list_jobs() if j["name"] == "custom-test-job"][0]
            checks.append({
                "name": "custom H/M/S interval type",
                "status": "ok" if r["type"] == "custom" and row.get("type") == "custom"
                and row.get("schedule") == "every 2h 30m" else "fail",
                "detail": f"type={row.get('type')} schedule={row.get('schedule')}",
            })
            scheduler.remove_job(r["id"])
            # Idempotent: a second boot adds nothing.
            added2 = ensure_services()
            count = len(list_jobs())
            checks.append({
                "name": "idempotent boot (no duplicates)",
                "status": "ok" if added2 == [] and count == len(SERVICES) else "fail",
                "detail": f"added2={added2} jobs={count}",
            })
            # The ServerLoop calls ensure_services on construction — it must
            # run without error and (on a warm DB) add nothing new.
            import core.server_loop as sl_mod
            loop = sl_mod.ServerLoop(runtime=None, config={"server": {"tick_interval_s": 1}})
            checks.append({
                "name": "server loop ensures services on boot",
                "status": "ok" if isinstance(loop.services_started, list) else "fail",
                "detail": f"services_started={loop.services_started} (0 = already present)",
            })
        finally:
            scheduler.SCHEDULER_DB = original_db
    return checks
