import { useId, useState } from "react";
import type { MusicPreset } from "@/lib/api-client";
import type { CaptionTrackConfig, OverlayItem, TimelineCommand, TimelineDocument, VisualSceneItem } from "@kinetiq/contracts";

export const inspectorButton = "rounded border border-zinc-700 bg-zinc-900 px-2.5 py-1.5 text-xs hover:border-amber-400 focus-visible:outline-amber-400 disabled:opacity-40";
const field = "mt-1 block w-full rounded border border-zinc-700 bg-zinc-900 px-2 py-1.5 text-zinc-100";
const presets = { ambient_pulse: "Ambient pulse", warm_pad: "Warm pad", documentary_minimal: "Documentary minimal" } as const;


interface RangeControlProps {
  label: string;
  value: number;
  unit: string;
  min: number;
  max: number;
  step: number;
  onCommit: (value: number) => void;
}

function RangeControl({ label, value, unit, min, max, step, onCommit }: RangeControlProps) {
  const id = useId();
  const [draft, setDraft] = useState<number | null>(null);
  const commit = (input: HTMLInputElement) => {
    const next = input.valueAsNumber;
    setDraft(null);
    if (next !== value) onCommit(next);
  };
  return (
    <div>
      <div className="flex justify-between">
        <label htmlFor={id}>{label}</label><output htmlFor={id}>{draft ?? value}{unit}</output>
      </div>
      <input id={id} type="range" className="w-full accent-amber-400" min={min} max={max} step={step}
        value={draft ?? value} onChange={(e) => setDraft(e.currentTarget.valueAsNumber)}
        onPointerUp={(e) => commit(e.currentTarget)} onKeyUp={(e) => commit(e.currentTarget)}
        onBlur={(e) => { if (draft !== null) commit(e.currentTarget); }} />
    </div>
  );
}

export function AudioInspector({ doc, command, changeVoice, setMusic, removeMusic }: {
  doc: TimelineDocument;
  command: (cmd: TimelineCommand) => void;
  changeVoice: (voice: string) => void;
  setMusic: (source: File | MusicPreset) => void;
  removeMusic: () => void;
}) {
  const { music } = doc.tracks;
  const voices = Array.from(new Set(["am_michael", "af_heart", "af_bella", "bm_george", "am_adam", doc.voice.voice_id]));
  return (
    <div className="space-y-4 p-3 text-xs text-zinc-300">
      <label className="block">Kokoro voice
        <select className={field} value={doc.voice.voice_id} onChange={(e) => changeVoice(e.target.value)}>
          {voices.map((voice) => <option key={voice}>{voice}</option>)}
        </select>
      </label>
      <RangeControl label="Voice speed" unit="×" min={0.85} max={1.25} step={0.01} value={doc.voice.speed}
        onCommit={(voice_speed) => command({ type: "mix_audio", voice_speed })} />
      <fieldset className="space-y-2 border-t border-zinc-800 pt-3">
        <legend className="font-semibold text-zinc-200">Background music</legend>
        {music.asset && <p className="text-amber-200">Current track · {music.mood.replaceAll("_", " ")}</p>}
        <label className="block">Upload MP3 or WAV
          <input className="mt-1 block max-w-full" type="file" accept=".mp3,.wav" onChange={(e) => {
            const file = e.currentTarget.files?.[0];
            e.currentTarget.value = "";
            if (file) setMusic(file);
          }} />
        </label>
        <div className="flex flex-wrap gap-1.5">
          {(Object.keys(presets) as MusicPreset[]).map((preset) => (
            <button type="button" className={inspectorButton} key={preset} onClick={() => setMusic(preset)}>{presets[preset]}</button>
          ))}
          <button type="button" className={inspectorButton} disabled={!music.asset} onClick={removeMusic}>Remove Music</button>
        </div>
      </fieldset>
      <RangeControl label="Music volume" min={0} max={100} step={1} unit="%" value={Math.round(music.volume * 100)}
        onCommit={(volume) => command({ type: "mix_audio", volume: volume / 100 })} />
      <RangeControl label="Ducking" min={-60} max={0} step={1} unit=" dB" value={music.duck_db}
        onCommit={(duck_db) => command({ type: "mix_audio", duck_db })} />
    </div>
  );
}

export function CaptionInspector({ doc, scene, command }: {
  doc: TimelineDocument;
  scene: VisualSceneItem | null;
  command: (cmd: TimelineCommand) => void;
}) {
  const { captions, overlays } = doc.tracks;
  const [overlayText, setOverlayText] = useState("");
  const [overlayType, setOverlayType] = useState<"badge" | "chapter_title">("badge");
  const setCaptions = (patch: Partial<CaptionTrackConfig>) => command({ type: "update_captions", patch });
  return (
    <div className="space-y-3 p-3 text-xs text-zinc-300">
      <label className="flex items-center gap-2"><input type="checkbox" checked={captions.enabled}
        onChange={(e) => setCaptions({ enabled: e.target.checked })} />Enable captions</label>
      <label className="block">Caption style
        <select className={field} value={captions.style} onChange={(e) => setCaptions({ style: e.target.value as CaptionTrackConfig["style"] })}>
          <option value="kinetic_highlight">Kinetic highlight</option>
          <option value="clean_subtitle">Clean subtitle</option>
          <option value="bold_reels">Bold reels</option>
        </select>
      </label>
      <label className="block">Caption position
        <select className={field} value={captions.position} onChange={(e) => setCaptions({ position: e.target.value as CaptionTrackConfig["position"] })}>
          <option value="bottom">Bottom</option><option value="center_lower">Lower center</option>
        </select>
      </label>
      <fieldset><legend>Accent color</legend>
        <div className="mt-1.5 flex gap-2">
          {Object.entries({ Amber: "#F59E0B", Cyan: "#22D3EE", Mint: "#34D399", Pink: "#F472B6", White: "#FFFFFF" }).map(([name, color]) => (
            <button type="button" key={color} aria-label={name} aria-pressed={color === captions.accent_color.toUpperCase()}
              title={name} style={{ backgroundColor: color }} onClick={() => setCaptions({ accent_color: color })}
              className="h-6 w-6 rounded-full aria-pressed:ring-2 aria-pressed:ring-white aria-pressed:ring-offset-2 aria-pressed:ring-offset-zinc-900" />
          ))}
        </div>
      </fieldset>
      <form className="space-y-2 border-t border-zinc-800 pt-3" onSubmit={(e) => {
        e.preventDefault();
        if (!scene || !overlayText.trim()) return;
        const overlay: OverlayItem = { id: `ov_${crypto.randomUUID()}`, type: overlayType, text: overlayText.trim(),
          start_ms: scene.start_ms, end_ms: scene.end_ms, position: overlayType === "badge" ? "top" : "center" };
        command({ type: "add_overlay", overlay });
        setOverlayText("");
      }}>
        <label className="block">Overlay type<select className={field} value={overlayType} onChange={(e) => setOverlayType(e.target.value as typeof overlayType)}>
          <option value="badge">Badge</option><option value="chapter_title">Chapter title</option>
        </select></label>
        <label className="block">Overlay text<input className={field} value={overlayText} onChange={(e) => setOverlayText(e.target.value)} /></label>
        <button type="submit" disabled={!scene || !overlayText.trim()} className={inspectorButton}>Add Overlay</button>
      </form>
      <ul className="space-y-1.5">{overlays.map((overlay) => (
        <li key={overlay.id} className="flex items-center justify-between gap-2 rounded bg-zinc-900 p-2">
          <span className="break-words">{overlay.text}</span>
          <button type="button" className={inspectorButton} aria-label={`Delete overlay ${overlay.text}`}
            onClick={() => command({ type: "remove_overlay", overlay_id: overlay.id })}>Delete</button>
        </li>
      ))}</ul>
    </div>
  );
}
