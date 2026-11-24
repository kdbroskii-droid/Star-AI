from __future__ import annotations

import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

load_dotenv()

APP_NAME = "Star-AI English Library"
DB_PATH = Path(os.getenv("DATABASE_PATH", "data/english_library.db"))
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
MAX_SOURCE_ITEMS = int(os.getenv("MAX_SOURCE_ITEMS", "10"))
WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]{1,48}")
PHRASE_RE = re.compile(r"[^.!?\n]{3,100}")

app = FastAPI(
    title=APP_NAME,
    description="A cloud-ready English word, phrase and language-learning API.",
    version="0.1.0",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def initialise_database() -> None:
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS words (
            word TEXT PRIMARY KEY COLLATE NOCASE,
            definition TEXT NOT NULL DEFAULT '',
            phonetic TEXT NOT NULL DEFAULT '',
            part_of_speech TEXT NOT NULL DEFAULT '',
            example TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'discovered',
            confidence REAL NOT NULL DEFAULT 0.5,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS phrases (
            phrase TEXT PRIMARY KEY COLLATE NOCASE,
            meaning TEXT NOT NULL DEFAULT '',
            example TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'discovered',
            confidence REAL NOT NULL DEFAULT 0.5,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            occurrences INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS learning_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            url TEXT NOT NULL DEFAULT '',
            added_at TEXT NOT NULL,
            words_found INTEGER NOT NULL DEFAULT 0,
            phrases_found INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_words_last_seen ON words(last_seen);
        CREATE INDEX IF NOT EXISTS idx_phrases_last_seen ON phrases(last_seen);
        """)


@app.on_event("startup")
def startup() -> None:
    initialise_database()


def require_admin(x_api_key: str | None = Header(default=None)) -> None:
    if ADMIN_API_KEY and x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="A valid X-API-Key header is required.")


class LearnText(BaseModel):
    text: str = Field(min_length=3, max_length=100_000)
    source: str = Field(default="manual", max_length=40)
    title: str = Field(default="", max_length=300)
    url: str = Field(default="", max_length=2_000)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=200)
    limit: int = Field(default=5, ge=1, le=10)


def extract_candidates(text: str) -> tuple[list[str], list[str]]:
    words = list(dict.fromkeys(w.lower().replace("’", "'") for w in WORD_RE.findall(text)))
    phrases: list[str] = []
    for sentence in PHRASE_RE.findall(text):
        cleaned = re.sub(r"\s+", " ", sentence).strip(" ,:;-'\"")
        if 2 <= len(cleaned.split()) <= 8 and len(cleaned) <= 100:
            phrases.append(cleaned.lower())
    return words, list(dict.fromkeys(phrases))


def store_learning(text: str, source: str, title: str = "", url: str = "") -> dict[str, int]:
    words, phrases = extract_candidates(text)
    now = utc_now()
    with db() as conn:
        for word in words:
            conn.execute("""
                INSERT INTO words(word, source, first_seen, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(word) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    occurrences=occurrences+1
            """, (word, source, now, now))
        for phrase in phrases:
            conn.execute("""
                INSERT INTO phrases(phrase, source, first_seen, last_seen)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(phrase) DO UPDATE SET
                    last_seen=excluded.last_seen,
                    occurrences=occurrences+1
            """, (phrase, source, now, now))
        conn.execute("""
            INSERT INTO learning_sources(provider, title, url, added_at, words_found, phrases_found)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (source, title, url, now, len(words), len(phrases)))
    return {"words_found": len(words), "phrases_found": len(phrases)}


async def fetch_dictionary_entry(word: str) -> dict[str, Any] | None:
    endpoint = f"https://api.dictionaryapi.dev/api/v2/entries/en/{word}"
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
            response = await client.get(endpoint)
        if response.status_code != 200:
            return None
        payload = response.json()
        if not payload:
            return None
        entry = payload[0]
        meanings = entry.get("meanings", [])
        first = meanings[0] if meanings else {}
        definitions = first.get("definitions", [])
        definition = definitions[0] if definitions else {}
        return {
            "word": entry.get("word", word),
            "phonetic": entry.get("phonetic", ""),
            "part_of_speech": first.get("partOfSpeech", ""),
            "definition": definition.get("definition", ""),
            "example": definition.get("example", ""),
            "source": "dictionaryapi.dev",
        }
    except (httpx.HTTPError, ValueError, IndexError, KeyError):
        return None


@app.get("/")
def root():
    return {
        "name": APP_NAME,
        "version": app.version,
        "status": "online",
        "docs": "/docs",
        "endpoints": ["/health", "/stats", "/words", "/phrases", "/lookup/{word}"],
    }


@app.get("/health")
def health():
    return {"status": "healthy", "service": APP_NAME, "time": utc_now()}


@app.get("/stats")
def stats():
    with db() as conn:
        words = conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        phrases = conn.execute("SELECT COUNT(*) FROM phrases").fetchone()[0]
        sources = conn.execute("SELECT COUNT(*) FROM learning_sources").fetchone()[0]
    return {"words": words, "phrases": phrases, "learning_sources": sources}


@app.get("/words")
def list_words(q: str = "", limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    with db() as conn:
        if q:
            rows = conn.execute(
                "SELECT * FROM words WHERE word LIKE ? ORDER BY word LIMIT ? OFFSET ?",
                (f"%{q.lower()}%", limit, offset),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM words ORDER BY word LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


@app.get("/phrases")
def list_phrases(q: str = "", limit: int = Query(default=50, ge=1, le=200), offset: int = Query(default=0, ge=0)):
    with db() as conn:
        if q:
            rows = conn.execute(
                "SELECT * FROM phrases WHERE phrase LIKE ? ORDER BY phrase LIMIT ? OFFSET ?",
                (f"%{q.lower()}%", limit, offset),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM phrases ORDER BY phrase LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return {"items": [dict(row) for row in rows], "limit": limit, "offset": offset}


@app.get("/lookup/{word}")
async def lookup(word: str):
    normalised = word.strip().lower()
    if not re.fullmatch(r"[a-z][a-z'-]{0,48}", normalised):
        raise HTTPException(status_code=400, detail="Enter one English word.")
    with db() as conn:
        row = conn.execute("SELECT * FROM words WHERE word = ?", (normalised,)).fetchone()
    if row and row["definition"]:
        return dict(row)
    entry = await fetch_dictionary_entry(normalised)
    if not entry:
        if row:
            return dict(row)
        raise HTTPException(status_code=404, detail="No dictionary entry found for this word yet.")
    now = utc_now()
    with db() as conn:
        conn.execute("""
            INSERT INTO words(word, definition, phonetic, part_of_speech, example, source, confidence, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?, 1.0, ?, ?)
            ON CONFLICT(word) DO UPDATE SET
                definition=excluded.definition,
                phonetic=excluded.phonetic,
                part_of_speech=excluded.part_of_speech,
                example=excluded.example,
                source=excluded.source,
                confidence=1.0,
                last_seen=excluded.last_seen
        """, (normalised, entry["definition"], entry["phonetic"], entry["part_of_speech"],
              entry["example"], entry["source"], now, now))
        saved = conn.execute("SELECT * FROM words WHERE word = ?", (normalised,)).fetchone()
    return dict(saved)


@app.post("/learn/text", dependencies=[Depends(require_admin)])
def learn_text(payload: LearnText):
    result = store_learning(payload.text, payload.source, payload.title, payload.url)
    return {"status": "learned", **result}


@app.post("/learn/google", dependencies=[Depends(require_admin)])
async def learn_google(payload: SearchRequest):
    api_key = os.getenv("GOOGLE_API_KEY")
    cse_id = os.getenv("GOOGLE_CSE_ID")
    if not api_key or not cse_id:
        raise HTTPException(status_code=503, detail="Configure GOOGLE_API_KEY and GOOGLE_CSE_ID first.")
    params = {"key": api_key, "cx": cse_id, "q": payload.query, "num": min(payload.limit, MAX_SOURCE_ITEMS)}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get("https://www.googleapis.com/customsearch/v1", params=params)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Google Search API request failed.")
    items = response.json().get("items", [])
    total_words = total_phrases = 0
    for item in items:
        text = " ".join([item.get("title", ""), item.get("snippet", "")])
        result = store_learning(text, "google", item.get("title", ""), item.get("link", ""))
        total_words += result["words_found"]
        total_phrases += result["phrases_found"]
    return {"provider": "google", "items_processed": len(items),
            "words_found": total_words, "phrases_found": total_phrases}


@app.post("/learn/youtube", dependencies=[Depends(require_admin)])
async def learn_youtube(payload: SearchRequest):
    api_key = os.getenv("YOUTUBE_API_KEY")
    if not api_key:
        raise HTTPException(status_code=503, detail="Configure YOUTUBE_API_KEY first.")
    params = {"key": api_key, "q": payload.query, "part": "snippet",
              "type": "video", "maxResults": min(payload.limit, MAX_SOURCE_ITEMS)}
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get("https://www.googleapis.com/youtube/v3/search", params=params)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="YouTube Data API request failed.")
    items = response.json().get("items", [])
    total_words = total_phrases = 0
    for item in items:
        snippet = item.get("snippet", {})
        video_id = item.get("id", {}).get("videoId", "")
        result = store_learning(
            " ".join([snippet.get("title", ""), snippet.get("description", "")]),
            "youtube", snippet.get("title", ""),
            f"https://www.youtube.com/watch?v={video_id}" if video_id else "",
        )
        total_words += result["words_found"]
        total_phrases += result["phrases_found"]
    return {"provider": "youtube", "items_processed": len(items),
            "words_found": total_words, "phrases_found": total_phrases}


@app.get("/sources")
def list_sources(limit: int = Query(default=30, ge=1, le=100)):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM learning_sources ORDER BY added_at DESC LIMIT ?", (limit,)
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@app.get("/integrations")
def integrations():
    return {
        "google_search": {"available": bool(os.getenv("GOOGLE_API_KEY") and os.getenv("GOOGLE_CSE_ID")),
                          "method": "Google Programmable Search API"},
        "youtube": {"available": bool(os.getenv("YOUTUBE_API_KEY")),
                    "method": "YouTube Data API v3 metadata"},
        "tiktok": {"available": False,
                   "method": "Approved TikTok API access or user-provided exports/imports required"},
        "manual_import": {"available": True, "endpoint": "/learn/text"},
    }
