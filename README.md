# Star-AI English Library

A cloud-ready English knowledge service for Star-AI. It stores words and phrases, looks up dictionary definitions, and learns vocabulary candidates from permitted source metadata and imported text.

## Features

- Searchable word and phrase database
- On-demand definitions, phonetics, parts of speech and examples through Dictionary API
- Phrase and vocabulary candidate extraction from text
- Google Programmable Search API integration
- YouTube Data API v3 metadata integration
- Import endpoint for approved TikTok exports, captions, notes or other user-provided text
- SQLite storage with a persistent cloud disk configuration
- FastAPI docs at `/docs`

## Run locally

Requires Python 3.10 or newer.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000/docs.

## Cloud deployment

This repository includes a Render Blueprint in `render.yaml`.

1. Create a new Render Blueprint from this GitHub repository.
2. Set a strong `ADMIN_API_KEY`.
3. Add API credentials for the integrations you want to enable.
4. Deploy. The configured persistent disk stores the SQLite database.

For a larger production deployment, move the database to managed PostgreSQL and restrict CORS to Star-AI's actual application domains.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/health` | Health check |
| GET | `/stats` | Word, phrase and source counts |
| GET | `/words?q=...` | Search stored words |
| GET | `/phrases?q=...` | Search stored phrases |
| GET | `/lookup/{word}` | Get a stored or live dictionary entry |
| POST | `/learn/text` | Import text and learn candidate words/phrases |
| POST | `/learn/google` | Learn from Google Search result titles/snippets |
| POST | `/learn/youtube` | Learn from YouTube video titles/descriptions |
| GET | `/sources` | Recent learning sources |
| GET | `/integrations` | Integration readiness |

Learning endpoints accept the `X-API-Key` header when `ADMIN_API_KEY` is configured.

Example import:

```bash
curl -X POST http://localhost:8000/learn/text \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: change-this-before-deploying' \
  -d '{"source":"manual","title":"Example phrases","text":"That is brilliant. No worries, give it a go, and keep me posted."}'
```

## How the learning system works

The initial version extracts candidate words and short phrases, counts repeated appearances, and stores where each item came from. Definitions are added when a word is looked up. This avoids pretending that a phrase is understood merely because it appeared online; later versions can add ranking, duplicate resolution, human review, semantic categories and context-aware meanings.

### Platform access

- **Google:** uses the official Programmable Search JSON API. Configure both `GOOGLE_API_KEY` and `GOOGLE_CSE_ID`.
- **YouTube:** uses the official YouTube Data API v3 for public video metadata. It does not download videos or bypass captions/access controls.
- **TikTok:** automatic collection requires an approved TikTok API/product and the relevant permissions. Until those credentials and permissions exist, import permitted text or exports through `/learn/text`. The service does not scrape private or logged-in content.

## Important

This is the working cloud API foundation, not yet a pre-populated copy of every dictionary or a trained language model. The database grows as definitions are requested and permitted learning sources are imported. Dictionary API availability and platform API quotas apply.
