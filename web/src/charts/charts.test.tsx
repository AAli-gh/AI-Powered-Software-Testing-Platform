import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { BarList } from "./BarList";
import { LineChart } from "./LineChart";
import { axisTicks, StackedColumns } from "./StackedColumns";

const SERIES = [
  { key: "passed", label: "Passed", color: "var(--good)" },
  { key: "failed", label: "Failed", color: "var(--critical)" },
];

describe("axisTicks", () => {
  it.each([
    [0, [0, 1]],
    [4, [0, 1, 2, 3, 4]],
    [5, [0, 1, 2, 3, 4, 5]],
    [7, [0, 2, 4, 6, 8]],
    [14, [0, 5, 10, 15]],
    [230, [0, 50, 100, 150, 200, 250]],
  ])("gives round ticks for %d", (value, ticks) => {
    expect(axisTicks(value)).toEqual(ticks);
  });
});

describe("StackedColumns", () => {
  const columns = [
    { id: "a", label: "10:00", title: "First", values: { passed: 3, failed: 1 } },
    { id: "b", label: "10:05", title: "Second", values: null },
  ];

  it("draws a legend, one segment per non-zero value, and nothing for an empty column", () => {
    const { container } = render(
      <StackedColumns columns={columns} series={SERIES} label="Tests per execution" />,
    );
    expect(screen.getByRole("img", { name: "Tests per execution" })).toBeInTheDocument();
    expect(screen.getByText("Passed")).toBeInTheDocument();
    expect(container.querySelectorAll("path")).toHaveLength(2);
    expect(screen.getByLabelText("First: 3 passed, 1 failed")).toBeInTheDocument();
    expect(screen.getByLabelText("Second: no test results")).toBeInTheDocument();
  });

  it("shows a tooltip with every series on hover and focus, and opens a column", async () => {
    const onSelect = vi.fn();
    render(<StackedColumns columns={columns} series={SERIES} label="Tests" onSelect={onSelect} />);
    const first = screen.getByLabelText("First: 3 passed, 1 failed");
    fireEvent.pointerEnter(first);
    const tooltip = screen.getByRole("status");
    expect(tooltip).toHaveTextContent("First");
    expect(tooltip).toHaveTextContent("3Passed");
    expect(tooltip).toHaveTextContent("1Failed");
    fireEvent.pointerLeave(first);
    expect(screen.queryByRole("status")).toBeNull();

    const second = screen.getByLabelText("Second: no test results");
    fireEvent.focus(second);
    expect(screen.getByRole("status")).toHaveTextContent("no test results");
    fireEvent.blur(second);

    await userEvent.click(first);
    fireEvent.keyDown(second, { key: "Enter" });
    fireEvent.keyDown(second, { key: "Escape" });
    expect(onSelect.mock.calls).toEqual([["a"], ["b"]]);
  });
});

describe("LineChart", () => {
  const points = [
    { label: "1", title: "First", values: { a: 50, b: 10 } },
    { label: "2", title: "Second", values: { a: null, b: 20 } },
    { label: "3", title: "Third", values: { a: 70, b: 30 } },
  ];
  const series = [
    { key: "a", label: "Code", color: "var(--series-1)" },
    { key: "b", label: "API", color: "var(--series-2)" },
  ];

  it("breaks a line at a missing value instead of drawing zero", () => {
    const { container } = render(
      <LineChart points={points} series={series} format={(v) => `${v}%`} label="Coverage" />,
    );
    // Series a: two separate segments; series b: one line.
    expect(container.querySelectorAll("path")).toHaveLength(3);
    expect(screen.getByText("Code")).toBeInTheDocument();
  });

  it("reads values with the keyboard: one tooltip, every series", () => {
    render(
      <LineChart
        points={points}
        series={series}
        format={(v) => `${v}.0 %`}
        tickFormat={(v) => `${v}%`}
        label="Coverage"
      />,
    );
    expect(screen.getByText("100%")).toBeInTheDocument();
    const plot = screen.getByLabelText("Coverage: use the arrow keys to read values");
    fireEvent.focus(plot);
    expect(screen.getByRole("status")).toHaveTextContent("Third");
    fireEvent.keyDown(plot, { key: "ArrowLeft" });
    const tooltip = screen.getByRole("status");
    expect(tooltip).toHaveTextContent("Second");
    expect(tooltip).toHaveTextContent("—Code");
    expect(tooltip).toHaveTextContent("20.0 %API");
    fireEvent.keyDown(plot, { key: "ArrowRight" });
    fireEvent.keyDown(plot, { key: "ArrowRight" });
    expect(screen.getByRole("status")).toHaveTextContent("Third");
    fireEvent.keyDown(plot, { key: "Home" });
    fireEvent.blur(plot);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("snaps the crosshair to the nearest point under the pointer", () => {
    render(<LineChart points={points} series={series} format={String} label="Coverage" />);
    const plot = screen.getByLabelText("Coverage: use the arrow keys to read values");
    plot.getBoundingClientRect = () =>
      ({ left: 0, width: 584, top: 0, height: 162, right: 584, bottom: 162 }) as DOMRect;
    fireEvent.pointerMove(plot, { clientX: 10 });
    expect(screen.getByRole("status")).toHaveTextContent("First");
    fireEvent.pointerMove(plot, { clientX: 580 });
    expect(screen.getByRole("status")).toHaveTextContent("Third");
    fireEvent.pointerLeave(plot);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("centres a single point and has no legend for one series", () => {
    const { container } = render(
      <LineChart
        points={[{ label: "1", title: "Only", values: { a: 60 } }]}
        series={[series[0] ?? { key: "a", label: "Code", color: "red" }]}
        format={String}
        label="Score"
      />,
    );
    expect(container.querySelector(".legend")).toBeNull();
    expect(container.querySelectorAll("circle")).toHaveLength(1);
  });
});

describe("BarList", () => {
  it("shows every label and value, and no bar for zero", () => {
    const { container } = render(
      <BarList
        label="Findings"
        format={(v) => `${v}!`}
        items={[
          { key: "high", label: "high", value: 3, color: "red" },
          { key: "low", label: "low", value: 0 },
        ]}
      />,
    );
    expect(screen.getByRole("list", { name: "Findings" })).toBeInTheDocument();
    expect(screen.getByText("3!")).toBeInTheDocument();
    expect(screen.getByText("0!")).toBeInTheDocument();
    expect(container.querySelectorAll(".bar-fill")).toHaveLength(1);
  });
});
