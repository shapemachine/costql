/** Python-compatible `round(x, ndigits)` for the digit counts the engine uses
 * (3 and 4). Both Python and `toFixed` produce the CORRECTLY-ROUNDED decimal
 * rendering of the binary double, and an exact decimal tie at >=1 fractional
 * digit is impossible for a double (it would need a factor of 5 in the
 * denominator), so no tie-breaking rule can ever disagree. */
export function pyRound(x: number, ndigits: number): number {
  if (!Number.isFinite(x)) return x;
  return Number(x.toFixed(ndigits));
}
