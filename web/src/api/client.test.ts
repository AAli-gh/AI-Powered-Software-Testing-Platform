import { apiError, mockApi } from "../test/harness";
import { api, ApiError, unwrap } from "./client";

describe("unwrap", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns the body of a successful answer", async () => {
    mockApi({ "GET /api/v1/overview": { body: { projects: 3 } } });
    const data = await unwrap(api.GET("/api/v1/overview"));
    expect(data.projects).toBe(3);
  });

  it("carries the API's error code and message", async () => {
    mockApi({
      "GET /api/v1/projects/{id}": apiError(404, "x", "x"),
      "POST /api/v1/projects": apiError(409, "conflict", "Project 'shop' already exists"),
    });
    const error = await unwrap(
      api.POST("/api/v1/projects", {
        body: { name: "shop", source_type: "local", source_location: "shop" },
      }),
    ).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "conflict",
      message: "Project 'shop' already exists",
      notFound: false,
    });
  });

  it("describes an answer without the error model", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(new Response("Bad gateway", { status: 502 }))),
    );
    const error = await unwrap(api.GET("/api/v1/overview")).catch((e: unknown) => e);
    expect(error).toMatchObject({
      status: 502,
      code: "unexpected_answer",
      message: "The API answered 502",
    });
  });

  it("reports an unreachable API as a network error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.reject(new TypeError("Failed to fetch"))),
    );
    const error = await unwrap(api.GET("/api/v1/overview")).catch((e: unknown) => e);
    expect(error).toMatchObject({ status: 0, code: "network_error" });
  });

  it("marks 404 answers as not found", () => {
    expect(new ApiError(404, "not_found", "gone").notFound).toBe(true);
  });
});
