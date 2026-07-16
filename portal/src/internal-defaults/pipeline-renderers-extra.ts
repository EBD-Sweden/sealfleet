// Default (no-op) private renderer registration. Deployments may overlay
// `src/internal/pipeline-renderers-extra.ts` (resolved first via the
// `@internal/*` path alias) to register custom pipeline result renderers.
import type { ComponentType } from "react";
import type { PipelineResultProps } from "@/lib/pipeline-renderers";

export function registerExtraRenderers(
  _register: (name: string, component: ComponentType<PipelineResultProps>) => void,
): void {
  // platform default: nothing to register
}
