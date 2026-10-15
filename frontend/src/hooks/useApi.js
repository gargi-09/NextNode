import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Load data from an API function and expose { data, error, loading, reload }.
 * `loader` is re-run whenever `deps` change; pass `enabled: false` to wait.
 */
export function useApi(loader, deps = [], { enabled = true } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: enabled });
  const loaderRef = useRef(loader);
  loaderRef.current = loader;
  const requestId = useRef(0);

  const reload = useCallback(async () => {
    const id = ++requestId.current;
    setState(s => ({ ...s, loading: true, error: null }));
    try {
      const data = await loaderRef.current();
      if (id === requestId.current) setState({ data, error: null, loading: false });
    } catch (error) {
      if (id === requestId.current) setState(s => ({ ...s, error, loading: false }));
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    // run after the effect commits; state updates happen after the await
    const t = setTimeout(reload, 0);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, reload, ...deps]);

  return { ...state, reload };
}

/** Wrap a one-off action (button click) with pending / error state. */
export function useAction(action) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState(null);
  const actionRef = useRef(action);
  actionRef.current = action;

  const run = useCallback(async (...args) => {
    setPending(true);
    setError(null);
    try {
      return await actionRef.current(...args);
    } catch (e) {
      setError(e);
      return undefined;
    } finally {
      setPending(false);
    }
  }, []);

  return { run, pending, error, clearError: () => setError(null) };
}
