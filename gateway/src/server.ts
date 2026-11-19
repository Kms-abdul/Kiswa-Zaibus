/* Kiswa-Zaibus gateway: the only process exposed to the network.
     /                 React app (frontend/dist), with SPA fallback
     /api/*            proxied to the Flask API on 127.0.0.1 (cookies pass through)
     /webhooks/razorpay/:company   signature-checked, then forwarded to Flask
     /healthz          gateway + API health */
import express from 'express';
import fs from 'fs';
import helmet from 'helmet';
import http from 'http';
import https from 'https';
import path from 'path';
import rateLimit from 'express-rate-limit';
import { createProxyMiddleware } from 'http-proxy-middleware';
import { config } from './config';
import { log } from './log';
import { razorpayRouter } from './razorpay';
import { startWorkers } from './scheduler';

export function createApp() {
  const app = express();
  app.disable('x-powered-by');
  app.set('trust proxy', config.trustProxy);
  app.use(helmet({
    contentSecurityPolicy: { directives: { defaultSrc: ["'self'"], imgSrc: ["'self'", 'data:', 'blob:'], styleSrc: ["'self'", "'unsafe-inline'"],
      scriptSrc: ["'self'", 'https://checkout.razorpay.com'], frameSrc: ["'self'", 'https://api.razorpay.com'], connectSrc: ["'self'", 'https://lumberjack.razorpay.com'],
      upgradeInsecureRequests: config.httpsPublic ? [] : null } },
    strictTransportSecurity: config.httpsPublic ? { maxAge: 31536000 } : false,
    crossOriginEmbedderPolicy: false,
  }));

  app.get('/healthz', async (_req, res) => {
    try {
      const r = await fetch(`${config.apiUrl}/api/health`);
      const j = await r.json();
      res.status(r.ok ? 200 : 503).json({ gateway: 'ok', api: j });
    } catch { res.status(503).json({ gateway: 'ok', api: 'unreachable' }); }
  });

  app.use(razorpayRouter());

  // Internal endpoints are never reachable from outside.
  app.use('/internal', (_req, res) => { res.status(404).end(); });

  const authLimiter = rateLimit({ windowMs: 15 * 60 * 1000, limit: 30, standardHeaders: 'draft-7', legacyHeaders: false,
    message: { error: 'rate_limited', message: 'Too many sign-in attempts from this address. Please wait 15 minutes.' } });
  app.use(['/api/auth/login', '/api/auth/otp', '/api/auth/forgot', '/api/auth/reset'], authLimiter);
  app.use('/api', rateLimit({ windowMs: 60 * 1000, limit: 600, standardHeaders: 'draft-7', legacyHeaders: false }));

  app.use('/api', createProxyMiddleware({
    target: config.apiUrl + '/api', changeOrigin: false, xfwd: true, proxyTimeout: 60000,
    on: { error: (err, _req, res) => {
      log('ERROR', `proxy: ${err.message}`);
      const r = res as http.ServerResponse;
      if (!r.headersSent) { r.writeHead(502, { 'Content-Type': 'application/json' }); }
      r.end(JSON.stringify({ error: 'api_unavailable', message: 'The server is starting or unavailable. Please try again in a minute.' }));
    } },
  }));

  if (fs.existsSync(config.staticDir)) {
    app.use(express.static(config.staticDir, { index: false, maxAge: '7d', setHeaders: (res, p) => { if (p.endsWith('.html')) res.setHeader('Cache-Control', 'no-store'); } }));
    app.get('*', (_req, res) => res.sendFile(path.join(config.staticDir, 'index.html')));
  } else {
    app.get('/', (_req, res) => res.type('text').send('Kiswa-Zaibus gateway is running, but the web app is not built (frontend/dist missing).'));
  }
  return app;
}

if (require.main === module) {
  const app = createApp();
  const server = config.tlsPfx
    ? https.createServer({ pfx: fs.readFileSync(config.tlsPfx), passphrase: config.tlsPassphrase || undefined }, app)
    : http.createServer(app);
  server.listen(config.port, config.host, () => log('INFO', `gateway listening on ${config.tlsPfx ? 'https' : 'http'}://${config.host}:${config.port} -> API ${config.apiUrl}`));
  if (config.workers) startWorkers();
  const stop = () => { log('INFO', 'gateway stopping'); server.close(() => process.exit(0)); setTimeout(() => process.exit(0), 5000).unref(); };
  process.on('SIGINT', stop); process.on('SIGTERM', stop);
}
