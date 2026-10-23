# Agri Price Tracker — Data Pipeline

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A Python data pipeline that collects agricultural mandi prices for **Kerala, India**, validates the source records, normalizes commodity and market information, and publishes app-ready JSON files. It uses the Government of India's `data.gov.in` API for Agmarknet market data and can optionally use NVIDIA's LLM API to classify newly encountered commodities.

> This repository contains the **data pipeline**, not the mobile application itself.

## Contents

- [Overview](#overview)
- [Pipeline workflow](#pipeline-workflow)
- [Data source](#data-source)
- [Output files](#output-files)
- [Requirements](#requirements)
- [Setup](#setup)
- [Configuration](#configuration)
- [Run the pipeline](#run-the-pipeline)
- [Data handling](#data-handling)
- [Security](#security)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Overview

The pipeline turns raw government market-price records into structured JSON suitable for a consumer application. It is designed around Kerala market data and supports a known commodity catalogue while allowing new commodities to be discovered from incoming API records.

### Key capabilities

- **API ingestion:** Fetches CSV data from the `data.gov.in` resource with pagination.
- **Resilient requests:** Retries failed requests with exponential backoff and request timeouts.
- **Kerala-only validation:** Checks the returned state and matches districts against a normalized allow-list to reduce accidental ingestion of other states' data.
- **Commodity normalization:** Maps source commodity names to stable product IDs and display names.
- **Automatic discovery:** Registers previously unseen commodities found in API records.
- **Price normalization:** Extracts market, district, arrival date, minimum price, maximum price, modal price, unit, and source.
- **Category assignment:** Uses predefined category overrides first, preserves a valid existing category where available, and optionally calls an NVIDIA-hosted language model for classification.
- **JSON output:** Maintains a manifest and per-product price-history files for downstream use.
- **Retention and merging:** Merges incoming data with existing history and applies a configurable retention window.

## Pipeline workflow

```text
data.gov.in / Agmarknet API
          |
          v
Paginated CSV ingestion
          |
          v
Retry handling and Kerala validation
          |
          v
Commodity discovery and normalization
          |
          v
Price/date/unit processing
          |
          v
Category assignment
  (overrides / saved category / optional NVIDIA API)
          |
          v
Merge history and apply retention policy
          |
          v
data/manifest.json + data/prices/*.json
```

## Data source

- **Provider:** Government of India's Open Government Data platform, `data.gov.in`
- **Dataset:** Agmarknet mandi price resource
- **Resource ID:** `9ef84268-d588-465a-a308-a864a43d0070`
- **Filter:** State = Kerala, followed by local state and district validation
- **Price fields:** Minimum, maximum, and modal price, along with market and arrival date

The source API's availability, coverage, update frequency, and field names can change. Values should be interpreted according to the source dataset's definitions.

## Output files

The script writes data relative to its own directory:

```text
data/
├── manifest.json
└── prices/
    ├── <product-id>.json
    └── ...
```

- **`data/manifest.json`** — product catalogue and metadata used by the consuming application, including category information.
- **`data/prices/<product-id>.json`** — price history for an individual product.

Price-history rows include fields such as:

| Field | Description |
| --- | --- |
| `id` | Stable row identifier derived from product, market, and date |
| `market` | Market name reported by the source |
| `district` | District name reported by the source |
| `date` | Arrival date in ISO format (`YYYY-MM-DD`) |
| `min_price` | Minimum reported price |
| `max_price` | Maximum reported price |
| `modal_price` | Modal reported price |
| `unit` | Price basis assigned by the pipeline |
| `source` | Source attribution |

The script's current default retention period is **7 days**. Check the retention and merge logic before changing it, especially if the consuming application expects historical records.

## Requirements

- Python **3.10 or later**
- A valid `data.gov.in` API key
- Internet access to the data source
- Optional: an NVIDIA API key if AI-based category classification is required

Python dependency:
- `requests`

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/muhammedshibilm/agri-price-tracker.git
cd agri-price-tracker
```

### 2. Create a virtual environment

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows PowerShell:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```

### 3. Install dependencies

```bash
python -m pip install --upgrade pip
python -m pip install requests
```

If the repository adds a `requirements.txt`, prefer installing from it:

```bash
python -m pip install -r requirements.txt
```

## Configuration

Configure credentials through environment variables. Do not hard-code API keys in source files.

| Variable | Required? | Purpose |
| --- | --- | --- |
| `DATA_GOV_IN_API_KEY` | Yes | Authenticates requests to the data.gov.in API |
| `NVIDIA_API_KEY` | No | Enables AI-based commodity classification |
| `NVIDIA_MODEL` | No | NVIDIA API model name; defaults to `meta/llama-3.1-8b-instruct` |

### Linux/macOS

```bash
export DATA_GOV_IN_API_KEY="your_data_gov_in_api_key"
export NVIDIA_API_KEY="your_nvidia_api_key"
# Optional: override the default model
export NVIDIA_MODEL="meta/llama-3.1-8b-instruct"
```

### Windows PowerShell

```powershell
$env:DATA_GOV_IN_API_KEY = "your_data_gov_in_api_key"
$env:NVIDIA_API_KEY = "your_nvidia_api_key"
$env:NVIDIA_MODEL = "meta/llama-3.1-8b-instruct"
```

The NVIDIA variables are optional. Without `NVIDIA_API_KEY`, the script logs that classification is unavailable; products without a predefined or previously stored category may have a null category.

## Run the pipeline

Run the script from the repository root:

```bash
python fetch_data.py
```

The script fetches source records, validates them, processes recognized commodities, updates the JSON output files, and logs progress to the console. Ensure the required API key is set before running it.

## Data handling

- **State validation:** API-side filtering is supplemented by local state and district checks. District spelling aliases are normalized to support common source variations.
- **Date parsing:** The script expects source arrival dates in `DD/MM/YYYY` format and writes ISO dates.
- **Units:** The default price basis is per quintal, with explicit product-specific overrides where configured. Verify the source's unit conventions before relying on a value.
- **Categories:** Known product categories take precedence over AI classification. AI output is accepted only when it matches a supported category.
- **Retention:** The configured retention window is seven days. Review this behavior if the downstream app needs longer historical trends.

## Security

- Never commit API keys, access tokens, or other secrets.
- Use environment variables or a secret manager in automated environments.
- If credentials are accidentally committed, revoke and rotate them; deleting them from the latest file alone is not sufficient.
- Treat external API responses and model-generated classifications as untrusted input.
- Check API usage limits and the terms of the source data provider.

## Troubleshooting

**Missing `DATA_GOV_IN_API_KEY`**

Set the required environment variable in the same shell or runtime that executes the script.

**No records verified as Kerala**

The pipeline deliberately stops if it cannot verify Kerala state and recognized district fields. Check whether the API response schema or source field names have changed; do not bypass validation without understanding the impact.

**NVIDIA classification is unavailable**

Confirm that `NVIDIA_API_KEY` is configured and that the selected model is available to your account. Classification is optional; predefined categories can still be assigned.

**API request failures**

Check network connectivity, API availability, credentials, and provider rate limits. The script retries failed requests, but it stops after the configured attempts.

## Contributing

Bug reports, improvements, and documentation updates are welcome. For substantial changes, open an issue to discuss the proposal before submitting a pull request. Please avoid committing credentials or private data.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for the full terms.

## Maintainer

Created and maintained by [muhammedshibilm](https://github.com/muhammedshibilm).
