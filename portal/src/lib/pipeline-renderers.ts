import { ComponentType } from "react";

/** Props passed to every pipeline result renderer */
export interface PipelineResultProps {
  pipelineName: string;
  runId: string;
  result: unknown;
  metadata?: {
    duration_ms?: number;
    started_at?: string;
    completed_at?: string;
    status?: string;
  };
}

/** Registry: pipeline name → renderer component */
type RendererRegistry = Record<string, ComponentType<PipelineResultProps>>;

const RENDERERS: RendererRegistry = {};

export function registerRenderer(
  name: string,
  component: ComponentType<PipelineResultProps>,
) {
  RENDERERS[name] = component;
}

export function getRenderer(
  name: string,
): ComponentType<PipelineResultProps> | null {
  return RENDERERS[name] ?? null;
}

// --- Register built-in renderers ---
// Lazy-import to keep the bundle lean; the dynamic import in the results page
// will pull these in only when needed.

// (No custom renderers registered in the public example set — GenericResult
// covers arbitrary pipeline output, and /weather-trip ships its own page.)

// Private deployments can register extra renderers via the @internal overlay;
// the platform default is a no-op.
import { registerExtraRenderers } from "@internal/pipeline-renderers-extra";

registerExtraRenderers(registerRenderer);
