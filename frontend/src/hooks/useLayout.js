import { useEffect, useRef, useState } from "react";
import { getOntologyGraph } from "../api/client";

/** Tracks whether the app is in dark mode (the `dark` class on <html>). */
export function useIsDark() {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"));
  useEffect(() => {
    const observer = new MutationObserver(() =>
      setDark(document.documentElement.classList.contains("dark")));
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return dark;
}

/** Width of a container element, kept up to date — canvas graphs need explicit sizes. */
export function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useEffect(() => {
    if (!ref.current) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.floor(entry.contentRect.width)));
    observer.observe(ref.current);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

// The ontology is served from a static file on the backend: fetch it once per page load.
let ontologyPromise = null;
export function loadOntology() {
  if (!ontologyPromise) {
    ontologyPromise = getOntologyGraph().catch(err => { ontologyPromise = null; throw err; });
  }
  return ontologyPromise;
}

/** Canonical skill names and domain keys, for autocomplete and selects. */
export function useOntologyNames() {
  const [names, setNames] = useState({ skills: [], domains: [] });
  useEffect(() => {
    let alive = true;
    loadOntology().then(g => {
      if (!alive) return;
      setNames({
        skills: g.nodes.filter(n => n.type === "skill").map(n => n.label).sort(),
        domains: g.nodes.filter(n => n.type === "domain").map(n => n.label).sort(),
      });
    }).catch(() => {});
    return () => { alive = false; };
  }, []);
  return names;
}
