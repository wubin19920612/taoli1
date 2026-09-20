import type { Opportunity, OpportunityFilters } from "../api/types";

export interface PriorityOpportunityRoute {
  symbol: string;
  exchange: string;
  displayName: string;
}

export const dashboardPriorityOpportunityRoutes: PriorityOpportunityRoute[] = [
  {
    symbol: "HOODUSDT",
    exchange: "lighter",
    displayName: "Robinhood / 罗宾汉"
  }
];

function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase().replace(/[-_/]/g, "");
}

export function matchesPriorityOpportunityRoute(
  opportunity: Opportunity,
  route: PriorityOpportunityRoute
): boolean {
  return (
    normalizeSymbol(opportunity.symbol) === normalizeSymbol(route.symbol) &&
    [opportunity.buy_exchange, opportunity.sell_exchange].some(
      (exchange) => exchange.trim().toLowerCase() === route.exchange
    )
  );
}

export function dashboardOpportunityPriority(opportunity: Opportunity): number {
  const index = dashboardPriorityOpportunityRoutes.findIndex((route) =>
    matchesPriorityOpportunityRoute(opportunity, route)
  );
  return index < 0 ? 0 : dashboardPriorityOpportunityRoutes.length - index;
}

export function priorityOpportunityDisplayName(opportunity: Opportunity): string | null {
  return (
    dashboardPriorityOpportunityRoutes.find((route) =>
      matchesPriorityOpportunityRoute(opportunity, route)
    )?.displayName ?? null
  );
}

export function priorityRoutesForFilters(
  filters: OpportunityFilters
): PriorityOpportunityRoute[] {
  if (filters.symbol?.trim()) {
    return [];
  }
  const wantedExchange = filters.exchange?.trim().toLowerCase();
  return dashboardPriorityOpportunityRoutes.filter(
    (route) => !wantedExchange || wantedExchange === route.exchange
  );
}
