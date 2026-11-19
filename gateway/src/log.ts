import fs from 'fs';
import path from 'path';
import { config } from './config';

let stream: fs.WriteStream | null = null;
function out() {
  if (stream) return stream;
  try { fs.mkdirSync(config.logDir, { recursive: true }); stream = fs.createWriteStream(path.join(config.logDir, 'gateway.log'), { flags: 'a' }); } catch { /* console only */ }
  return stream;
}
export function log(level: 'INFO' | 'WARN' | 'ERROR', msg: string) {
  const line = `${new Date().toISOString()} ${level} ${msg}`;
  (level === 'ERROR' ? console.error : console.log)(line);
  out()?.write(line + '\n');
}
