# Mining_OS logs

When the API server is running, it writes:

- **api_requests.log** – Every HTTP request and response (method, path, status code). Check this if "Fetch Claim Records" returns 404 or to see which path was hit.
- **mining_os.log** – General application log (INFO and above).

**Quick checks:**

1. **Restart the backend** after pulling new code so the latest routes and logging are active.
2. **Verify routes:** Open `GET /api/debug/routes` in the browser (e.g. `http://localhost:8000/api/debug/routes`). You should see `POST /api/areas-of-focus/{area_id}/fetch-claim-records` in the list. If not, the server is still running old code.
3. After clicking "Fetch Claim Records", look in **api_requests.log** for `REQUEST POST /api/areas-of-focus/...` and the corresponding `RESPONSE ... -> 200` (or the status you got).
