/**
 * Logo — renders the Nubi logo mark (public/nubi.png) with an optional
 * "Nubi" wordmark displayed in Space Grotesk with the brand gradient.
 *
 * Props:
 *   size        {number}  — logo image height/width in px (default 32)
 *   showName    {boolean} — show the "Nubi" wordmark beside the mark (default true)
 *   className   {string}  — extra classes on the wrapper
 */
export default function Logo({ size = 32, showName = true, className = '' }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <img
        src="/nubi.png"
        alt="Nubi logo"
        width={size}
        height={size}
        className="nubi-logo-mark"
        style={{ width: size, height: size, objectFit: 'contain' }}
        draggable={false}
      />
      {showName && (
        <span
          className="nubi-logo-word font-display font-semibold tracking-tight select-none"
          style={{ fontSize: size * 0.65, lineHeight: 1 }}
        >
          Nubi
        </span>
      )}
    </span>
  )
}
