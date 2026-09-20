# MeteorBase

[![PyPI Version](https://img.shields.io/pypi/v/meteorbase?style=flat-square&color=blue)](https://pypi.org/project/meteorbase/)
[![Python Version](https://img.shields.io/pypi/pyversions/meteorbase?style=flat-square)](https://pypi.org/project/meteorbase/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

MeteorBase is a FastAPI gateway and Python SDK for AI summarization, Firebase-backed data, API key management, and service telemetry.

## Features

- FastAPI service with health, readiness, summarization, summaries, and dynamic data endpoints.
- Python SDK with authenticated profile access and typed exception handling.
- API key generation, SHA-256 hashing, revocation, usage tracking, and rate limiting.
- Automated 90-day retention checks for stored records.
- Request counts, latency metrics, status totals, and uptime history.


## Installation

```bash
pip install meteorbase
```

Or with uv:

```bash
uv add meteorbase
```

## Python SDK

The SDK uses the hosted gateway by default and authenticates with a MeteorBase API key:

```python
from meteorbase import Client
from meteorbase.client import MeteorBaseAPIKeyError

try:
    with Client(apikey="meteor_live_your_api_key_here") as client:
        profile = client.about()
        print(profile["user"])
except MeteorBaseAPIKeyError:
    print("Authentication failed. Check your API key.")
```

The client maps authentication failures, rate limits, server errors, and network failures to `MeteorBaseError` subclasses. Use `client.close()` or a context manager to release the underlying HTTP connection.

## Run The API Locally

Install the project dependencies, configure the required environment variables, and start Uvicorn:

```bash
pip install -r requirements.txt
cp .env.example .env
uvicorn meteorbase.app:app --reload --port 3000
```

The application is also available through the compatibility entry point `app:app`.

### Configuration

Copy `.env.example` to `.env` and set the credentials required by the features you use:

| Variable | Purpose |
| --- | --- |
| `GEMINI_API_KEY` | Enables AI summarization. |
| `FIREBASE_API_KEY` | Enables Firestore reads and writes. |
| `FIREBASE_PROJECT_ID` | Firebase project identifier. |
| `MY_API_SECRET` | Optional shared secret for protected summarization requests. |
| `RETENTION_DAYS` | Retention window; defaults to `90`. |
| `PORT` | Local server port; defaults to `3000`. |

## API Endpoints

| Method | Endpoint | Description |
| --- | --- | --- |
| `GET` | `/` | Serves the dashboard interface. |
| `GET` | `/api/auth/me` | Validates a Bearer API key and returns its profile. |
| `POST` | `/summarize` | Generates and stores an AI summary. |
| `GET` | `/summarize/health` | Reports summarizer readiness. |
| `GET` | `/ping` | Health probe and retention check. |
| `GET` | `/healthz` | Health probe with telemetry. |
| `GET` | `/readyz` | Reports Firebase and Gemini readiness. |
| `GET` | `/summaries` | Lists active summaries. |
| `GET` | `/summaries/{summary_id}` | Fetches one summary. |
| `DELETE` | `/summaries/{summary_id}` | Deletes one summary. |
| `GET` | `/api/telemetry/stats` | Returns request and uptime metrics. |
| `POST` | `/api/user/keys` | Creates a developer API key. |
| `GET` | `/api/user/keys` | Lists keys for a user. |
| `DELETE` | `/api/user/keys/{key_id}` | Revokes a key. |
| `GET` | `/api/user/usage` | Returns usage metrics for a user. |
| `POST` | `/api/data/{collection}` | Stores a document in a dynamic collection. |
| `GET` | `/api/data/{collection}` | Lists documents in a dynamic collection. |

Interactive OpenAPI documentation is available at `/docs` when the service is running.

## Publishing

Releases are built and published to PyPI by `.github/workflows/publish.yml` when a GitHub release is published.


## License

MeteorBase is distributed under the MIT License.
