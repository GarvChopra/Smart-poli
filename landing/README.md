# SmartPoli waitlist page

Public marketing page and its small API. No strategy, no user data, no secrets in this folder.

- `index.html`, `style.css`, `app.js`, `assets/`: the static page (host on Cloudflare Pages).
- `_headers`: security headers for Cloudflare Pages. Replace `https://api.example.com` with the real API origin before deploying.
- `worker/`: the API (Cloudflare Worker + D1). Four routes, everything else is a tiny 404.

## Run it locally

```
cd landing/worker
npm install
npm test                      # starts the Worker locally and attacks it (11 checks)
npm run dev                   # Worker on :8791
```

Serve the page with any static server, e.g. `python -m http.server 8788` in `landing/`, and set `ALLOWED_ORIGIN` / `SITE_URL` to that address.

## Deploy checklist (Garv / Shashwat)

1. `wrangler d1 create smartpoli-waitlist`, paste the id into `wrangler.toml`, then `wrangler d1 execute smartpoli-waitlist --remote --file schema.sql`.
2. Secrets: `wrangler secret put RESEND_API_KEY` and `wrangler secret put TURNSTILE_SECRET`. Never put them in a file.
3. Set `ALLOWED_ORIGIN`, `SITE_URL`, `MAIL_FROM` in `wrangler.toml` to the real domain, then `wrangler deploy`.
4. In `index.html` set `api-base` and `turnstile-site-key`; in `_headers` set the API origin.
5. Cloudflare dashboard: proxy the API domain, add a rate-limiting rule (about 60 requests/minute per IP) and a WAF rule that allows only the four paths. Turn on Bot Fight Mode.
6. Kill switch: set `WAITLIST_OPEN = "false"` and redeploy to close signups instantly.
