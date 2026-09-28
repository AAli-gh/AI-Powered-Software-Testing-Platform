import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Schemas = components["schemas"];

/** An error answered by the API (its uniform error body), or a network failure (status 0). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }

  get notFound(): boolean {
    return this.status === 404;
  }
}

// Same origin: Vite proxies /api in development, nginx in production. `fetch` is looked up on
// every call so tests can replace it at the network boundary.
export const api = createClient<paths>({
  baseUrl: globalThis.location.origin,
  fetch: (request: Request) => globalThis.fetch(request),
});

const TOKEN_KEY = "testmind.token";

/** The API token of this browser tab (sessionStorage: it is gone when the tab closes). */
export function readToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

const tokenListeners = new Set<() => void>();

export function saveToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    // Storage unavailable (private mode): the API answers 401 and asks again.
  }
  tokenListeners.forEach((listener) => {
    listener();
  });
}

/** For useSyncExternalStore: components showing whether a token is set stay current. */
export function subscribeToken(listener: () => void): () => void {
  tokenListeners.add(listener);
  return () => {
    tokenListeners.delete(listener);
  };
}

api.use({
  onRequest({ request }) {
    const token = readToken();
    if (token) request.headers.set("Authorization", `Bearer ${token}`);
    return request;
  },
});

interface Answer<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** The body of a successful answer, or an ApiError carrying the API's code and message. */
export async function unwrap<T>(call: Promise<Answer<T>>): Promise<T> {
  let answer: Answer<T>;
  try {
    answer = await call;
  } catch {
    throw new ApiError(0, "network_error", "The TestMind API could not be reached");
  }
  if (answer.response.ok && answer.data !== undefined) {
    return answer.data;
  }
  const body = answer.error as { error?: { code?: string; message?: string } } | undefined;
  throw new ApiError(
    answer.response.status,
    body?.error?.code ?? "unexpected_answer",
    body?.error?.message ?? `The API answered ${answer.response.status}`,
  );
}
