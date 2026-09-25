import { Segmented } from "antd";

import type { AstroCardRouteVariant, AstroCardVariant } from "../api/types";

const options: Array<{ label: string; value: AstroCardVariant }> = [
  { label: "两种都建", value: "both" },
  { label: "仅非 GC", value: "non_gc" },
  { label: "仅 GC", value: "gc" }
];

export function supportsAstroCardVariant(
  routes: AstroCardRouteVariant[] | undefined,
  variant: AstroCardVariant
): boolean {
  return routes === undefined || routes.some((route) =>
    variant === "both" || route.card_variant === variant
  );
}

export function selectedAstroRoutes(
  routes: AstroCardRouteVariant[] | undefined,
  variant: AstroCardVariant
): string {
  if (!routes) return "-";
  return routes
    .filter((route) => variant === "both" || route.card_variant === variant)
    .map((route) => `${route.buy_exchange} → ${route.sell_exchange}`)
    .join("、") || "-";
}

export function AstroCardVariantSelector({
  routes,
  value,
  defaultValue,
  onChange
}: {
  routes?: AstroCardRouteVariant[];
  value?: AstroCardVariant;
  defaultValue?: AstroCardVariant;
  onChange?: (value: AstroCardVariant) => void;
}) {
  return (
    <Segmented
      block
      options={options.map((option) => ({
        ...option,
        disabled: !supportsAstroCardVariant(routes, option.value)
      }))}
      {...(value !== undefined ? { value } : { defaultValue })}
      onChange={(next) => onChange?.(next as AstroCardVariant)}
    />
  );
}
