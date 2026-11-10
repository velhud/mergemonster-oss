import type { HealthResponse } from "./client";

export async function requestHealth(): Promise<HealthResponse> {
  return { status: "ok", service: "synthetic-only" };
}
