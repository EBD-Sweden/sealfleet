// Default (empty) private navigation extras. Deployments may overlay
// `src/internal/nav-extra.ts` (resolved first via the `@internal/*` path
// alias) to add their own sidebar entries without touching platform files.
import type { ComponentType } from "react";

export interface ExtraNavItem {
  title: string;
  href: string;
  icon: ComponentType<{ className?: string }>;
}

export const extraNavItems: ExtraNavItem[] = [];
