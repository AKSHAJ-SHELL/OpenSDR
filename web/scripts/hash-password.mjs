#!/usr/bin/env node
/**
 * Generate a DASHBOARD_PASSWORD_HASH value for the dashboard login.
 *
 *   node scripts/hash-password.mjs 'my-strong-password'
 *
 * Paste the printed `scrypt$...` string into web/.env.local as
 * DASHBOARD_PASSWORD_HASH. The plaintext is never stored.
 */
import { randomBytes, scryptSync } from "node:crypto";

const password = process.argv[2];
if (!password) {
  console.error("usage: node scripts/hash-password.mjs '<password>'");
  process.exit(1);
}

const salt = randomBytes(16);
const hash = scryptSync(password, salt, 64);
console.log(`scrypt$${salt.toString("hex")}$${hash.toString("hex")}`);
