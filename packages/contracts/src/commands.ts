import type {
  CameraPreset,
  CaptionTrackConfig,
  DiagramProps,
  DiagramTemplate,
  ModelChoice,
  OverlayItem,
  TimelineDocument,
  VisualSceneItem,
} from "./timeline";

export type TimelineCommand =
  | {
      type: "trim_scene";
      scene_id: string;
      start_ms: number;
      end_ms: number;
    }
  | {
      type: "split_scene";
      scene_id: string;
      split_at_ms: number;
      new_scene_id: string;
    }
  | {
      type: "move_scene";
      scene_id: string;
      to_index: number;
    }
  | {
      type: "delete_scene";
      scene_id: string;
    }
  | {
      type: "restore_scene";
      scene: VisualSceneItem;
      index: number;
    }
  | {
      type: "tighten_pauses";
      max_gap_ms: number;
    }
  | {
      type: "edit_narration";
      scene_id: string;
      narration: string;
    }
  | {
      type: "regenerate_clip";
      scene_id: string;
      prompt?: string;
      model?: ModelChoice;
      camera?: CameraPreset;
      seed?: number;
    }
  | {
      type: "approve_scene_qa";
      scene_id: string;
    }
  | {
      type: "convert_scene_to_diagram";
      scene_id: string;
      template: DiagramTemplate;
      props: DiagramProps;
    }
  | {
      type: "insert_diagram";
      at_ms: number;
      duration_ms: number;
      scene_id: string;
      narration: string;
      template: DiagramTemplate;
      props: DiagramProps;
    }
  | {
      type: "add_overlay";
      overlay: OverlayItem;
    }
  | {
      type: "remove_overlay";
      overlay_id: string;
    }
  | {
      type: "update_captions";
      patch: Partial<CaptionTrackConfig>;
    }
  | {
      type: "mix_audio";
      mood?: string;
      duck_db?: number;
      volume?: number;
      voice_speed?: number;
    };

export interface CommandResult {
  doc: TimelineDocument;
  inverse: TimelineCommand;
  requiresGpu: boolean;
  requiresCpuAudio: boolean;
  estCuCost: number;
}

function cloneDoc(doc: TimelineDocument): TimelineDocument {
  return JSON.parse(JSON.stringify(doc)) as TimelineDocument;
}

function reflowSequential(scenes: VisualSceneItem[], gapMs = 0): VisualSceneItem[] {
  let cursor = 0;
  return scenes.map((s) => {
    const duration = Math.max(1000, s.end_ms - s.start_ms);
    const updated: VisualSceneItem = {
      ...s,
      start_ms: cursor,
      end_ms: cursor + duration,
    };
    cursor += duration + gapMs;
    return updated;
  });
}

export function applyTimelineCommand(doc: TimelineDocument, cmd: TimelineCommand): CommandResult {
  const next = cloneDoc(doc);
  let requiresGpu = false;
  let requiresCpuAudio = false;
  let estCuCost = 0;
  let inverse: TimelineCommand;

  switch (cmd.type) {
    case "trim_scene": {
      const idx = next.tracks.visual.findIndex((s) => s.id === cmd.scene_id);
      if (idx === -1) throw new Error(`Scene not found: ${cmd.scene_id}`);
      const prev = next.tracks.visual[idx];
      inverse = {
        type: "trim_scene",
        scene_id: cmd.scene_id,
        start_ms: prev.start_ms,
        end_ms: prev.end_ms,
      };
      next.tracks.visual[idx] = {
        ...prev,
        start_ms: Math.max(0, cmd.start_ms),
        end_ms: Math.max(cmd.start_ms + 500, cmd.end_ms),
      };
      break;
    }

    case "split_scene": {
      const idx = next.tracks.visual.findIndex((s) => s.id === cmd.scene_id);
      if (idx === -1) throw new Error(`Scene not found: ${cmd.scene_id}`);
      const orig = next.tracks.visual[idx];
      const splitAt = Math.min(orig.end_ms - 500, Math.max(orig.start_ms + 500, cmd.split_at_ms));
      const left: VisualSceneItem = { ...orig, end_ms: splitAt };
      const right: VisualSceneItem = {
        ...JSON.parse(JSON.stringify(orig)),
        id: cmd.new_scene_id,
        start_ms: splitAt,
        end_ms: orig.end_ms,
      };
      next.tracks.visual.splice(idx, 1, left, right);
      inverse = { type: "delete_scene", scene_id: cmd.new_scene_id };
      break;
    }

    case "move_scene": {
      const fromIdx = next.tracks.visual.findIndex((s) => s.id === cmd.scene_id);
      if (fromIdx === -1) throw new Error(`Scene not found: ${cmd.scene_id}`);
      const [moved] = next.tracks.visual.splice(fromIdx, 1);
      const bounded = Math.max(0, Math.min(next.tracks.visual.length, cmd.to_index));
      next.tracks.visual.splice(bounded, 0, moved);
      next.tracks.visual = reflowSequential(next.tracks.visual);
      inverse = { type: "move_scene", scene_id: cmd.scene_id, to_index: fromIdx };
      break;
    }

    case "delete_scene": {
      const idx = next.tracks.visual.findIndex((s) => s.id === cmd.scene_id);
      if (idx === -1) throw new Error(`Scene not found: ${cmd.scene_id}`);
      const removed = next.tracks.visual[idx];
      next.tracks.visual.splice(idx, 1);
      next.tracks.visual = reflowSequential(next.tracks.visual);
      inverse = { type: "restore_scene", scene: removed, index: idx };
      break;
    }

    case "restore_scene": {
      const bounded = Math.max(0, Math.min(next.tracks.visual.length, cmd.index));
      next.tracks.visual.splice(bounded, 0, cmd.scene);
      next.tracks.visual = reflowSequential(next.tracks.visual);
      inverse = { type: "delete_scene", scene_id: cmd.scene.id };
      break;
    }

    case "tighten_pauses": {
      const first = next.tracks.visual[0];
      next.tracks.visual = reflowSequential(next.tracks.visual, Math.max(0, Math.min(300, cmd.max_gap_ms)));
      inverse = first
        ? { type: "trim_scene", scene_id: first.id, start_ms: first.start_ms, end_ms: first.end_ms }
        : { type: "tighten_pauses", max_gap_ms: 0 };
      break;
    }

    case "edit_narration": {
      const idx = next.tracks.visual.findIndex((s) => s.id === cmd.scene_id);
      if (idx === -1) throw new Error(`Scene not found: ${cmd.scene_id}`);
      const prev = next.tracks.visual[idx];
      inverse = { type: "edit_narration", scene_id: cmd.scene_id, narration: prev.narration };
      next.tracks.visual[idx] = {
        ...prev,
        narration: cmd.narration,
        status: "generating_audio",
      };
      requiresCpuAudio = true;
      break;
    }

    case "regenerate_clip": {
      const idx = next.tracks.visual.findIndex((s) => s.id === cmd.scene_id);
      if (idx === -1) throw new Error(`Scene not found: ${cmd.scene_id}`);
      const prev = next.tracks.visual[idx];
      inverse = {
        type: "regenerate_clip",
        scene_id: cmd.scene_id,
        prompt: prev.prompt?.ltx25 ?? prev.prompt?.wan22 ?? prev.prompt?.qwen_image,
        model: prev.model,
        camera: prev.camera,
        seed: prev.seed,
      };
      next.tracks.visual[idx] = {
        ...prev,
        prompt: cmd.prompt
          ? { ...prev.prompt, ltx25: cmd.prompt, wan22: cmd.prompt, qwen_image: cmd.prompt }
          : prev.prompt,
        model: cmd.model ?? prev.model ?? "ltx25_fp8",
        camera: cmd.camera ?? prev.camera ?? "slow_dolly_in",
        seed: cmd.seed ?? prev.seed ?? Math.floor(Math.random() * 1000000),
        status: "generating_keyframe",
      };
      requiresGpu = true;
      estCuCost = 0.08;
      break;
    }

    case "approve_scene_qa": {
      const idx = next.tracks.visual.findIndex((s) => s.id === cmd.scene_id);
      if (idx === -1) throw new Error(`Scene not found: ${cmd.scene_id}`);
      const prev = next.tracks.visual[idx];
      inverse = {
        type: "regenerate_clip",
        scene_id: cmd.scene_id,
        seed: prev.seed,
      };
      next.tracks.visual[idx] = {
        ...prev,
        qa: {
          score: prev.qa?.score ?? 8.5,
          attempts: prev.qa?.attempts ?? 1,
          verdict: "human_approved",
          reason: "Approved by human-on-the-loop",
        },
        status: prev.kind === "ai_video" && !prev.assets?.master ? "generating_clip" : "ready",
      };
      if (prev.kind === "ai_video" && !prev.assets?.master) {
        requiresGpu = true;
        estCuCost = 0.06;
      }
      break;
    }

    case "convert_scene_to_diagram": {
      const idx = next.tracks.visual.findIndex((s) => s.id === cmd.scene_id);
      if (idx === -1) throw new Error(`Scene not found: ${cmd.scene_id}`);
      const prev = next.tracks.visual[idx];
      inverse = { type: "restore_scene", scene: prev, index: idx };
      next.tracks.visual[idx] = {
        ...prev,
        kind: "diagram",
        template: cmd.template,
        props: cmd.props,
        qa: { score: 10, attempts: prev.qa?.attempts ?? 0, verdict: "human_approved", reason: "Native Remotion diagram" },
        status: "ready",
      };
      break;
    }

    case "insert_diagram": {
      const insertIdx = next.tracks.visual.findIndex((s) => s.start_ms >= cmd.at_ms);
      const newScene: VisualSceneItem = {
        id: cmd.scene_id,
        start_ms: cmd.at_ms,
        end_ms: cmd.at_ms + Math.max(2000, cmd.duration_ms),
        kind: "diagram",
        narration: cmd.narration,
        template: cmd.template,
        props: cmd.props,
        qa: { score: 10, attempts: 0, verdict: "passed", reason: "Native Remotion diagram" },
        status: "ready",
      };
      if (insertIdx === -1) {
        next.tracks.visual.push(newScene);
      } else {
        next.tracks.visual.splice(insertIdx, 0, newScene);
      }
      next.tracks.visual = reflowSequential(next.tracks.visual);
      inverse = { type: "delete_scene", scene_id: cmd.scene_id };
      break;
    }

    case "add_overlay": {
      next.tracks.overlays.push(cmd.overlay);
      inverse = { type: "remove_overlay", overlay_id: cmd.overlay.id };
      break;
    }

    case "remove_overlay": {
      const idx = next.tracks.overlays.findIndex((o) => o.id === cmd.overlay_id);
      if (idx === -1) throw new Error(`Overlay not found: ${cmd.overlay_id}`);
      const [removed] = next.tracks.overlays.splice(idx, 1);
      inverse = { type: "add_overlay", overlay: removed };
      break;
    }

    case "update_captions": {
      const prev = { ...next.tracks.captions };
      next.tracks.captions = { ...next.tracks.captions, ...cmd.patch };
      inverse = { type: "update_captions", patch: prev };
      break;
    }

    case "mix_audio": {
      const prevMusic = { ...next.tracks.music };
      const prevSpeed = next.voice.speed;
      if (cmd.mood !== undefined) next.tracks.music.mood = cmd.mood;
      if (cmd.duck_db !== undefined) next.tracks.music.duck_db = cmd.duck_db;
      if (cmd.volume !== undefined) next.tracks.music.volume = cmd.volume;
      if (cmd.voice_speed !== undefined) next.voice.speed = cmd.voice_speed;
      inverse = {
        type: "mix_audio",
        mood: prevMusic.mood,
        duck_db: prevMusic.duck_db,
        volume: prevMusic.volume,
        voice_speed: prevSpeed,
      };
      break;
    }
  }

  next.revision += 1;
  return { doc: next, inverse, requiresGpu, requiresCpuAudio, estCuCost };
}

export function applyCommandBatch(
  doc: TimelineDocument,
  commands: TimelineCommand[],
): { doc: TimelineDocument; inverses: TimelineCommand[]; estCuCost: number } {
  let current = doc;
  const inverses: TimelineCommand[] = [];
  let estCuCost = 0;

  for (const cmd of commands) {
    const res = applyTimelineCommand(current, cmd);
    current = res.doc;
    inverses.unshift(res.inverse);
    estCuCost += res.estCuCost;
  }

  return { doc: current, inverses, estCuCost: Number(estCuCost.toFixed(3)) };
}
