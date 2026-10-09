#!/usr/bin/env node
// Publish an EAS Update to the channel of one eas.json build profile, bundling
// with that profile's `env`.
//
// Why this exists instead of a bare `eas update --channel ...`: `eas update`
// does NOT read the `env` block of an eas.json build profile -- only `eas build`
// does. EXPO_PUBLIC_* vars are inlined into the JS bundle at build time, so an
// update published without them would bundle EXPO_PUBLIC_API_URL unset, and
// lib/api.ts then falls back to http://localhost:8000/api -- which on a phone
// means the phone itself. Every request in that update would fail, on a build
// that worked fine before the update landed. Reading the env back out of
// eas.json keeps the API URL declared exactly once, per profile.
//
// Usage: node scripts/publish-update.mjs <profile> [extra eas update flags]

import { spawn } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const [profile, ...passthrough] = process.argv.slice(2);

const easJson = JSON.parse(readFileSync(resolve(root, "eas.json"), "utf8"));
const profiles = Object.keys(easJson.build ?? {});

function fail(message) {
  console.error(`publish-update: ${message}`);
  process.exit(1);
}

if (!profile) fail(`no build profile given. Expected one of: ${profiles.join(", ")}`);

const buildProfile = easJson.build?.[profile];
if (!buildProfile) fail(`unknown build profile "${profile}". Expected one of: ${profiles.join(", ")}`);

const { channel, env = {} } = buildProfile;
if (!channel) fail(`build profile "${profile}" has no "channel", so there is nothing to publish to.`);

const args = ["eas", "update", "--channel", channel, ...passthrough];

console.log(`» npx ${args.join(" ")}`);
for (const [key, value] of Object.entries(env)) console.log(`  ${key}=${value}`);

// Windows: npx resolves to npx.cmd, and Node refuses to spawn .cmd without a
// shell -- which in turn means quoting args ourselves (e.g. -m "a message").
const useShell = process.platform === "win32";
const quote = (arg) => (/[\s"]/.test(arg) ? `"${arg.replace(/"/g, '\\"')}"` : arg);

const child = spawn(useShell ? args.map(quote).join(" ") : "npx", useShell ? [] : args, {
  cwd: root,
  stdio: "inherit",
  shell: useShell,
  env: { ...process.env, ...env },
});

child.on("error", (error) => fail(error.message));
child.on("exit", (code, signal) => process.exit(code ?? (signal ? 1 : 0)));
