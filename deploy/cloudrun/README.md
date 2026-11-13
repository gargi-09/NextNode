# Hosting the public API on Cloud Run

The public NextNode API (`PUBLIC_MODE=1`: only the stateless analyze flow, the read-only
ontology and health) on Google Cloud Run. It scales to zero, so an idle demo costs nothing, and
it never runs more than one instance, so the in-process rate limits hold.

## What it costs

| Piece | Setting | Cost at demo traffic |
|---|---|---|
| Cloud Run | min 0 / max 1 instance, 1 vCPU, 512 MiB, billed only while handling requests | Inside the always-free monthly allowance for a low-traffic demo |
| Cloud Build | one build per deploy | Inside the free monthly build minutes for a few deploys |
| Artifact Registry | one image of about 275 MB (old images can be deleted) | Inside the free storage allowance |
| Secret Manager | 4 secrets | Inside the free allowance |
| Neo4j Aura Free | existing instance | $0 (kept awake by `.github/workflows/keepalive.yml`) |
| Gemini | one parse per new resume or job text; repeats come from the cache | Free tier, or a fraction of a cent per parse on the paid tier |

Free allowances change: check the Cloud Run, Cloud Build and Artifact Registry pricing pages
before you start. **Set a budget first** (Billing > Budgets & caps), with alerts and, where the
console offers it, a spend cap that pauses services at the cap (enforcement can lag by hours, so
overages are still billed). Keep `--max-instances 1` and the per-IP limits (3 analyses a minute,
20 a day) as well.

**Resumes are personal data.** On Gemini's free tier, Google may use inputs to improve its
products; on the paid tier it does not. For real visitors, enable billing on the Gemini API key's
project (AI Studio) so the key is on the paid tier.

## Steps

1. Create a Google Cloud project and link a billing account (console).
2. Budget alert (alerts by email; it does not stop spending):
   ```bash
   gcloud billing budgets create --billing-account=<BILLING_ACCOUNT_ID> --display-name=nextnode --budget-amount=5USD --threshold-rule=percent=0.5 --threshold-rule=percent=0.9 --threshold-rule=percent=1.0
   ```
3. Deploy (asks for the Neo4j URI, username, password and the Gemini key; input is hidden):
   ```bash
   .\deploy\cloudrun\deploy.ps1 -Project <PROJECT_ID> -SetSecrets
   ```
   Later deploys: `.\deploy\cloudrun\deploy.ps1 -Project <PROJECT_ID>`.
4. Check it: `curl <URL>/health` (public_mode true) and `curl <URL>/health/graph` (200).
5. Keep Aura awake: in GitHub, Settings > Secrets and variables > Actions > Variables, add
   `NEXTNODE_API_URL` = the service URL. The daily `keep-graph-awake` workflow then pings it.

## What is uploaded

`gcloud run deploy --source .` uploads only what `.gcloudignore` allows: the Dockerfile,
`requirements.txt`, `app/` and `ontology/`. `.env` is never uploaded or baked into the image;
secrets reach the container as environment variables from Secret Manager, readable only by the
`nextnode-api` service account.

## Notes

- Cold start: the first request after an idle period waits for the container to start.
- Cloud Run allocates CPU only during requests, so the 10-minute visitor sweep runs at startup
  and while requests are being served; it only matters if a request crashed mid-way.
- To take the demo offline: `gcloud run services delete nextnode-api --region us-east1`.
