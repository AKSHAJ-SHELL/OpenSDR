/**
 * Dashboard password verification against DASHBOARD_PASSWORD_HASH.
 *
 * Hash format: `scrypt$<saltHex>$<hashHex>` (scrypt with N=16384, r=8, p=1,
 * keylen=64). Generate one with `node scripts/hash-password.mjs <password>`.
 * Server-only (Node.js crypto).
 */
import { scryptSync, timingSafeEqual } from "node:crypto";

const KEYLEN = 64;

export function verifyPassword(plain: string): boolean {
  const stored = process.env.DASHBOARD_PASSWORD_HASH;
  if (!stored) {
    throw new Error("DASHBOARD_PASSWORD_HASH is not set — refusing to authenticate.");
  }
  const parts = stored.split("$");
  if (parts.length !== 3 || parts[0] !== "scrypt") {
    throw new Error("DASHBOARD_PASSWORD_HASH is malformed (expected scrypt$salt$hash).");
  }
  const [, saltHex, hashHex] = parts;
  const expected = Buffer.from(hashHex, "hex");
  let derived: Buffer;
  try {
    derived = scryptSync(plain, Buffer.from(saltHex, "hex"), KEYLEN);
  } catch {
    return false;
  }
  return expected.length === derived.length && timingSafeEqual(expected, derived);
}
