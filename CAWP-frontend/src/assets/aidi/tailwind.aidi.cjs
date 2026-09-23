/* Aidi AI Enterprise — Tailwind theme extension.
 * tailwind.config.js:  const aidi = require('./aidi-enterprise/assets/tailwind.aidi.js')
 *                      module.exports = { theme: { extend: aidi } }
 * Dark theme: use the CSS custom properties in aidi-enterprise.css with
 * darkMode: ['class', '[data-theme="dark"]'] — the hexes below are the light values.
 */
module.exports = {
  colors: {
    ink:   { 950:'#04062F', 900:'#070A4B', 800:'#14184F', 700:'#262B6B', 600:'#3B4189', 500:'#565CA6' },
    azure: { 50:'#EEF3FD', 100:'#D9E4FA', 200:'#B3C8F4', 300:'#7D9DEA', 400:'#5482E5', 500:'#2F5FD6', 600:'#234BB0', 700:'#1A3A8A' },
    // Rails, dots and 2px underlines. 2–4 per screen, under 2% of pixels.
    signal:{ 100:'#FFE0CC', tint:'#FFF4EC', 500:'#FF6600', 600:'#E65B00', dark:'#FF8433', darkTint:'#241634' },
    slate: { 0:'#FFFFFF', 25:'#FAFCFD', 50:'#F2F6F8', 100:'#E7EEF2', 150:'#DFEEF2', 200:'#D7E1E8', 300:'#B8C4CE', 400:'#8A929F', 500:'#6B7482', 600:'#515966', 700:'#3B4250', 900:'#221F20' },
    lavender: { 300:'#B2AAC4', 500:'#8E86B8' },
    success: { DEFAULT:'#0F7A52', tint:'#E4F2EC' },
    warning: { DEFAULT:'#B25A00', tint:'#FBEBDD' },
    danger:  { DEFAULT:'#B3261E', tint:'#F9E6E4' },
    info:    { DEFAULT:'#234BB0', tint:'#E7EDFA' },
    viz: { 1:'#070A4B', 2:'#2F5FD6', 3:'#5B93C9', 4:'#8FB9CE', 5:'#8E86B8', 6:'#B2AAC4', 7:'#6B7482', 8:'#B8C4CE', highlight:'#FF6600' },
    night: { page:'#060820', card:'#0C1033', raised:'#141A45', border:'#262C5E', 'border-strong':'#3A4179', strong:'#F2F5FB', body:'#C7CDE4', muted:'#8D95B8' },
  },
  fontFamily: {
    sans:   ['Anodina', 'Segoe UI', 'system-ui', 'sans-serif'],
    arabic: ['Manifa V2', 'Noto Sans Arabic', 'sans-serif'],
    mono:   ['ui-monospace', 'Menlo', 'Consolas', 'monospace'],
  },
  // Anodina has 3 weights only: 300 / 400 / 800. Manifa V2 has 2: 400 / 700.
  fontWeight: { light: '300', normal: '400', extrabold: '800' },
  fontSize: {
    micro:   ['11px',   { lineHeight: '1.3',  letterSpacing: '0.12em',  fontWeight: '800' }],
    section: ['13px',   { lineHeight: '1.3',  letterSpacing: '0.1em',   fontWeight: '800' }],
    small:   ['13.5px', { lineHeight: '1.5' }],
    body:    ['15px',   { lineHeight: '1.55' }],
    lead:    ['16px',   { lineHeight: '1.55' }],
    title:   ['20px',   { lineHeight: '1.25', letterSpacing: '-0.01em', fontWeight: '800' }],
    display: ['30px',   { lineHeight: '1.2',  letterSpacing: '-0.015em',fontWeight: '800' }],
    kpi:     ['32px',   { lineHeight: '1.1',  letterSpacing: '-0.02em', fontWeight: '800' }],
  },
  borderRadius: { none: '0', xs: '4px', sm: '6px', md: '8px', lg: '12px', pill: '999px' },
  boxShadow: {
    none:   'none',           // elevation 0 — the default. Use a border.
    raised: '0 1px 2px rgba(7,10,75,.06)',
    overlay:'0 8px 24px rgba(7,10,75,.10)',
    modal:  '0 24px 60px rgba(7,10,75,.16)',
    focus:  '0 0 0 2px #fff, 0 0 0 4px #2F5FD6',
    field:  '0 0 0 2px rgba(47,95,214,.22)',
  },
  height:   { control: '36px', 'control-sm': '32px', row: '44px', 'row-head': '38px' },
  spacing:  { card: '20px', section: '32px' },
  transitionTimingFunction: { aidi: 'cubic-bezier(.2,.8,.2,1)' },
  transitionDuration: { fast: '120ms', DEFAULT: '200ms', slow: '340ms' },
};
