import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { platformOverview } from "../test/fixtures";
import { apiError, mockApi, renderAt } from "../test/harness";

afterEach(() => {
  vi.unstubAllGlobals();
  sessionStorage.clear();
});

describe("API token", () => {
  it("asks for the token, rejects a wrong one, remembers the right one for the tab", async () => {
    const api = mockApi({
      "GET /api/v1/overview": (request) =>
        request.headers.get("Authorization") === "Bearer good-token"
          ? { body: platformOverview() }
          : apiError(401, "unauthorized", "A valid API token is required"),
    });
    renderAt("/");

    expect(await screen.findByText("This TestMind needs an API token")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Forget API token" })).toBeNull();

    await userEvent.type(screen.getByLabelText("API token"), "wrong");
    await userEvent.click(screen.getByRole("button", { name: "Use token" }));
    expect(await screen.findByText("The API token was not accepted")).toBeInTheDocument();

    await userEvent.type(screen.getByLabelText("API token"), " good-token ");
    await userEvent.click(screen.getByRole("button", { name: "Use token" }));
    expect(await screen.findByRole("heading", { name: "Projects" })).toBeInTheDocument();
    expect(api.calls.at(-1)?.authorization).toBe("Bearer good-token");
    expect(sessionStorage.getItem("testmind.token")).toBe("good-token");

    await userEvent.click(screen.getByRole("button", { name: "Forget API token" }));
    expect(sessionStorage.getItem("testmind.token")).toBeNull();
    await waitFor(() => {
      expect(screen.getByText("This TestMind needs an API token")).toBeInTheDocument();
    });
    expect(api.calls.at(-1)?.authorization).toBeNull();
  });
});
