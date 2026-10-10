import Link from "next/link";

import { PageHeader } from "@/components/panel";

export default function NotFound() {
  return (
    <div className="grid gap-8">
      <PageHeader experience="Not found" route="404" title="Nothing is published at this address" />
      <p className="text-ink-muted">
        <Link href="/" className="text-ink underline">
          Go to the overview
        </Link>
      </p>
    </div>
  );
}
