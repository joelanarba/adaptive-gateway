# Administrative dashboard

Next.js 16.4.2 / React 19, Node.js 22. From this directory run `npm ci`,
`npm run dev`; production verification is `npm run lint` and `npm run build`.
The gateway defaults to http://localhost:8000; set NEXT_PUBLIC_GATEWAY_URL before
building when using another URL. Browser-visible configuration is not secret.

Create the first administrator with the gateway's interactive manage.py command
(see the root README). Login accepts email/password JSON. Session tokens live in
sessionStorage; logout revokes the refresh token. Access expiry requires login
again. Backend administrator checks are authoritative.

Pages show actual /admin/stats, /admin/queue and /admin/config responses. API keys
are issued once, listed by prefix and can be revoked. No synthetic traffic is
displayed. Queue acceptance and HTTP success do not prove a business write committed.

The Dockerfile builds the application, prunes development dependencies and runs
as the node user. Compose dev overrides the command with npm run dev. Production
runs the baked build. The root audit records the locally verified build and
remaining development-only advisory; TLS/browser XSS hardening is still needed
before public administrative use.
