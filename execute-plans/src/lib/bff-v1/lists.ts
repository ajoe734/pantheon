export interface ListEnvelope<T> {
  items: T[];
  cursor: Record<string, unknown>;
  pageSize: number;
  totalCountExact: boolean;
  estimatedTotal: number;
  meta?: Record<string, unknown>;
}

function emptyEnvelope<T>(): ListEnvelope<T> {
  return {
    items: [],
    cursor: {},
    pageSize: 0,
    totalCountExact: true,
    estimatedTotal: 0,
  };
}

function toRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

export function normalizeLiveListResponse<T>(
  data: unknown,
  _key: string,
): ListEnvelope<T> {
  const root = toRecord(data);
  const body = toRecord(root.data);
  const items: T[] = Array.isArray(root.items)
    ? (root.items as T[])
    : Array.isArray(root.data)
      ? (root.data as T[])
      : Array.isArray(body.items)
        ? (body.items as T[])
        : [];
  const cursor = toRecord(root.cursor ?? root.page_info);
  const pageSize = typeof root.page_size === "number" ? root.page_size : items.length;
  const meta = root.meta ? toRecord(root.meta) : undefined;
  return {
    items,
    cursor,
    pageSize,
    totalCountExact: Boolean(root.total_count_exact ?? true),
    estimatedTotal: typeof root.estimated_total === "number" ? root.estimated_total : items.length,
    meta,
  };
}

export function emptyList<T>(): ListEnvelope<T> {
  return emptyEnvelope<T>();
}
