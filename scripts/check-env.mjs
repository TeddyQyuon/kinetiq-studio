#!/usr/bin/env node
/**
 * Kinetiq Studio — Phase 1 VPS Production Environment Preflight Validator
 *
 * Validates required secrets, Caddy domains, URL alignment, and OAuth/DB
 * settings before deploying `docker-compose.yml` + `infra/docker-compose.vps.yml`.
 * Never prints secret values in human or JSON output.
 */

import fs from "node:fs";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { pathToFileURL } from "node:url";

const PLACEHOLDER_MARKERS = [
  "dev-only",
  "change-me",
  "change-before-prod",
  "changeme",
  "placeholder",
  "example",
  "your-",
  "replace-me",
  "default-secret",
  "secret123",
];

const WEAK_POSTGRES_PASSWORDS = new Set(["postgres", "password", "admin", "kinetiq", "12345678"]);

const RESERVED_EXACT_DOMAINS = new Set([
  "localhost",
  "127.0.0.1",
  "0.0.0.0",
  "example.com",
  "example.org",
  "example.net",
  "yourdomain.com",
]);

const RESERVED_SUFFIXES = [
  ".local",
  ".localhost",
  ".example",
  ".example.com",
  ".example.org",
  ".example.net",
  ".invalid",
  ".test",
  ".yourdomain",
  ".yourdomain.com",
];

const FQDN_PATTERN = /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$/;

function hasPlaceholderMarker(value) {
  const lower = value.toLowerCase();
  return PLACEHOLDER_MARKERS.some((marker) => lower.includes(marker));
}

function uniqueCharCount(value) {
  return new Set(value).size;
}

/**
 * Parse `.env` file contents without executing shell commands.
 * Supports CRLF/LF, blank lines, `#` comments, `export KEY=VALUE`,
 * and single/double-quoted values with inline comments outside quotes.
 */
export function parseEnvContent(text) {
  const result = {};
  const lines = text.split(/\r?\n/);

  for (const rawLine of lines) {
    const trimmed = rawLine.trim();
    if (!trimmed || trimmed.startsWith("#")) {
      continue;
    }

    const normalized = trimmed.startsWith("export ") ? trimmed.slice(7).trimStart() : trimmed;
    const eqIndex = normalized.indexOf("=");
    if (eqIndex <= 0) {
      continue;
    }

    const key = normalized.slice(0, eqIndex).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      continue;
    }

    let rawVal = normalized.slice(eqIndex + 1).trim();
    if (!rawVal) {
      result[key] = "";
      continue;
    }

    const quote = rawVal[0];
    if (quote === '"' || quote === "'") {
      let parsed = "";
      let escaped = false;
      for (let i = 1; i < rawVal.length; i += 1) {
        const ch = rawVal[i];
        if (quote === '"' && escaped) {
          if (ch === "n") parsed += "\n";
          else if (ch === "r") parsed += "\r";
          else if (ch === "t") parsed += "\t";
          else parsed += ch;
          escaped = false;
          continue;
        }
        if (quote === '"' && ch === "\\") {
          escaped = true;
          continue;
        }
        if (ch === quote) {
          break;
        }
        parsed += ch;
      }
      result[key] = parsed;
      continue;
    }

    // Unquoted value: strip inline comment preceded by whitespace
    const commentMatch = rawVal.match(/\s+#/);
    if (commentMatch && commentMatch.index !== undefined) {
      rawVal = rawVal.slice(0, commentMatch.index).trimEnd();
    }
    result[key] = rawVal;
  }

  return result;
}

export function loadEnvFile(filePath) {
  const resolved = path.resolve(filePath);
  const content = fs.readFileSync(resolved, "utf8");
  return parseEnvContent(content);
}

function validateSecret(errors, env, key, minLength, minUniqueChars) {
  const val = (env[key] ?? "").trim();
  if (!val) {
    errors.push(`${key} is required and cannot be empty.`);
    return null;
  }
  if (val.length < minLength) {
    errors.push(`${key} must be at least ${minLength} characters long (got ${val.length}).`);
  }
  if (uniqueCharCount(val) < minUniqueChars) {
    errors.push(`${key} has insufficient entropy (must contain at least ${minUniqueChars} distinct characters).`);
  }
  if (hasPlaceholderMarker(val)) {
    errors.push(`${key} appears to use a development or placeholder value.`);
  }
  return val;
}

function validateDomain(errors, env, key) {
  const raw = (env[key] ?? "").trim();
  if (!raw) {
    errors.push(`${key} is required for Caddy automatic HTTPS.`);
    return null;
  }
  if (/^https?:\/\//i.test(raw) || raw.includes("/") || raw.includes(":") || /\s/.test(raw)) {
    errors.push(`${key} must be a bare FQDN hostname without scheme, port, path, or whitespace.`);
    return null;
  }
  const lower = raw.toLowerCase();
  const labels = lower.split(".");
  const tld = labels[labels.length - 1] ?? "";
  if (!FQDN_PATTERN.test(lower) || !/^[a-z]{2,}$/.test(tld)) {
    errors.push(`${key} ("${raw}") is not a valid FQDN hostname.`);
    return null;
  }
  if (RESERVED_EXACT_DOMAINS.has(lower) || RESERVED_SUFFIXES.some((suffix) => lower.endsWith(suffix))) {
    errors.push(`${key} ("${raw}") cannot use a local, reserved, or placeholder domain.`);
    return null;
  }
  return lower;
}

function parseHttpsUrl(errors, env, key) {
  const raw = (env[key] ?? "").trim();
  if (!raw) {
    errors.push(`${key} is required and must use https:// in Phase 1 VPS mode.`);
    return null;
  }
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    errors.push(`${key} is not a valid URL.`);
    return null;
  }
  if (parsed.protocol !== "https:") {
    errors.push(`${key} must use https:// in Phase 1 VPS mode.`);
  }
  if (parsed.username || parsed.password) {
    errors.push(`${key} must not contain embedded credentials.`);
  }
  if (parsed.pathname && parsed.pathname !== "/") {
    errors.push(`${key} must not include a sub-path (expected origin root only).`);
  }
  if (parsed.search || parsed.hash) {
    errors.push(`${key} must not include query strings or URL fragments.`);
  }
  if (parsed.port) {
    errors.push(`${key} must use the standard HTTPS port (no explicit port).`);
  }
  return parsed;
}

function crossSiteCookieWarning(web, api) {
  if (web.hostname === api.hostname) return null;
  const advice = "Set COOKIE_SAMESITE=none for cross-site Studio sign-in and API requests.";
  const lastTwoLabels = (host) => host.split(".").slice(-2).join(".");
  if (lastTwoLabels(web.hostname) !== lastTwoLabels(api.hostname)) {
    return `PUBLIC_WEB_URL and PUBLIC_API_URL are on different sites. ${advice}`;
  }
  // Sibling hosts can still be separate sites, e.g. tenants under vercel.app or co.uk.
  const psl = spawnSync("psl", ["--batch", "--print-reg-domain", "--", web.hostname, api.hostname],
    { encoding: "utf8", timeout: 2000, windowsHide: true });
  if (psl.status === 0) {
    const [webSite, apiSite] = psl.stdout.trim().split(/\s+/);
    if (webSite && apiSite && webSite !== "(null)" && apiSite !== "(null)") {
      return webSite === apiSite ? null : advice;
    }
  }
  return `Verify whether ${web.hostname} and ${api.hostname} share a registrable domain. ${advice} Same-site hosts can keep lax.`;
}

/**
 * Validate a map of environment variables for Phase 1 VPS deployment.
 * Returns `{ ok, errors, warnings, domains }`. Never includes secret values.
 */
export function validateProductionEnv(rawEnv, { apiOnly = false } = {}) {
  const env = rawEnv ?? {};
  const errors = [];
  const warnings = [];

  // 1. Core cryptographic secrets
  const masterKey = validateSecret(errors, env, "ENCRYPTION_MASTER_KEY", 32, 8);
  const sessionSecret = validateSecret(errors, env, "SESSION_SECRET", 32, 8);
  const mediaSecret = validateSecret(errors, env, "MEDIA_SIGNING_SECRET", 32, 8);

  const presentSecrets = [
    ["ENCRYPTION_MASTER_KEY", masterKey],
    ["SESSION_SECRET", sessionSecret],
    ["MEDIA_SIGNING_SECRET", mediaSecret],
  ].filter(([, val]) => Boolean(val));

  if (new Set(presentSecrets.map(([, val]) => val)).size !== presentSecrets.length) {
    errors.push(
      "ENCRYPTION_MASTER_KEY, SESSION_SECRET, and MEDIA_SIGNING_SECRET must each have distinct values."
    );
  }

  // 2. Database password
  const pgPass = (env.POSTGRES_PASSWORD ?? "").trim();
  if (!pgPass) {
    errors.push("POSTGRES_PASSWORD is required and cannot be empty.");
  } else {
    if (pgPass.length < 16) {
      errors.push(`POSTGRES_PASSWORD must be at least 16 characters long (got ${pgPass.length}).`);
    }
    if (uniqueCharCount(pgPass) < 6) {
      errors.push("POSTGRES_PASSWORD must contain at least 6 distinct characters.");
    }
    if (WEAK_POSTGRES_PASSWORDS.has(pgPass.toLowerCase()) || hasPlaceholderMarker(pgPass)) {
      errors.push("POSTGRES_PASSWORD uses a weak or placeholder value.");
    }
  }

  // 3. Caddy domains
  const studioDomain = apiOnly ? null : validateDomain(errors, env, "STUDIO_DOMAIN");
  const apiDomain = validateDomain(errors, env, "API_DOMAIN");
  if (studioDomain && apiDomain && studioDomain === apiDomain) {
    errors.push("STUDIO_DOMAIN and API_DOMAIN must be distinct hostnames.");
  }

  // 4. Phase 1 runtime & database flags
  const appEnv = (env.APP_ENV ?? "").trim();
  if (appEnv !== "phase1_vps") {
    errors.push(`APP_ENV must be set to "phase1_vps" for Phase 1 deployment (got "${appEnv || "<empty>"}").`);
  }

  const embeddedOrch = (env.RUN_EMBEDDED_ORCHESTRATOR ?? "").trim();
  if (embeddedOrch && embeddedOrch.toLowerCase() !== "false") {
    errors.push(
      `RUN_EMBEDDED_ORCHESTRATOR must be "false" in Phase 1 VPS mode (got "${embeddedOrch}").`
    );
  }

  const dbUrl = (env.DATABASE_URL ?? "").trim();
  if (dbUrl && !dbUrl.startsWith("postgresql")) {
    errors.push("DATABASE_URL must use PostgreSQL (postgresql+psycopg://...) in Phase 1 VPS mode, not SQLite.");
  }

  // 5. Public URLs & CORS alignment
  const publicApiUrl = parseHttpsUrl(errors, env, "PUBLIC_API_URL");
  const nextPublicApiUrl = apiOnly ? null : parseHttpsUrl(errors, env, "NEXT_PUBLIC_API_URL");
  const publicWebUrl = parseHttpsUrl(errors, env, "PUBLIC_WEB_URL");
  if (apiOnly && publicWebUrl) {
    validateDomain(errors, { PUBLIC_WEB_URL: publicWebUrl.hostname }, "PUBLIC_WEB_URL");
  }

  if (env.COOKIE_SAMESITE !== undefined && !["lax", "none"].includes(env.COOKIE_SAMESITE)) {
    errors.push('COOKIE_SAMESITE must be "lax" or "none".');
  } else if (publicWebUrl && publicApiUrl && env.COOKIE_SAMESITE !== "none") {
    const warning = crossSiteCookieWarning(publicWebUrl, publicApiUrl);
    if (warning) warnings.push(warning);
  }

  if (publicApiUrl && apiDomain && publicApiUrl.hostname.toLowerCase() !== apiDomain) {
    errors.push(`PUBLIC_API_URL hostname ("${publicApiUrl.hostname}") must match API_DOMAIN ("${apiDomain}").`);
  }
  if (nextPublicApiUrl && apiDomain && nextPublicApiUrl.hostname.toLowerCase() !== apiDomain) {
    errors.push(
      `NEXT_PUBLIC_API_URL hostname ("${nextPublicApiUrl.hostname}") must match API_DOMAIN ("${apiDomain}").`
    );
  }
  if (publicApiUrl && nextPublicApiUrl) {
    const normApi = publicApiUrl.origin.toLowerCase();
    const normNext = nextPublicApiUrl.origin.toLowerCase();
    if (normApi !== normNext) {
      errors.push("NEXT_PUBLIC_API_URL must match PUBLIC_API_URL.");
    }
  }
  if (publicWebUrl && studioDomain && publicWebUrl.hostname.toLowerCase() !== studioDomain) {
    errors.push(`PUBLIC_WEB_URL hostname ("${publicWebUrl.hostname}") must match STUDIO_DOMAIN ("${studioDomain}").`);
  }

  const corsRaw = (env.CORS_ORIGINS ?? "").trim();
  if (!corsRaw) {
    errors.push("CORS_ORIGINS is required and must be a JSON array containing the Studio HTTPS origin.");
  } else {
    try {
      const parsedCors = JSON.parse(corsRaw);
      if (!Array.isArray(parsedCors) || !parsedCors.every((item) => typeof item === "string")) {
        errors.push("CORS_ORIGINS must be a valid JSON array of origin strings.");
      } else {
        const lowerCors = parsedCors.map((item) => item.trim().toLowerCase());
        if (lowerCors.some((item) => item === "*" || item.includes("localhost") || item.includes("127.0.0.1"))) {
          errors.push('CORS_ORIGINS must not contain "*", "localhost", or "127.0.0.1" in production.');
        }
        if (publicWebUrl) {
          const expectedOrigin = publicWebUrl.origin;
          if (!lowerCors.includes(expectedOrigin)) {
            errors.push(`CORS_ORIGINS must include "${expectedOrigin}".`);
          }
        }
      }
    } catch {
      errors.push("CORS_ORIGINS must be valid JSON (for example: [\"https://studio.yourdomain.com\"]).");
    }
  }

  // 6. Storage & Google OAuth validation
  const storageProvider = (env.STORAGE_PROVIDER ?? "local").trim();
  if (storageProvider !== "drive" && storageProvider !== "local") {
    errors.push(`STORAGE_PROVIDER must be "drive" or "local" (got "${storageProvider}").`);
  } else if (storageProvider === "drive") {
    const clientId = (env.GOOGLE_CLIENT_ID ?? "").trim();
    const clientSecret = (env.GOOGLE_CLIENT_SECRET ?? "").trim();
    const redirectUri = (env.GOOGLE_REDIRECT_URI ?? "").trim();

    if (!clientId || hasPlaceholderMarker(clientId)) {
      errors.push("GOOGLE_CLIENT_ID is required (non-placeholder) when STORAGE_PROVIDER=drive.");
    }
    if (!clientSecret || hasPlaceholderMarker(clientSecret)) {
      errors.push("GOOGLE_CLIENT_SECRET is required (non-placeholder) when STORAGE_PROVIDER=drive.");
    }
    if (apiDomain) {
      const expectedRedirect = `https://${apiDomain}/v1/auth/google/callback`;
      if (redirectUri !== expectedRedirect) {
        errors.push(`GOOGLE_REDIRECT_URI must equal "${expectedRedirect}" when STORAGE_PROVIDER=drive.`);
      }
    } else if (!redirectUri.startsWith("https://")) {
      errors.push("GOOGLE_REDIRECT_URI must use https:// when STORAGE_PROVIDER=drive.");
    }
  } else {
    warnings.push(
      'STORAGE_PROVIDER is "local"; "drive" is recommended on Phase 1 VPS for durable Google Drive Vault backups.'
    );
  }

  // 7. Advisory warnings (non-blocking)
  if (!(env.MCP_ACCESS_TOKEN ?? "").trim()) {
    warnings.push("MCP_ACCESS_TOKEN is empty; set a strong token before exposing /mcp.");
  }
  if (!(env.GEMINI_API_KEY ?? "").trim() && !(env.OPENAI_API_KEY ?? "").trim()) {
    warnings.push(
      "Both GEMINI_API_KEY and OPENAI_API_KEY are empty; offline deterministic planner and heuristic QA will be used."
    );
  }
  if (!(env.OWNER_GOOGLE_EMAIL ?? "").trim()) {
    warnings.push(
      "OWNER_GOOGLE_EMAIL is empty; set it to lock VPS owner OAuth sign-in to a single Google account."
    );
  }

  return {
    ok: errors.length === 0,
    errors,
    warnings,
    domains: {
      studio: apiOnly ? publicWebUrl?.hostname ?? null : studioDomain,
      api: apiDomain,
    },
  };
}

function parseArgs(argv) {
  const opts = {
    envFile: null,
    fromEnv: false,
    apiOnly: false,
    json: false,
    help: false,
    unknown: null,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === "--") {
      continue;
    }
    if (arg === "-h" || arg === "--help") {
      opts.help = true;
    } else if (arg === "--json") {
      opts.json = true;
    } else if (arg === "--api-only") {
      opts.apiOnly = true;
    } else if (arg === "--from-env") {
      opts.fromEnv = true;
    } else if (arg === "-f" || arg === "--file" || arg === "--env-file") {
      const next = argv[i + 1];
      if (!next) {
        opts.unknown = `${arg} requires a path argument`;
        return opts;
      }
      opts.envFile = next;
      i += 1;
    } else if (arg.startsWith("--file=")) {
      opts.envFile = arg.slice("--file=".length);
    } else if (arg.startsWith("--env-file=")) {
      opts.envFile = arg.slice("--env-file=".length);
    } else if (!arg.startsWith("-") && !opts.envFile) {
      opts.envFile = arg;
    } else {
      opts.unknown = `Unknown argument: ${arg}`;
      return opts;
    }
  }

  if (opts.fromEnv && opts.envFile) {
    opts.unknown = "--from-env and --env-file cannot be combined";
  }
  return opts;
}

function printUsage() {
  console.log(`Kinetiq Studio — Phase 1 VPS Environment Preflight Validator

Usage:
  node scripts/check-env.mjs [options]

Options:
  -f, --file <path>   Validate strictly from the given .env file
  --from-env          Validate process environment variables only
  --api-only          Use PUBLIC_WEB_URL for externally hosted Studio
  --json              Output machine-readable JSON (never prints secrets)
  -h, --help          Show this help message

Exit codes:
  0  All production checks passed
  1  One or more production validation errors found
  2  Usage error or missing/unreadable env file`);
}

function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (opts.help) {
    printUsage();
    process.exit(0);
  }
  if (opts.unknown) {
    console.error(`Error: ${opts.unknown}`);
    process.exit(2);
  }

  let targetFile = null;
  let envMap = {};

  if (opts.fromEnv) {
    envMap = { ...process.env };
  } else if (opts.envFile) {
    targetFile = path.resolve(opts.envFile);
    try {
      envMap = loadEnvFile(targetFile);
    } catch (err) {
      if (opts.json) {
        console.log(
          JSON.stringify({
            ok: false,
            envFile: targetFile,
            errors: [`Unable to read env file "${targetFile}": ${err.message}`],
            warnings: [],
            domains: { studio: null, api: null },
          })
        );
      } else {
        console.error(`Error: Unable to read env file "${targetFile}": ${err.message}`);
      }
      process.exit(2);
    }
  } else {
    targetFile = path.resolve(process.cwd(), ".env");
    try {
      envMap = loadEnvFile(targetFile);
    } catch (err) {
      if (opts.json) {
        console.log(
          JSON.stringify({
            ok: false,
            envFile: targetFile,
            errors: [`Unable to read default env file "${targetFile}": ${err.message}`],
            warnings: [],
            domains: { studio: null, api: null },
          })
        );
      } else {
        console.error(`Error: Unable to read default env file "${targetFile}": ${err.message}`);
      }
      process.exit(2);
    }
    // Overlay non-empty process.env entries, matching Docker Compose precedence
    for (const [k, v] of Object.entries(process.env)) {
      if (typeof v === "string" && v !== "") {
        envMap[k] = v;
      }
    }
  }

  const report = validateProductionEnv(envMap, { apiOnly: opts.apiOnly });

  if (opts.json) {
    console.log(
      JSON.stringify(
        {
          ok: report.ok,
          envFile: targetFile,
          errors: report.errors,
          warnings: report.warnings,
          domains: report.domains,
        },
        null,
        2
      )
    );
  } else {
    const sourceLabel = targetFile ?? "process.env";
    console.log(`Kinetiq Studio VPS Env Preflight (${sourceLabel})`);
    if (report.domains.studio && report.domains.api) {
      console.log(`Domains: studio=${report.domains.studio} | api=${report.domains.api}`);
    }
    if (report.warnings.length > 0) {
      console.log("\nWarnings:");
      for (const w of report.warnings) {
        console.log(`  [WARN] ${w}`);
      }
    }
    if (report.errors.length > 0) {
      console.error("\nErrors:");
      for (const e of report.errors) {
        console.error(`  [FAIL] ${e}`);
      }
      console.error(`\nPreflight failed with ${report.errors.length} error(s).`);
    } else {
      console.log("\n[OK] All Phase 1 VPS environment checks passed.");
    }
  }

  process.exit(report.ok ? 0 : 1);
}

const isEntrypoint =
  Boolean(process.argv[1]) && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (isEntrypoint) {
  main();
}
