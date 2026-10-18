import { useEffect, useState } from "react";
import { apiBlob } from "../api/client";
import type { Artifact } from "../api/types";

/** Artifacts sit behind the API key, so images are fetched with it and shown from a blob URL. */
export function ArtifactImage({ artifact }: { artifact: Artifact }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let revoked: string | null = null;
    let cancelled = false;
    apiBlob(`/v1/artifacts/${artifact.id}/content`)
      .then((blob) => {
        if (cancelled) return;
        revoked = URL.createObjectURL(blob);
        setUrl(revoked);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
      if (revoked) URL.revokeObjectURL(revoked);
    };
  }, [artifact.id]);

  return (
    <figure className="w-44 shrink-0">
      <div className="grid aspect-[390/844] place-items-center overflow-hidden rounded-xl border border-line bg-surface-2">
        {url ? (
          <img src={url} alt={`Screenshot ${artifact.name}`} className="size-full object-cover" />
        ) : (
          <span className="px-2 text-center text-xs text-muted">
            {failed ? "Not available" : "Loading..."}
          </span>
        )}
      </div>
      <figcaption className="mt-1.5 truncate text-xs text-muted" title={artifact.name}>
        {artifact.name}
      </figcaption>
    </figure>
  );
}
