"use strict";
var __importDefault = (this && this.__importDefault) || function (mod) {
    return (mod && mod.__esModule) ? mod : { "default": mod };
};
Object.defineProperty(exports, "__esModule", { value: true });
exports.log = log;
const fs_1 = __importDefault(require("fs"));
const path_1 = __importDefault(require("path"));
const config_1 = require("./config");
let stream = null;
function out() {
    if (stream)
        return stream;
    try {
        fs_1.default.mkdirSync(config_1.config.logDir, { recursive: true });
        stream = fs_1.default.createWriteStream(path_1.default.join(config_1.config.logDir, 'gateway.log'), { flags: 'a' });
    }
    catch { /* console only */ }
    return stream;
}
function log(level, msg) {
    const line = `${new Date().toISOString()} ${level} ${msg}`;
    (level === 'ERROR' ? console.error : console.log)(line);
    out()?.write(line + '\n');
}
