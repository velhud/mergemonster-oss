import { requestHealth } from "./transport";

export interface HealthResponse {
  status: string;
  service: string;
}

export async function showHealth(): Promise<HealthResponse> {
  return requestHealth();
}
