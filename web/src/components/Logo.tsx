import { brand } from "../brand";

/** The STRIKTEK mark: a targeting reticle (aim) with a rising price line landing on the centre (strike). */
export function LogoMark({ size = 28, className = "" }: { size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" className={className} role="img" aria-label={brand.name}>
      <g fill="none" stroke="#f5b301" strokeWidth="4" strokeLinecap="round">
        <circle cx="32" cy="32" r="20" />
        <path d="M32 4v14M32 46v14M4 32h14M46 32h14" />
      </g>
      <path d="M21 38l8-8 5 5 11-12" fill="none" stroke="#e3e8ef" strokeWidth="4.5" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="45" cy="23" r="3.6" fill="#e3e8ef" />
    </svg>
  );
}

/** Mark plus the wordmark in capitals; the last three letters (TEK) take the brand amber. */
export function Logo({ size = 28 }: { size?: number }) {
  const name = brand.name; // already upper case
  const split = Math.max(name.length - 3, 1);
  return (
    <span className="flex items-center gap-2.5">
      <LogoMark size={size} />
      <span className="text-[15px] font-bold tracking-[0.18em] text-ink-100">
        {name.slice(0, split)}
        <span className="text-accent">{name.slice(split)}</span>
      </span>
    </span>
  );
}
