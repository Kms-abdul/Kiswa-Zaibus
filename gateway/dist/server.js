"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.createApp = createApp;
/* Kiswa-Zaibus gateway: the only process exposed to the network.
     /                 React app (frontend/dist), with SPA fallback
     /api/*            proxied to the Flask API on 127.0.0.1 (cookies pass through)
     /webhooks/razorpay/:company   signature-checked, then forwarded to Flask
     /healthz          gateway + API health */
const express_1 = __importDefault(require("express"));
const fs_1 = __importDefault(require("fs"));
const helmet_1 = __importDefault(require("helmet"));
const http_1 = __importDefault(require("http"));
const https_1 = __importDefault(require("https"));
const path_1 = __importDefault(require("path"));
const express_rate_limit_1 = __importDefault(require("express-rate-limit"));
const http_proxy_middleware_1 = require("http-proxy-middleware");
const config_1 = require("./config");
const log_1 = require("./log");
const razorpay_1 = require("./razorpay");
const scheduler_1 = require("./scheduler");
function createApp() {
    const app = (0, express_1.default)();
    app.disable('x-powered-by');
    app.set('trust proxy', config_1.config.trustProxy);
    app.use((0, helmet_1.default)({
        contentSecurityPolicy: { directives: { defaultSrc: ["'self'"], imgSrc: ["'self'", 'data:', 'blob:'], styleSrc: ["'self'", "'unsafe-inline'"],
                scriptSrc: ["'self'", 'https://checkout.razorpay.com'], frameSrc: ["'self'", 'https://api.razorpay.com'], connectSrc: ["'self'", 'https://lumberjack.razorpay.com'],
                upgradeInsecureRequests: config_1.config.httpsPublic ? [] : null } },
        strictTransportSecurity: config_1.config.httpsPublic ? { maxAge: 31536000 } : false,
        crossOriginEmbedderPolicy: false,
    }));
    app.get('/healthz', async (_req, res) => {
        try {
            const r = await fetch(`${config_1.config.apiUrl}/api/health`);
            const j = await r.json();
            res.status(r.ok ? 200 : 503).json({ gateway: 'ok', api: j });
        }
        catch {
            res.status(503).json({ gateway: 'ok', api: 'unreachable' });
        }
    });
    app.use((0, razorpay_1.razorpayRouter)());
    // Internal endpoints are never reachable from outside.
    app.use('/internal', (_req, res) => { res.status(404).end(); });
    const authLimiter = (0, express_rate_limit_1.default)({ windowMs: 15 * 60 * 1000, limit: 30, standardHeaders: 'draft-7', legacyHeaders: false,
        message: { error: 'rate_limited', message: 'Too many sign-in attempts from this address. Please wait 15 minutes.' } });
    app.use(['/api/auth/login', '/api/auth/otp', '/api/auth/forgot', '/api/auth/reset'], authLimiter);
    app.use('/api', (0, express_rate_limit_1.default)({ windowMs: 60 * 1000, limit: 600, standardHeaders: 'draft-7', legacyHeaders: false }));
    app.use('/api', (0, http_proxy_middleware_1.createProxyMiddleware)({
        target: config_1.config.apiUrl + '/api', changeOrigin: false, xfwd: true, proxyTimeout: 60000,
        on: { error: (err, _req, res) => {
                (0, log_1.log)('ERROR', `proxy: ${err.message}`);
                const r = res;
                if (!r.headersSent) {
                    r.writeHead(502, { 'Content-Type': 'application/json' });
                }
                r.end(JSON.stringify({ error: 'api_unavailable', message: 'The server is starting or unavailable. Please try again in a minute.' }));
            } },
    }));
    if (fs_1.default.existsSync(config_1.config.staticDir)) {
        app.use(express_1.default.static(config_1.config.staticDir, { index: false, maxAge: '7d', setHeaders: (res, p) => { if (p.endsWith('.html'))
                res.setHeader('Cache-Control', 'no-store'); } }));
        app.get('*', (_req, res) => res.sendFile(path_1.default.join(config_1.config.staticDir, 'index.html')));
    }
    else {
        app.get('/', (_req, res) => res.type('text').send('Kiswa-Zaibus gateway is running, but the web app is not built (frontend/dist missing).'));
    }
    return app;
}
if (require.main === module) {
    const app = createApp();
    const server = config_1.config.tlsPfx
        ? https_1.default.createServer({ pfx: fs_1.default.readFileSync(config_1.config.tlsPfx), passphrase: config_1.config.tlsPassphrase || undefined }, app)
        : http_1.default.createServer(app);
    server.listen(config_1.config.port, config_1.config.host, () => (0, log_1.log)('INFO', `gateway listening on ${config_1.config.tlsPfx ? 'https' : 'http'}://${config_1.config.host}:${config_1.config.port} -> API ${config_1.config.apiUrl}`));
    if (config_1.config.workers)
        (0, scheduler_1.startWorkers)();
    const stop = () => { (0, log_1.log)('INFO', 'gateway stopping'); server.close(() => process.exit(0)); setTimeout(() => process.exit(0), 5000).unref(); };
    process.on('SIGINT', stop);
    process.on('SIGTERM', stop);
}
