# Nubi brand assets

The mark is one ribbon: it forms a lowercase **n**, flows through a bowl (the n's swash) and rises
into an up arrow. It is the `public/nubi.png` silhouette redrawn as geometry (constant ribbon width,
true semicircular arch and bowl, flat terminals, an isosceles arrowhead whose base is twice the
ribbon width). One colour, no gradient.

**The mark is the "n" of nubi.** The lockup reads **[mark]ubi**: the mark followed by "ubi" in
Instrument Sans SemiBold, outlined, on the mark's own x-height and baseline. **Never set "nubi"
after the mark**, and never set the name as live text next to it.

| File | Use |
|---|---|
| `nubi-lockup.svg` / `nubi-lockup-inverse.svg` | The [mark]ubi lockup, ink / light ink. Headers, footers, auth, emails. |
| `nubi-lockup-mono.svg` / `nubi-lockup-white.svg` | Pure black (print, single-colour production) / white (on the accent or on photos). |
| `nubi-lockup-duo.svg` / `nubi-lockup-duo-inverse.svg` | Arrowhead in the accent colour. Only at an x-height of 48px and up (OG image, large marketing). |
| `nubi-mark.svg` / `nubi-mark-inverse.svg` | The mark alone. Collapsed sidebar, avatar, favicon source, anything below the lockup minimum. |
| `nubi-mark-duo.svg` / `nubi-mark-duo-inverse.svg` | Mark with the accent arrowhead. 48px and up only. |
| `nubi-wordmark.svg` / `nubi-wordmark-inverse.svg` | Retired name kept for existing references: carries the lockup. Use `nubi-lockup*` in new work. |
| `og-image.png` | 2400 x 1260 social card. |

"inverse" means for dark surfaces (fill `#f4f5f7`). On an accent tile use white.

In the app, do not use these files: render `<Logo />` from `src/components/Logo.tsx`
(inline SVG, `currentColor`, flips with the theme). For string-built markup import
`NUBI_MARK_SVG` / `NUBI_LOCKUP_SVG` from the same file. Geometry lives in
`src/components/ui/logoPaths.ts`.

## Geometry

In the mark's 362 x 286 grid: ribbon 60, x-height 250 (y 20 to the baseline at 270), bowl
overshoot to 286, arrow apex at 0. "ubi" has the same x-height and baseline (stems about 62).
The u's left stem sits at x 396: 64 units after the arrow stem, 34 after the barb tip, about 30%
wider than the font's own n|u gap to make up for the barb. Lockup viewBox `0 -90 1043 376`
(aspect 2.774). Never scale the mark or the letters relative to each other, and never respace them.

## Rules

- **The lockup reads [mark]ubi; never set "nubi" after the mark.**
- **Clear space:** the x-height on all four sides.
- **Minimum size:** lockup x-height 15px (`size` 22, about 63px wide). Below that use the mark alone, minimum 16px wide.
- **Sizes in product** (`<Logo size>` is the mark width in px): public header and landing 26
  (75 x 27px), app sidebar 24, auth 22, footer 28, 404 and onboarding 26.
- **Two-tone** (accent arrowhead) only at an x-height of 48px and up. Never in a header.
- Do not recolour outside ink, light ink, black, white or the single accent. Do not add shadows,
  outlines, gradients, or place the logo on a busy background. Do not stretch, rotate or re-set the
  letters in another font. The arrow is never replaced by another shape.
- The name is lowercase in the logo; in running text write "Nubi".

## Favicon set (in `public/`)

`favicon.svg` (ink, swaps to light ink with `prefers-color-scheme: dark`), `favicon.ico` (16/32/48,
white mark on an ink tile so it reads on any browser chrome), `apple-touch-icon.png` (180),
`icon-192.png`, `icon-512.png`, `icon-maskable-512.png` (mark inside the 80% safe zone), and
`site.webmanifest`. The mark did not change, so neither did these.
