import type { WorkerRecord } from "@kinetiq/contracts";

export function isOnlineGpuWorker(worker: WorkerRecord) {
  return worker.status !== "offline" && !["media", "render", "media_local", "render_local"].includes(worker.profile);
}

export function hasLowVram(worker: WorkerRecord) {
  return worker.vram_gb !== null && worker.vram_gb > 0 && worker.vram_gb < 20;
}
