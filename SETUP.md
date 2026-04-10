# Fix "database_unavailable" / get the site working

If the site shows **database not running** or `{"error":"database_unavailable",...}`:

## 1. Install Docker Desktop (one-time)

- Download and install: **https://www.docker.com/products/docker-desktop/**
- Open **Docker Desktop** and wait until it says it’s running.

## 2. Start the database

In Terminal:

```bash
cd /Users/craiglutz/Agents/Mining_OS
docker compose up -d
```

Wait about 30 seconds for Postgres to be ready.

## 3. Initialize the database

```bash
cd /Users/craiglutz/Agents/Mining_OS
.venv/bin/python -m mining_os.pipelines.run_all --init-db
```

## 4. Refresh the site

Reload **http://localhost:8000** in your browser. The Dashboard and Minerals/Areas pages should load with data.

---

**If the web server isn’t running**, start it:

```bash
cd /Users/craiglutz/Agents/Mining_OS
.venv/bin/uvicorn mining_os.api.main:app --host 127.0.0.1 --port 8000
```

Then open **http://localhost:8000**.
