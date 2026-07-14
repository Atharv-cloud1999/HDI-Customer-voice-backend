"""
FastAPI wrapper around the existing GROWW Review Pulse pipeline.

This does NOT change any pipeline logic — it just calls the same functions
that app.py (Streamlit) and main.py (CLI) already call, and exposes them
over HTTP so the Lovable frontend can drive them instead.

Endpoints:
  POST /generate            -> starts the pipeline, returns {job_id}
  GET  /status/{job_id}      -> returns current phase
  GET  /pulse/{job_id}       -> returns the structured pulse once done
  GET  /email-draft/{job_id} -> returns a downloadable .eml draft (dry run)
  POST /send-email           -> actually sends the email via SMTP
"""

import os
import re
import uuid
import threading
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

from ingest_reviews import fetch_and_save_reviews
from process_reviews import ReviewProcessor
from generate_pulse import PulseGenerator
from generate_email import EmailGenerator

load_dotenv(override=True)

app = FastAPI(title="GROWW Review Pulse API")

# NOTE: allow_origins=["*"] is fine to get things working quickly.
# Once your Lovable/Vercel URL is final, replace "*" with that exact URL
# for better security (e.g. ["https://your-app.lovable.app"]).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# In-memory job store. Fine for a single-user / low-traffic tool like this.
# Jobs disappear if the server restarts — that's expected and OK here.
jobs: dict[str, dict] = {}


class GenerateRequest(BaseModel):
    weeks: int = 10
    max_reviews: int = 500


class SendEmailRequest(BaseModel):
    job_id: str
    recipient_email: str
    recipient_name: Optional[str] = "Team"


def run_pipeline(job_id: str, weeks: int, max_reviews: int):
    try:
        jobs[job_id]["phase"] = "ingesting"
        fetch_and_save_reviews(
            app_id="com.nextbillion.groww",
            weeks_requested=weeks,
            max_count=max_reviews,
        )

        jobs[job_id]["phase"] = "analyzing"
        processor = ReviewProcessor()
        processor.run()

        jobs[job_id]["phase"] = "generating"
        generator = PulseGenerator()
        pulse_path = generator.run()

        if not pulse_path:
            raise RuntimeError(
                "Pulse generation returned nothing — check that reviews were "
                "fetched and classified successfully before this step."
            )

        jobs[job_id]["pulse_path"] = pulse_path
        jobs[job_id]["phase"] = "done"

    except Exception as e:
        jobs[job_id]["phase"] = "error"
        jobs[job_id]["error"] = str(e)


@app.post("/generate")
def generate(req: GenerateRequest):
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"phase": "queued", "weeks": req.weeks, "max_reviews": req.max_reviews}
    thread = threading.Thread(
        target=run_pipeline, args=(job_id, req.weeks, req.max_reviews), daemon=True
    )
    thread.start()
    return {"job_id": job_id}


@app.get("/status/{job_id}")
def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {"job_id": job_id, "phase": job["phase"], "error": job.get("error")}


def parse_pulse_markdown(md_content: str) -> dict:
    """Turns the LLM-generated markdown pulse into the JSON shape the frontend expects."""

    themes = []
    top_themes_match = re.search(r"##\s*Top Themes\s*(.*?)(?=\n##|\Z)", md_content, re.DOTALL)
    if top_themes_match:
        block = top_themes_match.group(1)
        theme_chunks = re.split(r"\n(?=\d+\.\s)", block.strip())
        accents = ["ember", "gold", "ember-dark"]
        for i, chunk in enumerate(theme_chunks):
            header_match = re.match(r"\d+\.\s*(.+?)\s*\((\d+)\s*mentions?\)", chunk.strip())
            if not header_match:
                continue
            label = header_match.group(1).strip()
            mentions = int(header_match.group(2))
            points = [p.strip() for p in re.findall(r"-\s*(.+)", chunk)]
            themes.append(
                {
                    "id": i + 1,
                    "label": label,
                    "mentions": mentions,
                    "accent": accents[i % len(accents)],
                    "points": points,
                }
            )

    quotes = []
    quotes_match = re.search(r"##\s*What do users say\s*(.*?)(?=\n##|\Z)", md_content, re.DOTALL)
    if quotes_match:
        block = quotes_match.group(1)
        for m in re.finditer(r'"([^"]+)"\s*—\s*(\d)', block):
            quotes.append({"text": m.group(1).strip(), "rating": int(m.group(2))})

    action_idea = ""
    action_match = re.search(r"##\s*Action Ideas\s*(.*?)(?=\n##|\Z)", md_content, re.DOTALL)
    if action_match:
        ideas = re.findall(r"\d+\.\s*(.+)", action_match.group(1))
        action_idea = " ".join(i.strip() for i in ideas)

    title_match = re.search(r"#\s*GROWW Weekly Review Pulse\s*--\s*Week of (.+)", md_content)
    week_of = title_match.group(1).strip() if title_match else ""

    return {"weekOf": week_of, "themes": themes, "quotes": quotes, "actionIdea": action_idea}


@app.get("/pulse/{job_id}")
def get_pulse(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job["phase"] != "done":
        raise HTTPException(status_code=409, detail=f"Job is not finished yet (phase: {job['phase']})")

    with open(job["pulse_path"], "r", encoding="utf-8") as f:
        md_content = f.read()

    parsed = parse_pulse_markdown(md_content)
    parsed["weeksRequested"] = job["weeks"]
    parsed["maxReviews"] = job["max_reviews"]
    return parsed


@app.get("/email-draft/{job_id}")
def email_draft(job_id: str, recipient_name: Optional[str] = "Team"):
    job = jobs.get(job_id)
    if not job or job["phase"] != "done":
        raise HTTPException(status_code=409, detail="Pulse is not ready for this job yet")

    try:
        email_gen = EmailGenerator(recipient_name=recipient_name, dry_run=True)
        email_gen.run()
        eml_path = os.path.join("data", "phase5", "draft_email.eml")
        with open(eml_path, "r", encoding="utf-8") as f:
            eml_content = f.read()
        return {"eml": eml_content}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/send-email")
def send_email(req: SendEmailRequest):
    job = jobs.get(req.job_id)
    if not job or job["phase"] != "done":
        raise HTTPException(status_code=409, detail="Pulse is not ready for this job yet")

    try:
        email_gen = EmailGenerator(
            recipient_email=req.recipient_email,
            recipient_name=req.recipient_name,
            dry_run=False,
        )
        email_gen.run()
        return {"status": "sent", "recipient": req.recipient_email}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/")
def root():
    return {"status": "ok", "service": "GROWW Review Pulse API"}
