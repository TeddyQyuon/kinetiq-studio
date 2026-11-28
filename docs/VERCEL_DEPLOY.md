# Deploy Studio on Vercel with the API on EC2

Vercel serves the Next.js Studio. EC2 continues to run Caddy, FastAPI, the orchestrator, PostgreSQL, Redis, the media worker, and the render worker. The examples use the existing Sydney API. Substitute your assigned production Vercel hostname for `<project>.vercel.app`.

## 1. Import the web workspace

1. Make this release available in GitHub. In Vercel, choose **Add New → Project**, connect GitHub, and import **TeddyQyuon/kinetiq-studio**. Deploy the revision containing `apps/web/vercel.json` and the matching API changes. For a production deployment, use the production branch configured for the project.
2. Select **Next.js** and set **Root Directory** to **apps/web**. Under the root directory setting, enable **Include source files outside of the Root Directory in the Build Step**. Studio imports the contracts and Remotion workspaces, and npm uses the root lockfile. [Vercel monorepo settings](https://vercel.com/docs/monorepos/monorepo-faq)
3. Select Node.js **22.x**. Leave the install command, build command, and output directory at their project defaults. The checked-in configuration runs `cd ../.. && npm ci` and `cd ../.. && npm run build --workspace @kinetiq/web`.
4. Under **Environment Variables**, set both values for **Production**. `NEXT_PUBLIC_API_URL` is embedded in the browser bundle during the build. `API_INTERNAL_URL` is used by the Vercel server for Studio's backend status check.

   | Variable | Value |
   | --- | --- |
   | `NEXT_PUBLIC_API_URL` | `https://api.13.238.76.182.sslip.io` |
   | `API_INTERNAL_URL` | `https://api.13.238.76.182.sslip.io` |

5. Click **Deploy** and copy the stable production hostname from **Settings → Domains**. If using a custom Studio domain, add it there and use its exact origin throughout the remaining steps. Redeploy Vercel after changing either API URL.

## 2. Configure the EC2 release

1. On EC2, use the existing checkout, Compose project name, volumes, and private root `.env`. Check out the same release deployed on Vercel. In `.env`, update these entries while retaining the existing production secrets, database, Drive, and worker settings.

   ```dotenv
   APP_ENV=phase1_vps
   RUN_EMBEDDED_ORCHESTRATOR=false
   API_DOMAIN=api.13.238.76.182.sslip.io
   PUBLIC_API_URL=https://api.13.238.76.182.sslip.io
   PUBLIC_WEB_URL=https://<project>.vercel.app
   CORS_ORIGINS='["https://<project>.vercel.app"]'
   COOKIE_SAMESITE=none
   GOOGLE_REDIRECT_URI=https://api.13.238.76.182.sslip.io/v1/auth/google/callback
   ```

   Use the exact production origin in CORS, without a trailing slash. API-only deployment reads the Studio host from `PUBLIC_WEB_URL`. It ignores the VPS `STUDIO_DOMAIN` and `NEXT_PUBLIC_API_URL` settings. Keep backend credentials on EC2.

2. In Google Cloud, open the existing web OAuth client. Under **Authorized redirect URIs**, confirm the exact API callback above. If the client configuration calls for an **Authorized JavaScript origin**, add `https://<project>.vercel.app` there. The API exchanges the code and redirects the owner to Studio's `/settings?drive=connected`. Retain the `openid`, `email`, and `https://www.googleapis.com/auth/drive.file` scopes.
3. From the EC2 repository root, run the preflight and deploy.

   ```bash
   chmod 600 .env
   node scripts/check-env.mjs --file .env --api-only
   bash scripts/deploy-vps.sh --env-file .env --api-only --dry-run
   bash scripts/deploy-vps.sh --env-file .env --api-only
   ```

   Use `--api-only` on subsequent hybrid deployments. Caddy loads `infra/Caddyfile.api` and obtains TLS for the API hostname only. The deploy script takes the usual pre-migration backup, checks the internal and public API endpoints, and stops the old VPS web container after the cutover.

## 3. Verify the cutover

Run the smoke test from a machine that can reach both public hosts.

```bash
node scripts/smoke-test.mjs \
  --api-url https://api.13.238.76.182.sslip.io \
  --web-url 'https://<project>.vercel.app'
```

Substitute the Vercel hostname before running the command. Expect **10/10** checks, including an online response from `/api/backend-status`. A signed-out Phase 1 API returns 401 for owner routes.

In the production Studio, open **Settings → Connect Google Drive**. After Google redirects back, verify that Drive shows connected. Reload Studio and make a plan edit. In browser DevTools, check that the credentialed `GET /v1/me` succeeds, the edit sends `X-CSRF-Token`, and the project event stream connects. The API's host-only session and PKCE cookies use `Secure`, `HttpOnly`, `Path=/`, and `SameSite=None`. The CSRF token comes from the authenticated identity response.

If sign-in loops, inspect the browser's blocked-cookie reason. Browsers or profiles that block third-party cookies can still reject this Vercel/sslip.io pairing. For those environments, serve Studio and the API on HTTPS subdomains of the same registrable domain, update both providers' URLs, CORS and Google's callback, and use `COOKIE_SAMESITE=lax`. [Browser cookie behavior](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie)
