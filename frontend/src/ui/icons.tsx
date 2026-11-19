/* Line icons (2.8, Redwood theme). 24-unit grid, stroke = currentColor. */
const P: Record<string, string> = {
  home: 'M3 10.5 12 3l9 7.5M5 9.5V21h5v-6h4v6h5V9.5',
  check: 'M4 12.5 9 17.5 20 6.5',
  bill: 'M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6M9 16h3',
  list: 'M8 6h13M8 12h13M8 18h13M3.5 6h.01M3.5 12h.01M3.5 18h.01',
  cap: 'M2 9l10-5 10 5-10 5zM6 11v5c0 1.5 3 3 6 3s6-1.5 6-3v-5M22 9v6',
  cart: 'M3 4h2l2.5 11h11L21 7H6.5M9 20h.01M18 20h.01',
  box: 'M3 7.5 12 3l9 4.5v9L12 21l-9-4.5zM3 7.5l9 4.5 9-4.5M12 12v9',
  tag: 'M3 12V3h9l9 9-9 9zM7.5 7.5h.01',
  swap: 'M4 8h14l-4-4M20 16H6l4 4',
  plusminus: 'M12 4v8M8 8h8M8 18h8',
  truck: 'M2 6h12v10H2zM14 9h4l3 3v4h-7M6 19.5a1.5 1.5 0 1 0 0-.01M17 19.5a1.5 1.5 0 1 0 0-.01',
  clipboard: 'M9 4h6v3H9zM7 5H5v16h14V5h-2M8.5 13l2.5 2.5 4.5-5',
  barcode: 'M4 5v14M7 5v14M10 5v14M14 5v14M16 5v14M20 5v14',
  cash: 'M2 7h20v10H2zM12 14.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5M5.5 12h.01M18.5 12h.01',
  ledger: 'M5 3h12a2 2 0 0 1 2 2v16H7a2 2 0 0 1-2-2zM5 17a2 2 0 0 1 2-2h12M9 7h6',
  bank: 'M3 10 12 4l9 6M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 20h18',
  qr: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h2v2h-2zM18 14h2M14 18h2v2M18 18h2v2h-2',
  chart: 'M4 20V10M10 20V4M16 20v-7M21 20H3',
  flag: 'M5 21V4h11l-1.5 4L16 12H5',
  users: 'M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8M2 21a7 7 0 0 1 14 0M17 3.5a4 4 0 0 1 0 7.5M22 21a7 7 0 0 0-4-6.3',
  tools: 'M14.5 6.5a4 4 0 0 0-5.3 5.3L3 18l3 3 6.2-6.2a4 4 0 0 0 5.3-5.3l-2.5 2.5-2.5-.5-.5-2.5z',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.2-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 4.1V4a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0 1.2 2.9h.1a2 2 0 0 1 0 4h-.1z',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14M21 21l-5-5',
  bell: 'M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9M10.3 21a1.9 1.9 0 0 0 3.4 0',
  help: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3M12 17h.01',
  chev: 'M9 6l6 6-6 6',
  down: 'M6 9l6 6 6-6',
  trend: 'M3 17l6-6 4 4 8-8M15 7h6v6',
  receipt: 'M6 3h12v18l-3-2-3 2-3-2-3 2zM9 8h6M9 12h6',
  person: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8M4 21a8 8 0 0 1 16 0',
  draft: 'M6 3h9l4 4v14H6zM14 3v5h5M9 13h6M9 17h4',
  menu: 'M4 6h16M4 12h16M4 18h16',
  out: 'M15 4h4v16h-4M10 16l4-4-4-4M14 12H4',
};
export function Icon({ n, size = 18 }: { n: string; size?: number }) {
  return <svg className="ico" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.7} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={P[n] || P.list} /></svg>;
}
/** Icon for each menu screen. */
export const NAV_ICON: Record<string, string> = {
  '/': 'home', '/approvals': 'check', '/pos': 'bill', '/invoices': 'list', '/msea': 'cap', '/online': 'cart', '/stock': 'box', '/items': 'tag', '/transfers': 'swap',
  '/adjustments': 'plusminus', '/purchasing': 'truck', '/stock-audits': 'clipboard', '/labels': 'barcode', '/cash': 'cash', '/accounts': 'ledger', '/finance': 'bank',
  '/paylinks': 'qr', '/reports': 'chart', '/audit': 'flag', '/users': 'users', '/setup': 'tools', '/settings': 'gear',
};
