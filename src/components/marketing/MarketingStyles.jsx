/**
 * MarketingStyles — the shared design language for every marketing page
 * (landing, /compare, /pricing): observatory panels, glass cards, gradient
 * text, scroll reveals, terminal cards, range sliders, code-highlight tokens.
 *
 * Render ONCE near the top of each marketing page. All selectors are
 * lp-/nubi-lp-prefixed so they cannot leak into the app shell.
 */
const MarketingStyles = () => (
  <style>{`
    /* ── Hero float (product frame + chips, staggered) ── */
    @keyframes lp-float {
      0%, 100% { transform: translateY(0px); }
      50%       { transform: translateY(-8px); }
    }
    .lp-float-1 { animation: lp-float 7s ease-in-out infinite; }
    .lp-float-2 { animation: lp-float 8.5s ease-in-out infinite; animation-delay: 0.9s; }
    .lp-float-3 { animation: lp-float 9.5s ease-in-out infinite; animation-delay: 1.7s; }

    /* ── Terminal code card (How-it-works) — always dark, so force the dark
          highlight token set regardless of theme ── */
    .lp-term {
      --lp-hl-kw:    #7eaaf0;
      --lp-hl-fn:    #2dd4bf;
      --lp-hl-str:   #e8a35c;
      --lp-hl-num:   #2dd4bf;
      --lp-hl-param: #b39df5;
      --lp-hl-punc:  #93a3b8;
      --lp-hl-cm:    #7a8aa0;
    }

    /* ── Scroll reveal (decision rows) ── */
    .lp-reveal {
      opacity: 0;
      transform: translateY(26px);
      transition: opacity 0.7s ease, transform 0.7s cubic-bezier(0.22, 1, 0.36, 1);
    }
    .lp-reveal.lp-in { opacity: 1; transform: none; }
    @media (prefers-reduced-motion: reduce) {
      .lp-reveal { transition: none; opacity: 1; transform: none; }
      .lp-float-1, .lp-float-2, .lp-float-3, .lp-mesh-a, .lp-mesh-b { animation: none; }
    }

    /* ── Observatory hero panel — light by day, dark by night ── */
    .lp-hero-panel {
      background:
        radial-gradient(ellipse 60% 55% at 18% 8%,  rgba(36, 86, 166, 0.13) 0%, transparent 62%),
        radial-gradient(ellipse 55% 60% at 88% 36%, rgba(23, 179, 163, 0.11) 0%, transparent 60%),
        linear-gradient(180deg, #f6f9ff 0%, #e9effb 100%);
      transition: background 0.45s ease, border-color 0.45s ease;
    }
    .dark .lp-hero-panel {
      background:
        radial-gradient(ellipse 60% 55% at 18% 8%,  rgba(46, 96, 186, 0.34) 0%, transparent 62%),
        radial-gradient(ellipse 55% 60% at 88% 36%, rgba(20, 160, 146, 0.20) 0%, transparent 60%),
        radial-gradient(ellipse 70% 60% at 50% 115%, rgba(27, 35, 99, 0.55) 0%, transparent 70%),
        #070b21;
    }
    .lp-mesh-blob { opacity: 0.45; }
    .dark .lp-mesh-blob { opacity: 1; }
    .lp-hero-grid { opacity: 0.22; }
    .dark .lp-hero-grid { opacity: 0.14; }
    /* drifting mesh blobs — slow, barely-there life */
    @keyframes lp-mesh-a {
      0%, 100% { transform: translate(0, 0) scale(1); }
      50%       { transform: translate(3%, -4%) scale(1.07); }
    }
    @keyframes lp-mesh-b {
      0%, 100% { transform: translate(0, 0) scale(1); }
      50%       { transform: translate(-4%, 3%) scale(1.1); }
    }
    .lp-mesh-a { animation: lp-mesh-a 17s ease-in-out infinite; will-change: transform; }
    .lp-mesh-b { animation: lp-mesh-b 21s ease-in-out infinite; will-change: transform; }
    /* film grain on the dark panel */
    .lp-noise {
      background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%25' height='100%25' filter='url(%23n)'/></svg>");
      opacity: 0.025;
      mix-blend-mode: overlay;
    }
    .dark .lp-noise { opacity: 0.05; }
    /* gradient display text — brand stops on light, lifted stops on dark */
    .lp-hero-gradient-text {
      background: linear-gradient(105deg, #1b2363 0%, #2456a6 45%, #17b3a3 100%);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
      color: transparent;
    }
    .dark .lp-hero-gradient-text {
      background: linear-gradient(105deg, #8db4f5 0%, #5fd6c8 60%, #2dd4bf 100%);
      -webkit-background-clip: text;
      background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    /* primary CTA glow */
    .lp-cta-glow {
      box-shadow: 0 12px 44px -10px rgba(23, 179, 163, 0.55), 0 4px 16px rgba(36, 86, 166, 0.45);
    }
    .lp-cta-glow:hover {
      box-shadow: 0 16px 56px -10px rgba(23, 179, 163, 0.7), 0 6px 20px rgba(36, 86, 166, 0.55);
    }
    /* glassy floating stat chips over the product frame */
    .lp-hero-chip {
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid rgba(27, 35, 99, 0.12);
      box-shadow: 0 12px 32px -12px rgba(27, 35, 99, 0.35), inset 0 1px 0 rgba(255, 255, 255, 0.6);
    }
    .dark .lp-hero-chip {
      background: rgba(13, 20, 48, 0.72);
      border: 1px solid rgba(255, 255, 255, 0.14);
      box-shadow: 0 12px 32px -10px rgba(0, 0, 0, 0.6), inset 0 1px 0 rgba(255, 255, 255, 0.08);
    }

    /* ── CTA button pulse ── */
    @keyframes lp-pulse-primary {
      0%, 100% { box-shadow: 0 0 0 0 rgba(36, 86, 166, 0.4); }
      50%       { box-shadow: 0 0 0 10px rgba(36, 86, 166, 0); }
    }
    .lp-cta-pulse { animation: lp-pulse-primary 3s ease-in-out infinite; }
    .lp-cta-pulse:hover { animation: none; }

    /* ── Diff card hover lift ── */
    .lp-diff-card {
      transition: transform 0.24s cubic-bezier(0.34, 1.56, 0.64, 1),
                  box-shadow 0.24s ease;
    }
    .lp-diff-card:hover {
      transform: translateY(-3px);
    }

    /* ── Illustration canvas — dotted gradient panel so illustrations sit on an
          intentional surface (Stripe/Vercel-style) instead of empty whitespace ── */
    .lp-illo-card {
      position: relative;
      background:
        radial-gradient(circle at 1px 1px, rgba(36,86,166,0.07) 1px, transparent 1.6px) 0 0 / 22px 22px,
        linear-gradient(155deg, var(--surface-2) 0%, var(--surface) 60%);
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.5),
        0 1px 2px rgba(27,35,99,0.04),
        0 18px 40px -18px rgba(27,35,99,0.22);
      transition: transform 0.3s cubic-bezier(0.34,1.4,0.64,1), box-shadow 0.3s ease;
    }
    .lp-illo-card:hover {
      transform: translateY(-4px);
      box-shadow:
        inset 0 1px 0 rgba(255,255,255,0.5),
        0 24px 50px -16px rgba(27,35,99,0.28);
    }
    /* brand hairline at the top edge of the canvas */
    .lp-illo-card::before {
      content: '';
      position: absolute; left: 16px; right: 16px; top: 0; height: 2px;
      background: linear-gradient(90deg, transparent, rgba(23,179,163,0.5), rgba(36,86,166,0.5), transparent);
      border-radius: 2px;
    }

    /* ── Step connector line ── */
    .lp-connector {
      background: linear-gradient(90deg, #1b2363 0%, #2456a6 50%, #17b3a3 100%);
    }

    /* ── Compare table row striping ── */
    .lp-compare-row:nth-child(even) { background: rgba(36, 86, 166, 0.04); }
    .lp-compare-row:hover           { background: rgba(36, 86, 166, 0.08); }

    /* NOTE: no global html smooth-scroll rule — it animates the browser's
       scroll restoration on every route change (visible jank at the top bar).
       Anchor links smooth-scroll programmatically instead. */

    /* ── Compare table — mobile horizontal scroll ── */
    .lp-compare-table-wrap {
      overflow-x: auto;
      -webkit-overflow-scrolling: touch;
    }
    .lp-compare-table-inner {
      min-width: 620px;
    }

    /* ── Compare table — Nubi column highlight ── */
    .lp-nubi-col {
      background: linear-gradient(180deg,
        rgba(23,179,163,0.07) 0%,
        rgba(36,86,166,0.05) 100%);
      border-left: 1.5px solid rgba(23,179,163,0.25);
      border-right: 1.5px solid rgba(23,179,163,0.25);
    }
    .lp-nubi-col-header {
      background: linear-gradient(180deg,
        rgba(23,179,163,0.15) 0%,
        rgba(36,86,166,0.10) 100%);
      border-left: 1.5px solid rgba(23,179,163,0.35);
      border-right: 1.5px solid rgba(23,179,163,0.35);
      border-top: 2px solid #17b3a3;
    }

    /* ── How-it-works step card ── */
    .lp-step-card {
      transition: box-shadow 0.2s ease, transform 0.2s ease;
    }
    .lp-step-card:hover {
      transform: translateY(-2px);
    }

    /* ── Step connector arrow ── */
    .lp-step-arrow {
      color: #17b3a3;
      opacity: 0.5;
    }

    /* ── Chip badges ── */
    .lp-chip {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      font-size: 0.7rem;
      font-weight: 600;
      padding: 3px 9px;
      border-radius: 999px;
      border: 1px solid;
      line-height: 1.4;
      white-space: nowrap;
    }

    /* ── Cost-calculator range input ── */
    .lp-range {
      -webkit-appearance: none; appearance: none;
      height: 6px; border-radius: 999px; cursor: pointer;
      background: linear-gradient(90deg, #2456a6, #17b3a3);
    }
    .lp-range::-webkit-slider-thumb {
      -webkit-appearance: none; appearance: none;
      width: 20px; height: 20px; border-radius: 50%;
      background: #fff; border: 3px solid #17b3a3;
      box-shadow: 0 1px 4px rgba(27,35,99,0.25);
    }
    .lp-range::-moz-range-thumb {
      width: 20px; height: 20px; border-radius: 50%;
      background: #fff; border: 3px solid #17b3a3;
      box-shadow: 0 1px 4px rgba(27,35,99,0.25);
    }

    /* ── Code-highlighter token colors — explicit light/dark pairs so every
          token keeps readable contrast on the code surface in BOTH themes ── */
    .nubi-lp {
      --lp-hl-kw:    #2456a6; /* keyword (blue) */
      --lp-hl-fn:    #0f766e; /* function / tag / command (teal) */
      --lp-hl-str:   #9a5b16; /* string (amber) */
      --lp-hl-num:   #0f766e;
      --lp-hl-param: #6d3fd4; /* {{param}} (violet) */
      --lp-hl-punc:  #64748b;
      --lp-hl-cm:    #6b7a90; /* comment */
    }
    .dark .nubi-lp {
      --lp-hl-kw:    #7eaaf0;
      --lp-hl-fn:    #2dd4bf;
      --lp-hl-str:   #e8a35c;
      --lp-hl-num:   #2dd4bf;
      --lp-hl-param: #b39df5;
      --lp-hl-punc:  #93a3b8;
      --lp-hl-cm:    #7a8aa0;
    }
  `}</style>
)

export default MarketingStyles
