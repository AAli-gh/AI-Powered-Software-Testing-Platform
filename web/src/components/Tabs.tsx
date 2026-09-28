import { useId, type KeyboardEvent, type ReactNode } from "react";
import { useSearchParams } from "react-router";

export interface Tab {
  id: string;
  label: string;
  render: () => ReactNode;
}

/** Tabs kept in the URL (?tab=…), so a view can be linked and survives a reload. */
export function Tabs({ tabs }: { tabs: Tab[] }) {
  const [params, setParams] = useSearchParams();
  const prefix = useId();
  const first = tabs[0];
  if (!first) return null;
  const current = tabs.find((t) => t.id === params.get("tab")) ?? first;

  const select = (id: string) => {
    const next = new URLSearchParams(params);
    next.set("tab", id);
    setParams(next, { replace: true });
  };

  const onKeyDown = (event: KeyboardEvent) => {
    const index = tabs.indexOf(current);
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    const target = tabs[(index + step + tabs.length) % tabs.length];
    if (step !== 0 && target) {
      event.preventDefault();
      select(target.id);
      document.getElementById(`${prefix}-${target.id}`)?.focus();
    }
  };

  return (
    <div>
      <div className="tabs" role="tablist" onKeyDown={onKeyDown}>
        {tabs.map((tab) => (
          <button
            key={tab.id}
            id={`${prefix}-${tab.id}`}
            className="tab"
            role="tab"
            type="button"
            aria-selected={tab === current}
            aria-controls={`${prefix}-panel`}
            tabIndex={tab === current ? 0 : -1}
            onClick={() => {
              select(tab.id);
            }}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div id={`${prefix}-panel`} role="tabpanel" aria-labelledby={`${prefix}-${current.id}`}>
        {current.render()}
      </div>
    </div>
  );
}
