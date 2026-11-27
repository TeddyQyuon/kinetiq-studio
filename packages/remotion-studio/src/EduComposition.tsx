import React from "react";
import {
  AbsoluteFill,
  Img,
  Sequence,
  Video,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import type {
  DiagramProps,
  DiagramTemplate,
  OverlayItem,
  TimelineDocument,
  VisualSceneItem,
} from "@kinetiq/contracts";

export interface EduCompositionProps {
  document: TimelineDocument;
  mediaUrls?: Record<string, string>;
}

const TimelineFlowView: React.FC<{
  props: DiagramProps;
  palette: string[];
}> = ({ props, palette }) => {
  const frame = useCurrentFrame();
  const { fps, width, height } = useVideoConfig();
  const accent = palette[1] ?? "#F59E0B";
  const secondary = palette[2] ?? "#22D3EE";
  const isHorizontal = width > height;

  const steps =
    props.steps && props.steps.length > 0
      ? props.steps
      : props.labels && props.labels.length > 0
        ? props.labels
        : ["Signal Capture", "Core Transform", "Verified Output"];
  const labels = props.labels ?? [];
  const values = props.values ?? [];
  const highlightIdx = props.highlight_index ?? steps.length - 1;

  const lineProgress = spring({
    frame: Math.max(0, frame - 4),
    fps,
    config: { damping: 18, stiffness: 90 },
  });
  const travelProgress = interpolate(frame, [6, Math.max(28, steps.length * 14)], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  if (isHorizontal) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
        {/* Horizontal Timeline Spine & Milestone Nodes */}
        <div style={{ position: "relative", paddingTop: 42, paddingBottom: 18 }}>
          {/* Background Rail */}
          <div
            style={{
              position: "absolute",
              top: 58,
              left: "6%",
              right: "6%",
              height: 6,
              borderRadius: 999,
              backgroundColor: "rgba(255,255,255,0.1)",
            }}
          />
          {/* Animated Flow Beam */}
          <div
            style={{
              position: "absolute",
              top: 58,
              left: "6%",
              width: `${88 * lineProgress}%`,
              height: 6,
              borderRadius: 999,
              background: `linear-gradient(90deg, ${secondary}, ${accent})`,
              boxShadow: `0 0 18px ${accent}66`,
            }}
          />
          {/* Traveling Pulse Dot */}
          <div
            style={{
              position: "absolute",
              top: 52,
              left: `calc(${6 + 88 * travelProgress}% - 9px)`,
              width: 18,
              height: 18,
              borderRadius: 999,
              backgroundColor: "#F8FAFC",
              boxShadow: `0 0 16px ${accent}, 0 0 28px ${secondary}`,
              opacity: lineProgress,
            }}
          />

          <div
            style={{
              position: "relative",
              display: "grid",
              gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))`,
              gap: 20,
            }}
          >
            {steps.map((step, idx) => {
              const nodeSpring = spring({
                frame: Math.max(0, frame - idx * 8),
                fps,
                config: { damping: 14 },
              });
              const isHighlight = idx === highlightIdx;
              const nodeColor = isHighlight ? accent : secondary;
              const timeTag = labels[idx] ?? `T+${idx + 1}`;
              const metricVal = values[idx];

              return (
                <div
                  key={`${idx}-${step}`}
                  style={{
                    position: "relative",
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    opacity: nodeSpring,
                    transform: `translateY(${(1 - nodeSpring) * 22}px)`,
                  }}
                >
                  {/* Phase / Timestamp Pill */}
                  <div
                    style={{
                      position: "absolute",
                      top: -40,
                      padding: "4px 12px",
                      borderRadius: 999,
                      backgroundColor: `${nodeColor}22`,
                      border: `1px solid ${nodeColor}66`,
                      color: nodeColor,
                      fontSize: 16,
                      fontWeight: 700,
                      letterSpacing: "0.04em",
                    }}
                  >
                    {timeTag}
                  </div>

                  {/* Node Marker */}
                  <div
                    style={{
                      width: 38,
                      height: 38,
                      borderRadius: 999,
                      backgroundColor: isHighlight ? accent : "#0F172A",
                      border: `3px solid ${nodeColor}`,
                      color: isHighlight ? "#09090B" : "#F8FAFC",
                      display: "flex",
                      alignItems: "center",
                      justifyContent: "center",
                      fontSize: 17,
                      fontWeight: 800,
                      boxShadow: `0 0 20px ${nodeColor}55`,
                      marginBottom: 20,
                    }}
                  >
                    {idx + 1}
                  </div>

                  {/* Milestone Card */}
                  <div
                    style={{
                      width: "100%",
                      padding: "18px 20px",
                      borderRadius: 16,
                      backgroundColor: isHighlight
                        ? "rgba(245, 158, 11, 0.1)"
                        : "rgba(255, 255, 255, 0.05)",
                      border: isHighlight
                        ? `1px solid ${accent}88`
                        : "1px solid rgba(255,255,255,0.12)",
                      display: "flex",
                      flexDirection: "column",
                      gap: 8,
                    }}
                  >
                    <div style={{ fontSize: 22, fontWeight: 700, lineHeight: 1.3 }}>{step}</div>
                    {metricVal !== undefined && (
                      <div style={{ fontSize: 18, fontWeight: 600, color: nodeColor }}>
                        {metricVal}
                        {props.unit ? ` ${props.unit}` : ""}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  // Vertical 9:16 Safe-Zone TimelineFlow Layout
  return (
    <div style={{ position: "relative", paddingLeft: 44, display: "flex", flexDirection: "column", gap: 18 }}>
      {/* Vertical Background Rail */}
      <div
        style={{
          position: "absolute",
          left: 18,
          top: 20,
          bottom: 20,
          width: 5,
          borderRadius: 999,
          backgroundColor: "rgba(255,255,255,0.1)",
        }}
      />
      {/* Animated Vertical Flow Rail */}
      <div
        style={{
          position: "absolute",
          left: 18,
          top: 20,
          bottom: 20,
          width: 5,
          borderRadius: 999,
          background: `linear-gradient(180deg, ${secondary}, ${accent})`,
          boxShadow: `0 0 16px ${accent}66`,
          transformOrigin: "top center",
          transform: `scaleY(${lineProgress})`,
        }}
      />
      {/* Traveling Vertical Pulse Dot */}
      <div
        style={{
          position: "absolute",
          left: 13.5,
          top: `${6 + 88 * travelProgress}%`,
          width: 14,
          height: 14,
          borderRadius: 999,
          backgroundColor: "#F8FAFC",
          boxShadow: `0 0 14px ${accent}, 0 0 24px ${secondary}`,
          opacity: lineProgress,
        }}
      />

      {steps.map((step, idx) => {
        const nodeSpring = spring({
          frame: Math.max(0, frame - idx * 8),
          fps,
          config: { damping: 14 },
        });
        const isHighlight = idx === highlightIdx;
        const nodeColor = isHighlight ? accent : secondary;
        const timeTag = labels[idx] ?? `T+${idx + 1}`;
        const metricVal = values[idx];

        return (
          <div
            key={`${idx}-${step}`}
            style={{
              position: "relative",
              opacity: nodeSpring,
              transform: `translateX(${(1 - nodeSpring) * 26}px)`,
              display: "flex",
              alignItems: "center",
            }}
          >
            {/* Node Dot on Vertical Rail */}
            <div
              style={{
                position: "absolute",
                left: -38,
                width: 28,
                height: 28,
                borderRadius: 999,
                backgroundColor: isHighlight ? accent : "#0F172A",
                border: `3px solid ${nodeColor}`,
                color: isHighlight ? "#09090B" : "#F8FAFC",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 14,
                fontWeight: 800,
                boxShadow: `0 0 16px ${nodeColor}55`,
              }}
            >
              {idx + 1}
            </div>

            {/* Connector Tick */}
            <div
              style={{
                position: "absolute",
                left: -10,
                width: 10,
                height: 2,
                backgroundColor: `${nodeColor}99`,
              }}
            />

            {/* Milestone Card */}
            <div
              style={{
                width: "100%",
                padding: "16px 20px",
                borderRadius: 16,
                backgroundColor: isHighlight
                  ? "rgba(245, 158, 11, 0.1)"
                  : "rgba(255, 255, 255, 0.05)",
                border: isHighlight
                  ? `1px solid ${accent}88`
                  : "1px solid rgba(255,255,255,0.12)",
                display: "flex",
                flexDirection: "column",
                gap: 6,
              }}
            >
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 10 }}>
                <span
                  style={{
                    fontSize: 15,
                    fontWeight: 700,
                    letterSpacing: "0.05em",
                    textTransform: "uppercase",
                    color: nodeColor,
                  }}
                >
                  {timeTag}
                </span>
                {metricVal !== undefined && (
                  <span style={{ fontSize: 16, fontWeight: 700, color: nodeColor }}>
                    {metricVal}
                    {props.unit ? ` ${props.unit}` : ""}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 24, fontWeight: 700, lineHeight: 1.3 }}>{step}</div>
            </div>
          </div>
        );
      })}
    </div>
  );
};

const DiagramSceneView: React.FC<{
  template: DiagramTemplate;
  props: DiagramProps;
  palette: string[];
  narration: string;
}> = ({ template, props, palette }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const accent = palette[1] ?? "#F59E0B";
  const secondary = palette[2] ?? "#22D3EE";

  const titleOpacity = interpolate(frame, [0, 12], [0, 1], { extrapolateRight: "clamp" });
  const titleY = interpolate(frame, [0, 12], [18, 0], { extrapolateRight: "clamp" });

  const labels = props.labels ?? ["Baseline", "Optimized", "Peak"];
  const values = props.values ?? [25, 62, 96];
  const maxVal = Math.max(...values, 1);
  const steps = props.steps ?? ["Signal Capture", "Core Transform", "Verified Output"];

  const badgeLabel =
    template === "BarCompare"
      ? "Data Comparison"
      : template === "StatCallout"
        ? "Key Metric"
        : template === "TimelineFlow"
          ? "Timeline Flow"
          : "Process Model";

  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(circle at 20% 20%, #162033 0%, ${palette[0] ?? "#0B0F19"} 72%)`,
        color: "#F8FAFC",
        padding: "7% 8%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <div
        style={{
          opacity: titleOpacity,
          transform: `translateY(${titleY}px)`,
          marginBottom: 32,
        }}
      >
        <div
          style={{
            display: "inline-block",
            padding: "6px 14px",
            borderRadius: 999,
            border: `1px solid ${accent}55`,
            backgroundColor: `${accent}18`,
            color: accent,
            fontSize: 18,
            fontWeight: 600,
            letterSpacing: "0.06em",
            textTransform: "uppercase",
            marginBottom: 14,
          }}
        >
          {badgeLabel}
        </div>
        <div style={{ fontSize: 46, fontWeight: 800, lineHeight: 1.12 }}>{props.title}</div>
        {props.subtitle ? (
          <div style={{ fontSize: 22, color: "#94A3B8", marginTop: 8 }}>{props.subtitle}</div>
        ) : null}
      </div>

      {template === "BarCompare" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 22 }}>
          {labels.map((label, idx) => {
            const val = values[idx] ?? 0;
            const prog = spring({ frame: Math.max(0, frame - idx * 6), fps, config: { damping: 16 } });
            const widthPct = Math.max(4, (val / maxVal) * 100 * prog);
            const isHighlight = idx === (props.highlight_index ?? labels.length - 1);
            return (
              <div key={label} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 24, fontWeight: 600 }}>
                  <span>{label}</span>
                  <span style={{ color: isHighlight ? accent : secondary }}>
                    {val}
                    {props.unit ? ` ${props.unit}` : ""}
                  </span>
                </div>
                <div
                  style={{
                    height: 28,
                    borderRadius: 999,
                    backgroundColor: "rgba(255,255,255,0.08)",
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${widthPct}%`,
                      height: "100%",
                      borderRadius: 999,
                      background: isHighlight
                        ? `linear-gradient(90deg, ${accent}, #FBBF24)`
                        : `linear-gradient(90deg, #334155, ${secondary})`,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}

      {template === "StepBreakdown" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 18 }}>
          {steps.map((step, idx) => {
            const prog = spring({ frame: Math.max(0, frame - idx * 8), fps, config: { damping: 15 } });
            return (
              <div
                key={step}
                style={{
                  opacity: prog,
                  transform: `translateX(${(1 - prog) * 28}px)`,
                  display: "flex",
                  alignItems: "center",
                  gap: 20,
                  padding: "18px 24px",
                  borderRadius: 16,
                  backgroundColor: "rgba(255,255,255,0.05)",
                  border: "1px solid rgba(255,255,255,0.1)",
                }}
              >
                <div
                  style={{
                    width: 42,
                    height: 42,
                    borderRadius: 12,
                    backgroundColor: `${accent}22`,
                    border: `1px solid ${accent}`,
                    color: accent,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    fontSize: 20,
                    fontWeight: 700,
                    flexShrink: 0,
                  }}
                >
                  {idx + 1}
                </div>
                <div style={{ fontSize: 26, fontWeight: 600 }}>{step}</div>
              </div>
            );
          })}
        </div>
      )}

      {template === "TimelineFlow" && <TimelineFlowView props={props} palette={palette} />}

      {template === "StatCallout" && (
        <div
          style={{
            padding: "42px",
            borderRadius: 24,
            backgroundColor: "rgba(255,255,255,0.04)",
            border: `1px solid ${accent}44`,
          }}
        >
          <div style={{ fontSize: 96, fontWeight: 900, color: accent, lineHeight: 1 }}>
            {props.stat_value ?? "99.4%"}
          </div>
          <div style={{ fontSize: 28, color: "#CBD5E1", marginTop: 14 }}>
            {props.stat_label ?? "Measured efficiency gain"}
          </div>
        </div>
      )}
    </AbsoluteFill>
  );
};

const VisualSceneLayer: React.FC<{
  scene: VisualSceneItem;
  palette: string[];
  mediaUrls: Record<string, string>;
}> = ({ scene, palette, mediaUrls }) => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();

  if (scene.kind === "diagram") {
    return (
      <DiagramSceneView
        template={scene.template ?? "BarCompare"}
        props={scene.props ?? { title: "Visual Breakdown" }}
        palette={palette}
        narration={scene.narration}
      />
    );
  }

  const videoId = scene.assets?.proxy ?? scene.assets?.master;
  const videoUrl = videoId ? mediaUrls[videoId] : undefined;
  const kfId = scene.assets?.keyframe;
  const kfUrl = kfId ? mediaUrls[kfId] : undefined;

  if (videoUrl && !videoUrl.startsWith("mock://")) {
    const startFrom = Math.max(0, Math.round(((scene.source_start_ms ?? 0) / 1000) * fps));
    return (
      <AbsoluteFill style={{ backgroundColor: "#000" }}>
        <Video src={videoUrl} startFrom={startFrom} style={{ width: "100%", height: "100%", objectFit: "cover" }} />
      </AbsoluteFill>
    );
  }

  if (kfUrl && !kfUrl.startsWith("mock://")) {
    const scale = interpolate(frame, [0, Math.max(1, durationInFrames)], [1.0, 1.09], {
      extrapolateRight: "clamp",
    });
    return (
      <AbsoluteFill style={{ backgroundColor: "#000", overflow: "hidden" }}>
        <Img
          src={kfUrl}
          style={{
            width: "100%",
            height: "100%",
            objectFit: "cover",
            transform: `scale(${scale})`,
          }}
        />
      </AbsoluteFill>
    );
  }

  // Rich storyboard preview fallback before GPU rendering completes
  const zoom = interpolate(frame, [0, Math.max(1, durationInFrames)], [1.0, 1.06], {
    extrapolateRight: "clamp",
  });
  const accent = palette[1] ?? "#F59E0B";
  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(135deg, ${palette[0] ?? "#0B0F19"} 0%, #172033 55%, #09090B 100%)`,
        color: "#F8FAFC",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        alignItems: "center",
        padding: "10%",
        textAlign: "center",
        transform: `scale(${zoom})`,
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      <div
        style={{
          padding: "6px 14px",
          borderRadius: 999,
          backgroundColor: `${accent}20`,
          border: `1px solid ${accent}66`,
          color: accent,
          fontSize: 16,
          fontWeight: 700,
          letterSpacing: "0.08em",
          textTransform: "uppercase",
          marginBottom: 20,
        }}
      >
        {scene.model ?? "ltx25_fp8"} · {scene.camera ?? "slow_dolly_in"}
      </div>
      <div style={{ fontSize: 32, fontWeight: 700, maxWidth: "85%", lineHeight: 1.35 }}>
        {scene.prompt?.ltx25 ?? scene.prompt?.qwen_image ?? scene.narration}
      </div>
    </AbsoluteFill>
  );
};

const KineticCaptionBar: React.FC<{
  scenes: VisualSceneItem[];
  accentColor: string;
  position: "bottom" | "center_lower";
  isVertical: boolean;
}> = ({ scenes, accentColor, position, isVertical }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const currentMs = (frame / fps) * 1000;

  const active = scenes.find((s) => currentMs >= s.start_ms && currentMs < s.end_ms);
  if (!active) return null;

  const words = active.narration.split(/\s+/).filter(Boolean);
  const relProgress = (currentMs - active.start_ms) / Math.max(1, active.end_ms - active.start_ms);
  const activeIdx = Math.min(words.length - 1, Math.floor(relProgress * words.length));
  const chunkStart = Math.floor(activeIdx / 5) * 5;
  const visibleWords = words.slice(chunkStart, chunkStart + 5);

  return (
    <div
      style={{
        position: "absolute",
        left: "6%",
        right: "6%",
        bottom: isVertical ? (position === "center_lower" ? "26%" : "20%") : "8%",
        display: "flex",
        justifyContent: "center",
        pointerEvents: "none",
      }}
    >
      <div
        style={{
          backgroundColor: "rgba(9, 9, 11, 0.82)",
          border: "1px solid rgba(255,255,255,0.14)",
          padding: "12px 22px",
          borderRadius: 14,
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          gap: 10,
          fontFamily: "system-ui, -apple-system, sans-serif",
        }}
      >
        {visibleWords.map((w, i) => {
          const globalIdx = chunkStart + i;
          const isCurrent = globalIdx === activeIdx;
          return (
            <span
              key={`${globalIdx}-${w}`}
              style={{
                fontSize: isVertical ? 38 : 30,
                fontWeight: 800,
                color: isCurrent ? accentColor : "#F8FAFC",
                textShadow: "0 2px 10px rgba(0,0,0,0.8)",
              }}
            >
              {w}
            </span>
          );
        })}
      </div>
    </div>
  );
};

const OverlayLayer: React.FC<{ overlay: OverlayItem }> = ({ overlay }) => {
  return (
    <div
      style={{
        position: "absolute",
        top: overlay.position === "bottom" ? "72%" : overlay.position === "center" ? "44%" : "7%",
        left: "6%",
        padding: "10px 18px",
        borderRadius: 10,
        backgroundColor: "rgba(245, 158, 11, 0.92)",
        color: "#09090B",
        fontSize: 22,
        fontWeight: 800,
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      {overlay.text}
    </div>
  );
};

export const EduComposition: React.FC<EduCompositionProps> = ({ document, mediaUrls = {} }) => {
  const { fps } = useVideoConfig();
  const isVertical = document.format.aspect === "9:16";

  return (
    <AbsoluteFill style={{ backgroundColor: "#09090B" }}>
      {document.tracks.visual.map((scene) => {
        const from = Math.round((scene.start_ms / 1000) * fps);
        const duration = Math.max(1, Math.round(((scene.end_ms - scene.start_ms) / 1000) * fps));
        return (
          <Sequence key={scene.id} from={from} durationInFrames={duration}>
            <VisualSceneLayer
              scene={scene}
              palette={document.style_bible.palette}
              mediaUrls={mediaUrls}
            />
          </Sequence>
        );
      })}

      {document.tracks.overlays.map((ov) => {
        const from = Math.round((ov.start_ms / 1000) * fps);
        const duration = Math.max(1, Math.round(((ov.end_ms - ov.start_ms) / 1000) * fps));
        return (
          <Sequence key={ov.id} from={from} durationInFrames={duration}>
            <OverlayLayer overlay={ov} />
          </Sequence>
        );
      })}

      {document.tracks.captions.enabled && (
        <KineticCaptionBar
          scenes={document.tracks.visual}
          accentColor={document.tracks.captions.accent_color}
          position={document.tracks.captions.position}
          isVertical={isVertical}
        />
      )}
    </AbsoluteFill>
  );
};
