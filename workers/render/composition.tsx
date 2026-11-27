import React from "react";
import {Composition, getInputProps, registerRoot, staticFile} from "remotion";
import {EduComposition} from "@kinetiq/remotion-studio";
import type {TimelineDocument} from "@kinetiq/contracts";

type RenderProps = {
  document: TimelineDocument;
  mediaFiles: Record<string, string>;
  durationInFrames: number;
};

const Master: React.FC<RenderProps> = ({document, mediaFiles}) => {
  const mediaUrls = Object.fromEntries(
    Object.entries(mediaFiles).map(([id, filename]) => [id, staticFile(filename)]),
  );
  return <EduComposition document={document} mediaUrls={mediaUrls} />;
};

const Root: React.FC = () => {
  const props = getInputProps<RenderProps>();
  const {width, height, fps} = props.document.format;
  return <Composition
    id="EduComposition"
    component={Master}
    defaultProps={props}
    width={width}
    height={height}
    fps={fps}
    durationInFrames={props.durationInFrames}
  />;
};

registerRoot(Root);
