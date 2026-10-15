// Shared colour scales for the graph views and their legends.

export const STATUS_COLORS = {
  met: "#059669", partial: "#D97706", missing: "#DC2626",
  owned: "#0D9488", prerequisite: "#94A3B8",
};

export const TYPE_COLORS = { user: "#7C3AED", job: "#2563EB", domain: "#6B7280" };

export const LINK_COLORS = {
  HAS_SKILL: "#0D9488", REQUIRES_SKILL: "#60A5FA", PREREQUISITE_OF: "#F59E0B",
  TARGETS: "#A78BFA", IN_DOMAIN: "#9CA3AF",
};

export const CATEGORY_COLORS = {
  language: "#7C3AED", ml_framework: "#2563EB", platform: "#0D9488",
  concept: "#D97706", tool: "#DB2777", domain: "#9CA3AF",
};

// force-graph renders node labels as HTML tooltips; names can come from user input
export const escapeHtml = s => String(s).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
