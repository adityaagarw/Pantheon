/** `next/dynamic` as React.lazy (see next-link.tsx). There is no server rendering, so `ssr` is ignored. */
import React, { lazy, Suspense, type ComponentType, type ReactNode } from "react";

type Loader<P> = () => Promise<ComponentType<P> | { default: ComponentType<P> }>;

export default function dynamic<P extends object>(
  load: Loader<P>,
  opts: { ssr?: boolean; loading?: () => ReactNode } = {},
): ComponentType<P> {
  const Lazy = lazy(async () => {
    const mod = await load();
    return "default" in mod ? mod : { default: mod };
  });
  const Fallback = opts.loading;
  return function Dynamic(props: P) {
    return (
      <Suspense fallback={Fallback ? <Fallback /> : null}>
        <Lazy {...(props as P & React.JSX.IntrinsicAttributes)} />
      </Suspense>
    );
  };
}
