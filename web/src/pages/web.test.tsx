import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { page, SCAN_ID, siteScan, webScanRead } from "../test/fixtures";
import { apiError, mockApi, renderAt } from "../test/harness";

afterEach(() => {
  vi.unstubAllGlobals();
});

const SCANS = "/api/v1/web-scans";
const SCAN = `/api/v1/web-scans/${SCAN_ID}`;

describe("Websites", () => {
  it("lists scans and links to each one", async () => {
    mockApi({ [`GET ${SCANS}`]: { body: page([webScanRead()]) } });
    renderAt("/web-scans");
    expect(await screen.findByRole("heading", { name: "Websites" })).toBeInTheDocument();
    const link = await screen.findByRole("link", { name: "https://example.com/" });
    expect(link).toHaveAttribute("href", `/web-scans/${SCAN_ID}`);
    expect(screen.getByText("succeeded")).toBeInTheDocument();
  });

  it("starts a scan and navigates to it", async () => {
    const api = mockApi({
      [`GET ${SCANS}`]: { body: page([]) },
      [`POST ${SCANS}`]: { status: 202, body: webScanRead({ status: "queued", result: null }) },
      [`GET ${SCAN}`]: { body: webScanRead({ status: "queued", result: null }) },
    });
    const { router } = renderAt("/web-scans");
    expect(await screen.findByText("No scans yet.")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("URL"), "https://example.com/");
    await userEvent.click(screen.getByLabelText(/Exercise controls/));
    await userEvent.click(screen.getByRole("button", { name: "Start scan" }));

    await waitFor(() => {
      expect(router.state.location.pathname).toBe(`/web-scans/${SCAN_ID}`);
    });
    const posted = api.calls.find((c) => c.method === "POST" && c.path === SCANS)?.body;
    expect(posted).toEqual({
      target_url: "https://example.com/",
      max_pages: 100,
      max_depth: 5,
      interact: true,
    });
    expect(await screen.findByText(/The scan is queued/)).toBeInTheDocument();
  });

  it("rejects an internal target with the API's message", async () => {
    const api = mockApi({
      [`GET ${SCANS}`]: { body: page([]) },
      [`POST ${SCANS}`]: apiError(422, "invalid_target", "The target URL cannot be scanned"),
    });
    renderAt("/web-scans");
    await userEvent.type(screen.getByLabelText("URL"), "http://127.0.0.1/");
    await userEvent.click(screen.getByRole("button", { name: "Start scan" }));
    expect(await screen.findByText("The target URL cannot be scanned")).toBeInTheDocument();
    expect(api.calls.some((c) => c.method === "POST")).toBe(true);
  });

  it("shows a finished scan's issues and pages", async () => {
    mockApi({ [`GET ${SCAN}`]: { body: webScanRead() } });
    renderAt(`/web-scans/${SCAN_ID}`);
    expect(
      await screen.findByRole("heading", { name: "https://example.com/" }),
    ).toBeInTheDocument();

    const issues = screen.getByText("Issues", { selector: ".tile-label" }).parentElement;
    expect(issues).toHaveTextContent("1 high severity");

    expect(screen.getByText("server error")).toBeInTheDocument();
    expect(screen.getByText("dead button")).toBeInTheDocument();
    // The high-severity issue is sorted first.
    const rows = screen.getAllByRole("row");
    expect(rows[1]).toHaveTextContent("server error");

    expect(screen.getByText("Pages crawled")).toBeInTheDocument();
    // The crash URL appears in both the issues and the pages tables.
    expect(screen.getAllByText("https://example.com/crash").length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Refused as out of scope/)).toBeInTheDocument();
  });

  it("explains a scan that is still running, and one that failed", async () => {
    mockApi({
      [`GET ${SCAN}`]: { body: webScanRead({ status: "running", result: null }) },
    });
    const running = renderAt(`/web-scans/${SCAN_ID}`);
    expect(await screen.findByText(/The scan is running/)).toBeInTheDocument();
    running.unmount();
    vi.unstubAllGlobals();

    mockApi({
      [`GET ${SCAN}`]: {
        body: webScanRead({ status: "failed", result: null, error_message: "DNS failure" }),
      },
    });
    renderAt(`/web-scans/${SCAN_ID}`);
    expect(await screen.findByText("DNS failure")).toBeInTheDocument();
  });

  it("says when a finished scan found nothing", async () => {
    mockApi({
      [`GET ${SCAN}`]: { body: webScanRead({ result: siteScan({ issues: [], denied: [] }) }) },
    });
    renderAt(`/web-scans/${SCAN_ID}`);
    expect(await screen.findByText("No issues found.")).toBeInTheDocument();
  });
});
