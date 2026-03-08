export const DASHBOARD_TIME_ZONE = "America/Sao_Paulo";
const BRT_OFFSET = "-03:00";

const brtDateFormatter = new Intl.DateTimeFormat("en-CA", {
  timeZone: DASHBOARD_TIME_ZONE,
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export function formatDateInBrt(input: number | Date): string {
  const date = typeof input === "number" ? new Date(input) : input;
  const parts = Object.fromEntries(
    brtDateFormatter
      .formatToParts(date)
      .filter((part) => part.type !== "literal")
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function todayInBrt(): string {
  return formatDateInBrt(Date.now());
}

export function startOfBrtDayMs(date: string): number {
  return new Date(`${date}T00:00:00${BRT_OFFSET}`).getTime();
}
