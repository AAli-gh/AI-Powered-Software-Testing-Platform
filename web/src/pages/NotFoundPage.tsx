import { Link } from "react-router";

import { Empty } from "../components/ui";

export function NotFoundPage() {
  return (
    <Empty>
      <h1>Page not found</h1>
      <p>
        <Link to="/">Back to the overview</Link>
      </p>
    </Empty>
  );
}
