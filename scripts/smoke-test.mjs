#!/usr/bin/env node
import { parseArgs } from "node:util";

function origin(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
      url.pathname !== "/" || url.search || url.hash) {
    throw new Error("Supply an HTTP or HTTPS origin, for example http://127.0.0.1:8000");
  }
  return url.origin;
}

async function main() {
  const { values } = parseArgs({ options: {
    "api-url": { type: "string", default: "http://127.0.0.1:8000" },
    "web-url": { type: "string", default: "http://127.0.0.1:3000" },
    "timeout-ms": { type: "string", default: "5000" },
    "api-only": { type: "boolean" },
    "web-only": { type: "boolean" },
  } });
  if (values["api-only"] && values["web-only"]) throw new Error("Choose one of --api-only or --web-only.");
  const timeout = Number(values["timeout-ms"]);
  if (!Number.isSafeInteger(timeout) || timeout < 1 || timeout > 2147483647) {
    throw new Error("--timeout-ms must be an integer between 1 and 2147483647.");
  }
  const results = [];
  let phase;
  async function check(service, base, path, { json, protectedRoute } = {}) {
    const started = performance.now();
    let passed = false;
    let detail;
    try {
      const response = await fetch(base + path, { signal: AbortSignal.timeout(timeout), redirect: "manual" });
      try {
        detail = "HTTP " + response.status;
        if (protectedRoute && phase === "phase1_vps" && response.status === 401) {
          passed = true;
          detail += " (owner sign-in required)";
        } else if (response.status === 200) {
          if (json) json(await response.json());
          passed = true;
        }
      } finally {
        if (!response.bodyUsed) await response.body?.cancel();
      }
    } catch (error) {
      detail = error.name === "TimeoutError" ? "Timed out after " + timeout + " ms" : error.message;
    }
    results.push({ service, path, passed, ms: Math.round(performance.now() - started), detail });
  }

  if (!values["web-only"]) {
    const base = origin(values["api-url"]);
    await check("API", base, "/health", { json(data) {
      if (data?.status !== "ok") throw new Error("Expected health.status = ok");
      phase = data.env;
    } });
    await check("API", base, "/ready", { json(data) {
      if (data?.status !== "ready") throw new Error("Expected ready.status = ready");
    } });
    for (const path of ["/v1/health/metrics", "/v1/me", "/v1/projects"]) {
      await check("API", base, path, { protectedRoute: true });
    }
  }
  if (!values["api-only"]) {
    const base = origin(values["web-url"]);
    for (const path of ["/", "/create", "/workers", "/settings"]) await check("Web", base, path);
    await check("Web", base, "/api/backend-status", { json(data) {
      if (typeof data?.online !== "boolean") throw new Error("Expected backend-status JSON with an online flag");
    } });
  }

  console.log("Result  Service  GET path             Time (ms)  Details");
  for (const row of results) {
    console.log((row.passed ? "PASS" : "FAIL").padEnd(8) + row.service.padEnd(9) +
      row.path.padEnd(21) + String(row.ms).padStart(9) + "  " + row.detail);
  }
  const passed = results.filter(row => row.passed).length;
  console.log("\n" + passed + "/" + results.length + " checks passed.");
  if (passed !== results.length) process.exitCode = 1;
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
