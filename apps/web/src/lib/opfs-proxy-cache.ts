import type { TimelineDocument } from "@kinetiq/contracts";

const CACHE_DIR_NAME = "kinetiq-proxy-cache";
const MAX_CACHE_BYTES = 512 * 1024 * 1024; // 512 MiB soft cap

type OpfsDirectoryWithEntries = FileSystemDirectoryHandle & {
  entries?: () => AsyncIterableIterator<[string, FileSystemHandle]>;
};

export interface OpfsCacheStats {
  supported: boolean;
  entryCount: number;
  totalBytes: number;
}

const blobUrlMap = new Map<string, string>();
const inFlightFetches = new Map<string, Promise<string>>();

export function isOpfsSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    Boolean(navigator.storage) &&
    typeof navigator.storage.getDirectory === "function"
  );
}

function sanitizeAssetKey(assetId: string): string {
  return assetId.replace(/[^a-zA-Z0-9._-]/g, "_");
}

function getCacheFilename(assetId: string): string {
  return `${sanitizeAssetKey(assetId)}.mp4`;
}

async function getCacheDirectory(create: boolean): Promise<OpfsDirectoryWithEntries | null> {
  if (!isOpfsSupported()) return null;
  try {
    const root = await navigator.storage.getDirectory();
    const dir = await root.getDirectoryHandle(CACHE_DIR_NAME, { create });
    return dir as OpfsDirectoryWithEntries;
  } catch {
    return null;
  }
}

async function readOpfsEntryAsBlobUrl(assetId: string): Promise<string | null> {
  const existing = blobUrlMap.get(assetId);
  if (existing) return existing;

  const dir = await getCacheDirectory(false);
  if (!dir) return null;

  try {
    const handle = await dir.getFileHandle(getCacheFilename(assetId));
    const file = await handle.getFile();
    if (file.size <= 0) return null;
    const blobUrl = URL.createObjectURL(file);
    blobUrlMap.set(assetId, blobUrl);
    return blobUrl;
  } catch {
    return null;
  }
}

async function enforceSoftQuota(dir: OpfsDirectoryWithEntries, protectFilename: string): Promise<void> {
  if (typeof dir.entries !== "function") return;
  try {
    const items: Array<{ name: string; size: number; lastModified: number }> = [];
    let total = 0;

    for await (const [name, handle] of dir.entries()) {
      if (handle.kind !== "file") continue;
      const file = await (handle as FileSystemFileHandle).getFile();
      items.push({ name, size: file.size, lastModified: file.lastModified });
      total += file.size;
    }

    if (total <= MAX_CACHE_BYTES) return;

    items.sort((a, b) => a.lastModified - b.lastModified);
    for (const item of items) {
      if (total <= MAX_CACHE_BYTES) break;
      if (item.name === protectFilename) continue;
      await dir.removeEntry(item.name);
      total -= item.size;
      const assetId = item.name.replace(/\.mp4$/, "");
      const url = blobUrlMap.get(assetId);
      if (url) {
        URL.revokeObjectURL(url);
        blobUrlMap.delete(assetId);
      }
    }
  } catch {
    // Ignore quota eviction failures
  }
}

export async function getCachedProxyBlobUrl(assetId: string): Promise<string | null> {
  if (!assetId) return null;
  return readOpfsEntryAsBlobUrl(assetId);
}

export async function cacheProxyClip(
  assetId: string,
  remoteUrl: string,
  signal?: AbortSignal,
): Promise<string> {
  if (!assetId || !remoteUrl || remoteUrl.startsWith("mock://")) {
    return remoteUrl;
  }

  const cached = await readOpfsEntryAsBlobUrl(assetId);
  if (cached) return cached;

  const inflight = inFlightFetches.get(assetId);
  if (inflight) return inflight;

  const task = (async () => {
    try {
      const response = await fetch(remoteUrl, { signal });
      if (!response.ok) {
        return remoteUrl;
      }
      const blob = await response.blob();
      if (blob.size <= 0) {
        return remoteUrl;
      }

      const dir = await getCacheDirectory(true);
      const filename = getCacheFilename(assetId);
      if (dir) {
        try {
          const fileHandle = await dir.getFileHandle(filename, {
            create: true,
          });
          if (typeof fileHandle.createWritable === "function") {
            const writable = await fileHandle.createWritable();
            await writable.write(blob);
            await writable.close();
            await enforceSoftQuota(dir, filename);
          }
        } catch {
          // Fall back to in-memory Blob URL if OPFS write fails
        }
      }

      const blobUrl = URL.createObjectURL(blob);
      blobUrlMap.set(assetId, blobUrl);
      return blobUrl;
    } catch {
      return remoteUrl;
    } finally {
      inFlightFetches.delete(assetId);
    }
  })();

  inFlightFetches.set(assetId, task);
  return task;
}

export async function warmProjectProxyCache(
  doc: TimelineDocument,
  mediaUrls: Record<string, string>,
  opts?: {
    onAssetCached?: (assetId: string, blobUrl: string) => void;
    signal?: AbortSignal;
  },
): Promise<Record<string, string>> {
  const result: Record<string, string> = {};
  const tasks: Array<Promise<void>> = [];

  for (const scene of doc.tracks.visual) {
    const proxyId = scene.assets?.proxy;
    if (!proxyId) continue;

    const remoteUrl = mediaUrls[proxyId];
    if (!remoteUrl || remoteUrl.startsWith("mock://")) continue;

    tasks.push(
      cacheProxyClip(proxyId, remoteUrl, opts?.signal).then((resolvedUrl) => {
        result[proxyId] = resolvedUrl;
        if (resolvedUrl !== remoteUrl) {
          opts?.onAssetCached?.(proxyId, resolvedUrl);
        }
      }),
    );
  }

  await Promise.allSettled(tasks);
  return result;
}

export async function getOpfsCacheStats(): Promise<OpfsCacheStats> {
  const supported = isOpfsSupported();
  if (!supported) {
    return { supported: false, entryCount: 0, totalBytes: 0 };
  }

  const dir = await getCacheDirectory(false);
  if (!dir || typeof dir.entries !== "function") {
    return { supported: true, entryCount: blobUrlMap.size, totalBytes: 0 };
  }

  try {
    let entryCount = 0;
    let totalBytes = 0;
    for await (const [, handle] of dir.entries()) {
      if (handle.kind !== "file") continue;
      const file = await (handle as FileSystemFileHandle).getFile();
      entryCount += 1;
      totalBytes += file.size;
    }
    return { supported: true, entryCount, totalBytes };
  } catch {
    return { supported: true, entryCount: blobUrlMap.size, totalBytes: 0 };
  }
}

export async function evictCachedProxy(assetId: string): Promise<void> {
  const existing = blobUrlMap.get(assetId);
  if (existing) {
    URL.revokeObjectURL(existing);
    blobUrlMap.delete(assetId);
  }
  const dir = await getCacheDirectory(false);
  if (!dir) return;
  try {
    await dir.removeEntry(getCacheFilename(assetId));
  } catch {
    // Entry may not exist
  }
}

export async function clearOpfsProxyCache(): Promise<void> {
  for (const url of blobUrlMap.values()) {
    URL.revokeObjectURL(url);
  }
  blobUrlMap.clear();

  const dir = await getCacheDirectory(false);
  if (!dir || typeof dir.entries !== "function") return;
  try {
    const names: string[] = [];
    for await (const [name] of dir.entries()) {
      names.push(name);
    }
    for (const name of names) {
      await dir.removeEntry(name);
    }
  } catch {
    // Ignore cleanup errors
  }
}