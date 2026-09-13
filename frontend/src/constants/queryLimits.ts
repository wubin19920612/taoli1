export const HOURLY_HISTORY_THRESHOLD_HOURS = 24 * 7;
export const DAILY_HISTORY_THRESHOLD_HOURS = 24 * 365;
export const HOURLY_HISTORY_INTERVAL_SECONDS = 3_600;
export const DAILY_HISTORY_INTERVAL_SECONDS = 86_400;

const HISTORICAL_INTERVAL_SECONDS = [60, 300, 900, 3_600, 14_400, 86_400];

export function resolveHistoryIntervalSeconds(hours: number, requestedIntervalSeconds: number): number {
  if (hours > DAILY_HISTORY_THRESHOLD_HOURS) {
    return DAILY_HISTORY_INTERVAL_SECONDS;
  }
  if (hours <= HOURLY_HISTORY_THRESHOLD_HOURS) {
    return requestedIntervalSeconds;
  }
  if (requestedIntervalSeconds <= HOURLY_HISTORY_INTERVAL_SECONDS) {
    return HOURLY_HISTORY_INTERVAL_SECONDS;
  }
  return HISTORICAL_INTERVAL_SECONDS.find((candidate) => candidate >= requestedIntervalSeconds) ?? DAILY_HISTORY_INTERVAL_SECONDS;
}
