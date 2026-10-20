/**
 * `next/link` on React Router, so v1's components keep their imports while the UI runs
 * on Vite. New code should use react-router's Link directly.
 */
import { forwardRef, type AnchorHTMLAttributes } from "react";
import { Link as RouterLink } from "react-router";

type Props = AnchorHTMLAttributes<HTMLAnchorElement> & { href: string; prefetch?: boolean };

const Link = forwardRef<HTMLAnchorElement, Props>(function Link({ href, prefetch, ...rest }, ref) {
  void prefetch; // Next's prop; the router has no prefetch
  if (/^[a-z]+:/i.test(href) || rest.target === "_blank") return <a ref={ref} href={href} {...rest} />;
  return <RouterLink ref={ref} to={href} {...rest} />;
});

export default Link;
