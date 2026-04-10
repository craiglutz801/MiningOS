# Mining OS — Synopsis for AI (strategy & “prime the pump” context)

Use this document as context when helping design **how to build or source an initial Target list** and **ongoing deal-sourcing strategy**. It describes what the product is, what it is for, and which levers already exist in the tool.

---

## Purpose

**Mining OS** (Mining Deal OS) supports **mining deal workflow**: tracking **targets** (properties, claims, or prospects) that matter for **priority minerals**, tying them to **location** (PLSS, coordinates), **government and technical reports**, **BLM claim/payment context**, and **maps**. The user’s goal is to **prioritize and operationalize** opportunities (monitoring → negotiation → diligence → ownership), not generic GIS browsing.

---

## Core concepts

- **Targets** — The primary unit of work: a named area/prospect with minerals, location, status fields, notes, links to reports, and integrations (BLM, MLRS, LR2000-style data, etc.).
- **Minerals of interest** — A configurable, DB-backed list (e.g. tungsten, uranium, fluorspar) that drives filtering, discovery, and alerts.
- **Target Status** — Deal stage: Monitoring (Low/Med/High), Negotiation, Due Diligence, Ownership.
- **Claim Status / Claim Type** — BLM-oriented fields (paid/unpaid, patented/unpatented, lode/placer, etc.) with UI badges and map styling where applicable.
- **Report links** — PDFs and URLs associated with targets (including USGS and other government reports).

---

## Main ways targets (and related data) enter the system

1. **CSV import** — Bulk load from structured files (legacy paths also reference Utah dockets, perspective mines, review spreadsheets under `data_files/`).
2. **Single PDF report upload** — User uploads a mining report; **AI** extracts candidate targets (names, PLSS, lat/long, minerals, county, notes) in a **review → import** flow. PDFs can be stored and linked to imported targets.
3. **Batch Process Reports** — User uploads a **CSV of USGS-style docket metadata** (docket, property name, state, county, minerals). The app builds **USGS Data Series 1004** scan URLs for **OME / DMEA / DMA** lists, **downloads PDFs**, extracts text (PyMuPDF, pypdf, optional **Tesseract OCR** for scans), then **AI** proposes targets. Users can also **import metadata only** (skip PDF) to seed the list quickly.
4. **Discovery agent** — Dashboard workflow: editable prompts; AI (+ optional web search) proposes locations aligned to **target states** and **minerals**, with BLM/status checks where integrated. Can **replace** or **add** discovery-sourced areas.
5. **Legacy pipeline** — BLM open-claims × MRDS proximity **scoring** still exists for candidate-style workflows (separate from the newer Targets-centric UI).

---

## Location, map, and research features

- **PLSS** — Stored and normalized; **BLM Cadastral** used to geocode PLSS to coordinates for the map where possible.
- **Map (Leaflet)** — Targets plotted by coordinates; status reflected in pin styling. **Overlays**: WMS, ArcGIS, GeoJSON, MRDS “known mines” and similar public layers.
- **BLM / MLRS** — Paid/unpaid and case linkage via integrated **BLM_ClaimAgent** (sibling project or configured path) when available.
- **LR2000 / Geographic Index–style report** — In-app query against BLM MLRS-style national claims data by target PLSS/coordinates; results attached to target **characteristics** for review next to other MLRS fetches.

---

## Alerts and comms

- **Email alerts** for high-priority **unpaid** claims tied to priority minerals (`ALERT_EMAIL`, optional SMTP in `.env`).

---

## Technical prerequisites (for AI-assisted ingestion)

- **Postgres/PostGIS** (typically Docker Compose), **Python 3.11+**, **FastAPI** backend, **React** frontend on port 8000 in the bundled setup.
- **`OPENAI_API_KEY`** — Required for **PDF extraction** and **Discovery agent** (and batch AI extraction).
- **Optional OCR** — `pdf-ocr` extra + **Tesseract** binary for image-only USGS scans in batch/single PDF flows.

---

## Roadmap direction (not yet built)

- **Automation engine** — Scheduled rules (e.g. “weekly, high-priority targets → refresh MLRS / BLM status → email if changed”). Planned; not a substitute for defining the **initial** target universe.

---

## What “prime the pump” means in this product

An initial Target list should be **actionable in Mining OS**: rows that can carry **minerals**, **location** (PLSS and/or coords), **state/county**, **report or docket references**, and **deal status**. Strong sources include:

- **USGS DS-1004 docket CSVs** (OME, DMEA, DMA) + batch PDF or metadata import.
- **Company or internal spreadsheets** aligned to the CSV import schema.
- **Discovery agent** output (iterative enrichment).
- **Legacy MRDS×BLM candidates** if the user wants scored geographic seeds.

When advising strategy, call out **data shape** (what columns or fields exist), **provenance** (government vs desk study), **refresh cadence**, and **how each source maps** to Targets + report_links + optional PDF storage.

---

*This file is a briefing for external or internal AI assistants; it is not a substitute for reading `README.md`, `ROADMAP.md`, or the API schema for exact field names.*
