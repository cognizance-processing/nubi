/**
 * Nubi logo geometry — generated from the vector redraw of public/nubi.png.
 * Mark grid: 362 x 286 (ribbon width 60, arch/bowl R98 outer / R38 inner,
 * arrowhead base 120, apex 20 above the arch, x-height 250 from y 20 to the
 * baseline at y 270, bowl overshoot to 286). Do not edit by hand.
 *
 * Lockup: the mark IS the "n", so the lockup reads [mark]ubi. "ubi" is
 * Instrument Sans SemiBold outlined to paths (u, b with its counter, i stem,
 * i dot) at the mark's own scale: same x-height and baseline, stems ~62 against
 * the 60 ribbon. Never scale either part. The u's left stem sits at x 396, 64
 * units after the arrow stem (332) and 34 after the barb tip (362): about 30%
 * wider than the font's own n|u gap, to make up for the barb. Extents
 * x 0..1042.08, y -89.31 (i dot) .. 286 (bowl). The standalone "nubi" wordmark
 * is retired: never set "nubi" after the mark.
 */
export const NUBI_MARK_PATH = "M0 270V118A98 98 0 0 1 196 118V188A38 38 0 0 0 272 188V100H242L302 0L362 100H332V188A98 98 0 0 1 136 188V118A38 38 0 0 0 60 118V270Z"
export const NUBI_MARK_VIEWBOX = '0 0 362 286'
/** Mark split for the two-tone variant: ribbon without the head, and the head. */
export const NUBI_MARK_RIBBON_PATH = 'M0 270V118A98 98 0 0 1 196 118V188A38 38 0 0 0 272 188V99H332V188A98 98 0 0 1 136 188V118A38 38 0 0 0 60 118V270Z'
export const NUBI_MARK_HEAD_PATH = 'M242 100L302 0L362 100Z'
/** Ribbon centreline (stroke width 60, butt caps) for the animated loader. */
export const NUBI_MARK_CENTERLINE = 'M30 270V118A68 68 0 0 1 166 118V188A68 68 0 0 0 302 188V70'
export const NUBI_MARK_CENTERLINE_LENGTH = 770
/** "ubi" in lockup coordinates (u stem at x 396). A separate path from the mark, so the duo variant can colour the mark's head alone. */
export const NUBI_LOCKUP_PATH = "M479.83 274.9Q453.85 274.9 434.98 263.87Q416.1 252.84 406.05 233.73Q396 214.61 396 190.59L396 20L457.77 20L457.77 177.84Q457.77 200.88 469.29 212.65Q480.81 224.41 501.89 224.41Q521 224.41 535.47 215.59Q549.93 206.76 558.5 190.83Q567.08 174.9 567.08 154.8L567.08 20L629.34 20L629.34 270L569.04 270L569.04 223.43Q557.28 245 537.18 258.24Q512.67 274.9 479.83 274.9M821.99 274.9Q786.2 274.9 763.65 257.75Q746.99 245.98 739.63 226.86L739.63 270L679.34 270L679.34 -82.94L741.1 -82.94L741.1 61.67Q748.95 43.53 767.08 31.27Q790.61 15.1 825.91 15.1Q859.24 15.1 884 31.03Q908.75 46.96 922.48 75.88Q936.2 104.8 936.2 144.02Q936.2 183.73 921.99 213.14Q907.77 242.55 882.28 258.73Q856.79 274.9 821.99 274.9M806.3 223.92Q836.2 223.92 853.85 203.09Q871.5 182.25 871.5 144.51Q871.5 106.76 853.6 86.18Q835.71 65.59 805.81 65.59Q776.4 65.59 758.26 86.42Q740.12 107.25 740.12 145Q740.12 182.75 758.26 203.33Q776.4 223.92 806.3 223.92M1039.63 270L977.87 270L977.87 20L1039.63 20L1039.63 270M1042.08 -21.18L974.93 -21.18L974.93 -89.31L1042.08 -89.31"
export const NUBI_LOCKUP_VIEWBOX = '0 -90 1043 376'
/** Width / height of the lockup viewBox (1043 / 376). */
export const NUBI_LOCKUP_ASPECT = 2.774
