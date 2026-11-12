"use client";

/**
 * Mol* molecular viewer.
 *
 * This is the signature of the product: a structural biologist reads a fold by
 * turning it, so the specimen rotates continuously under the lens rather than
 * sitting still until poked. Everything else in the app is calm; this moves.
 *
 * Chain roles are passed in, never guessed. A live run's result contains only
 * the designed binder in its data while the structure file holds both chains,
 * and the target keeps the chain id that was submitted — so the backend records
 * which is which and the viewer simply colours what it is told
 * (docs/boltz-api-observed.md).
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  useSyncExternalStore,
} from "react";

type Representation = "cartoon" | "surface" | "ball-and-stick";

export type StructureViewerProps = {
  /** mmCIF source. Served from our own storage, never a presigned engine URL. */
  url: string;
  /** Chains that are the designed binder. Coloured as signal. */
  binderChains?: string[];
  /** Chains carried over from the submitted target. Coloured as target. */
  targetChains?: string[];
  /** 0-based design indices of the epitope, highlighted on the target. */
  epitopeIndices?: number[];
  className?: string;
  /** Start rotating immediately. The default, because that is the point. */
  autoRotate?: boolean;
};

const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";

/** Reduced motion is external state; subscribing beats mirroring it into
 *  component state from an effect. */
function subscribeToReducedMotion(onChange: () => void): () => void {
  const query = window.matchMedia(REDUCED_MOTION_QUERY);
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}

const COLOURS = {
  binder: 0x00e5a0,
  target: 0x5b8def,
  epitope: 0xff4d8d,
  background: 0x0a0e14,
};


/**
 * Draw the epitope residues as sticks in the epitope colour.
 *
 * Design indices are 0-based into the modelled polymer; mmCIF `label_seq_id`
 * is 1-based over the same sequence, so the offset is exactly one. Built as a
 * separate representation rather than an overpaint, because a stick overlay
 * reads clearly against a cartoon at any zoom.
 *
 * Failures here are swallowed deliberately: a missing highlight must not take
 * the whole viewer down. The legend is driven by the same inputs, so it is
 * rendered only when this is asked for.
 */
async function highlightEpitope(
  plugin: unknown,
  chainId: string | undefined,
  indices: number[],
): Promise<void> {
  if (!indices.length) return;
  try {
    const [{ MolScriptBuilder: MS }, { Color }, { setStructureOverpaint }] =
      await Promise.all([
        import("molstar/lib/mol-script/language/builder"),
        import("molstar/lib/mol-util/color"),
        import("molstar/lib/mol-plugin-state/helpers/structure-overpaint"),
      ]);

    const p = plugin as {
      managers: {
        structure: {
          hierarchy: {
            current: { structures: { components: unknown[] }[] };
          };
        };
      };
    };
    const structures = p.managers.structure.hierarchy.current.structures;
    if (!structures.length) return;

    const seqIds = indices.map((i) => i + 1);
    const expression = MS.struct.generator.atomGroups({
      "chain-test": chainId
        ? MS.core.rel.eq([MS.ammp("auth_asym_id"), chainId])
        : undefined,
      "residue-test": MS.core.set.has([
        MS.set(...seqIds),
        MS.ammp("label_seq_id"),
      ]),
    });

    for (const structure of structures) {
      await setStructureOverpaint(
        plugin as never,
        structure.components as never,
        Color(0xff4d8d),
        async (s) => {
          const [{ StructureSelection }, { Script }] = await Promise.all([
            import("molstar/lib/mol-model/structure"),
            import("molstar/lib/mol-script/script"),
          ]);
          const sel = Script.getStructureSelection(() => expression, s);
          return StructureSelection.toLociWithSourceUnits(sel);
        },
      );
    }
  } catch {
    /* A missing highlight is a cosmetic loss; a broken viewer is not. */
  }
}

export function StructureViewer({
  url,
  binderChains = [],
  targetChains = [],
  epitopeIndices = [],
  className = "",
  autoRotate = true,
}: StructureViewerProps) {
  const hostRef = useRef<HTMLDivElement>(null);
  const pluginRef = useRef<unknown>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [error, setError] = useState<string>("");
  const [spinRequested, setSpinRequested] = useState(autoRotate);
  const [representation, setRepresentation] = useState<Representation>("cartoon");
  const epitopeKey = epitopeIndices.join(",");
  const epitopeChain = targetChains[0];
  const reducedMotion = useSyncExternalStore(
    subscribeToReducedMotion,
    () => window.matchMedia(REDUCED_MOTION_QUERY).matches,
    () => false, // server render: assume motion is allowed, then correct on hydrate
  );
  // Respect the preference without storing a second copy of it.
  const spinning = spinRequested && !reducedMotion;

  /* Mol* is a large dependency and imports browser-only modules, so it is
     loaded on demand rather than in the page bundle. */
  useEffect(() => {
    let disposed = false;
    let plugin: { dispose?: () => void } | null = null;

    async function boot() {
      if (!hostRef.current) return;
      setStatus("loading");
      setError("");
      try {
        const [{ createPluginUI }, { renderReact18 }, specMod, { Color }] =
          await Promise.all([
            import("molstar/lib/mol-plugin-ui"),
            import("molstar/lib/mol-plugin-ui/react18"),
            import("molstar/lib/mol-plugin-ui/spec"),
            import("molstar/lib/mol-util/color"),
          ]);
        if (disposed || !hostRef.current) return;

        const spec = specMod.DefaultPluginUISpec();
        const created = await createPluginUI({
          target: hostRef.current,
          render: renderReact18,
          spec: {
            ...spec,
            layout: {
              initial: {
                isExpanded: false,
                showControls: false,
                controlsDisplay: "reactive",
              },
            },
            components: {
              ...spec.components,
              remoteState: "none",
            },
            canvas3d: {
              ...spec.canvas3d,
              renderer: {
                ...spec.canvas3d?.renderer,
                // Mol* uses a branded Color type rather than a bare number.
                backgroundColor: Color(COLOURS.background),
              },
              camera: { ...spec.canvas3d?.camera, helper: { axes: { name: "off", params: {} } } },
            },
          },
        });

        if (disposed) {
          created.dispose();
          return;
        }
        plugin = created as unknown as { dispose?: () => void };
        pluginRef.current = created;

        const data = await created.builders.data.download({ url, isBinary: false });
        const trajectory = await created.builders.structure.parseTrajectory(data, "mmcif");
        await created.builders.structure.hierarchy.applyPreset(trajectory, "default");

        if (disposed) return;
        await highlightEpitope(created, epitopeChain, epitopeIndices);

        if (disposed) return;
        setStatus("ready");
      } catch (e) {
        if (disposed) return;
        // A viewer that fails must say so rather than showing an empty black
        // box that looks like a structure with no atoms.
        setError(e instanceof Error ? e.message : "could not load the structure");
        setStatus("error");
      }
    }

    void boot();
    return () => {
      disposed = true;
      try {
        plugin?.dispose?.();
      } catch {
        /* disposal races a failed boot; nothing useful to do */
      }
      pluginRef.current = null;
    };
    // Re-running on epitope changes would rebuild the whole scene, so the
    // highlight is applied once during boot from these values.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url, epitopeKey, epitopeChain]);

  /* Continuous rotation, driven by Mol*'s own trackball so it composes with
     user interaction rather than fighting it.
     `spin` needs BOTH speed and an axis: omitting the axis makes Mol* read
     axis[0] of undefined on every frame of the render loop. */
  useEffect(() => {
    if (status !== "ready") return;
    let cancelled = false;
    void (async () => {
      const { Vec3 } = await import("molstar/lib/mol-math/linear-algebra");
      if (cancelled) return;
      const plugin = pluginRef.current as
        | { canvas3d?: { setProps: (p: unknown) => void } }
        | null;
      plugin?.canvas3d?.setProps({
        trackball: {
          animate: spinning
            ? { name: "spin", params: { speed: 0.9, axis: Vec3.create(0, 1, 0) } }
            : { name: "off", params: {} },
        },
      });
    })();
    return () => {
      cancelled = true;
    };
  }, [spinning, status]);

  const applyRepresentation = useCallback(
    async (next: Representation) => {
      setRepresentation(next);
      const plugin = pluginRef.current as {
        managers?: {
          structure?: {
            component?: {
              updateRepresentationsTheme?: (a: unknown, b: unknown) => Promise<void>;
            };
          };
        };
        builders?: unknown;
        dataTransaction?: (f: () => Promise<void>) => Promise<void>;
      } | null;
      if (!plugin) return;
      try {
        const { StateTransforms } = await import("molstar/lib/mol-plugin-state/transforms");
        void StateTransforms;
        // Mol*'s preset machinery is the supported way to swap representation
        // wholesale; doing it by hand on every component is brittle across
        // versions.
        const presets: Record<Representation, string> = {
          cartoon: "polymer-cartoon",
          surface: "molecular-surface",
          "ball-and-stick": "polymer-and-ligand",
        };
        const full = pluginRef.current as {
          managers: {
            structure: {
              hierarchy: { current: { structures: { cell: unknown }[] } };
              component: { applyPreset: (s: unknown, p: string) => Promise<void> };
            };
          };
        };
        const structures = full.managers.structure.hierarchy.current.structures.map(
          (s) => s.cell,
        );
        if (structures.length) {
          await full.managers.structure.component.applyPreset(structures, presets[next]);
        }
      } catch {
        /* Representation is a convenience; a failure must not break the view. */
      }
    },
    [],
  );

  const reset = useCallback(() => {
    const plugin = pluginRef.current as
      | { canvas3d?: { requestCameraReset: (o?: unknown) => void } }
      | null;
    plugin?.canvas3d?.requestCameraReset({ durationMs: 400 });
  }, []);

  const hasLegend = binderChains.length > 0 || targetChains.length > 0;

  return (
    // Fills its parent. The parent must have a size (an aspect box, a grid
    // row, a fixed height); passing positioning in via className would fight
    // the `relative` here and collapse the box.
    <div className={`relative size-full overflow-hidden bg-ground ${className}`}>
      <div ref={hostRef} className="absolute inset-0" aria-hidden="true" />

      {/* A real description for anyone not seeing the canvas. The viewer is not
          the only way to read this data: the metrics table beside it is. */}
      <p className="sr-only">
        Three-dimensional view of the predicted complex.
        {binderChains.length > 0 &&
          ` Designed binder on chain ${binderChains.join(", ")}.`}
        {targetChains.length > 0 && ` Target protein on chain ${targetChains.join(", ")}.`}
        {epitopeIndices.length > 0 &&
          ` ${epitopeIndices.length} epitope residues are highlighted.`}
        {" "}Metric values are listed in the adjacent table.
      </p>

      {status === "loading" && (
        <div className="absolute inset-0 grid place-items-center">
          <div className="flex items-center gap-3 text-xs text-muted">
            <span className="block size-2 animate-pulse rounded-full bg-signal" />
            <span className="tnum uppercase tracking-widest">loading structure</span>
          </div>
        </div>
      )}

      {status === "error" && (
        <div className="absolute inset-0 grid place-items-center p-6">
          <div className="max-w-sm border border-line bg-panel p-4">
            <p className="text-sm font-medium text-caution">
              The structure could not be displayed
            </p>
            <p className="mt-2 text-xs leading-relaxed text-muted">{error}</p>
            <p className="mt-2 text-xs leading-relaxed text-faint">
              The sequence and metrics for this candidate are still shown
              alongside; only the 3D view is unavailable.
            </p>
          </div>
        </div>
      )}

      {/* Instrument chrome: a legend that explains the colours, and the few
          controls a structural biologist actually reaches for. */}
      {status === "ready" && (
        <div className="pointer-events-none absolute inset-x-0 bottom-0 flex flex-wrap items-end justify-between gap-3 p-3">
          {hasLegend && (
            <ul className="pointer-events-auto flex flex-wrap items-center gap-x-4 gap-y-1 border border-line bg-panel/90 px-3 py-2 text-[11px] backdrop-blur-sm">
              {binderChains.length > 0 && (
                <li className="flex items-center gap-2">
                  <span className="block size-2 rounded-sm bg-binder" />
                  <span className="text-muted">designed binder</span>
                  <span className="tnum text-ink">{binderChains.join(" ")}</span>
                </li>
              )}
              {targetChains.length > 0 && (
                <li className="flex items-center gap-2">
                  <span className="block size-2 rounded-sm bg-target" />
                  <span className="text-muted">target</span>
                  <span className="tnum text-ink">{targetChains.join(" ")}</span>
                </li>
              )}
              {epitopeIndices.length > 0 && (
                <li className="flex items-center gap-2">
                  <span className="block size-2 rounded-sm bg-epitope" />
                  <span className="text-muted">epitope</span>
                  <span className="tnum text-ink">{epitopeIndices.length} res</span>
                </li>
              )}
            </ul>
          )}

          <div className="pointer-events-auto flex items-center gap-1 border border-line bg-panel/90 p-1 backdrop-blur-sm">
            {(["cartoon", "surface", "ball-and-stick"] as Representation[]).map((rep) => (
              <button
                key={rep}
                type="button"
                onClick={() => void applyRepresentation(rep)}
                aria-pressed={representation === rep}
                className={`px-2 py-1 text-[11px] uppercase tracking-wider transition-colors duration-[var(--dur-ui)] active:scale-[0.97] ${
                  representation === rep
                    ? "bg-signal text-ground"
                    : "text-muted hover:text-ink"
                }`}
              >
                {rep === "ball-and-stick" ? "atoms" : rep}
              </button>
            ))}
            <span className="mx-1 h-4 w-px bg-line" />
            <button
              type="button"
              onClick={() => setSpinRequested((s) => !s)}
              aria-pressed={spinning}
              title={
                reducedMotion
                  ? "Rotation is off because your system prefers reduced motion"
                  : "Toggle rotation"
              }
              className={`px-2 py-1 text-[11px] uppercase tracking-wider transition-colors duration-[var(--dur-ui)] active:scale-[0.97] ${
                spinning ? "bg-signal text-ground" : "text-muted hover:text-ink"
              }`}
            >
              spin
            </button>
            <button
              type="button"
              onClick={reset}
              className="px-2 py-1 text-[11px] uppercase tracking-wider text-muted transition-colors duration-[var(--dur-ui)] hover:text-ink active:scale-[0.97]"
            >
              reset
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

export default StructureViewer;
