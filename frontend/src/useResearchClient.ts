import { useEffect, useRef, useState } from 'react';
import { ApiError, RagApi } from './api';
import { ResearchApi } from './research';

/** Dialog-owned requests never abort the workspace's stream when a dialog closes. */
export function useResearchClient(session: RagApi, onUnauthorized: () => void) {
  const [api] = useState(() => new RagApi(session.token, session.base));
  const [client] = useState(() => new ResearchApi(api));
  const [error, setError] = useState('');
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; api.dispose(); }; }, [api]);
  function report(e: unknown) {
    if (!alive.current) return;
    if (e instanceof ApiError && e.status === 401) onUnauthorized();
    setError(e instanceof Error ? e.message : '操作失败，请重试');
  }
  return { api, client, error, setError, alive, report };
}
