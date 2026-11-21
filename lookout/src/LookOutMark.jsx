/**
 * The LookOut mark — the same eye the officer app wears as its launcher icon.
 *
 * Why it is a component and not three copies of a lucide <Eye>: the dashboard
 * drew its brand with a generic icon-set eye at three different sizes and two
 * different background colours, while the phone app shipped a drawn mark of
 * its own. The two halves of one system did not look like one product, and
 * nothing in the code said what the brand actually was. This file is now the
 * answer to that question, in one place.
 *
 * The geometry is traced from officer_app/assets/icon.png: an almond stroked
 * at 43/512 of the box, an iris ring of the same weight, and the pupil left
 * open so the badge colour shows through it rather than being painted on.
 */

/** Badge ground. Sampled from the app icon, not guessed. */
export const BRAND_TEAL = "#0b5471";
/** The eye itself, and anything that sits on the teal. */
export const BRAND_CREAM = "#f6f0e7";

/**
 * The eye glyph alone, drawn in `currentColor` unless told otherwise.
 *
 * Deliberately NOT theme-reactive the way the old `var(--sidebar-primary)`
 * pairing was: a logo that inverts between light and dark mode is two logos.
 * The badge carries its own contrast, so it reads on either background.
 */
export function LookOutEye({ size = 24, color = "currentColor", className = "" }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 512 512"
      fill="none"
      stroke={color}
      strokeWidth="43"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      focusable="false"
    >
      <path d="M40 256Q256 -7 472 256Q256 519 40 256Z" />
      <circle cx="256" cy="256" r="60" />
    </svg>
  );
}

/**
 * The full badge: teal ground, cream eye, rounded square.
 *
 * `radius` is a fraction of the box rather than a pixel value, so the corner
 * stays proportionally the same from the 28px sidebar mark to the 80px one on
 * the login panel — a fixed radius makes the small one look square and the
 * large one look like a button.
 */
export function LookOutMark({ size = 32, radius = 0.22, className = "", style = {} }) {
  return (
    <div
      className={`flex items-center justify-center flex-shrink-0 ${className}`}
      style={{
        width: size,
        height: size,
        borderRadius: size * radius,
        background: BRAND_TEAL,
        ...style,
      }}
      role="img"
      aria-label="LookOut"
    >
      {/* The glyph is drawn to fill its own box edge to edge, so the inset
          here is what gives the badge its breathing room. */}
      <LookOutEye size={size * 0.68} color={BRAND_CREAM} />
    </div>
  );
}
