import { redirect } from "next/navigation";

import { currentSession } from "@/lib/server-session";

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<{ error?: string }>;
}) {
  if (await currentSession()) redirect("/queue");
  const { error } = await searchParams;
  return (
    <main id="main" className="mx-auto mt-24 max-w-md px-4">
      <h1 className="text-2xl font-semibold">collections-copilot</h1>
      <p className="mt-2 text-muted">
        Prioritized collections with AI drafts that a person approves. Synthetic data only.
      </p>
      {error ? (
        <p role="alert" className="mt-4 text-danger">
          Sign-in did not complete. Try again.
        </p>
      ) : null}
      <a
        href="/auth/login"
        className="mt-6 inline-block rounded bg-teal px-4 py-2 font-semibold text-teal-ink"
      >
        Sign in
      </a>
    </main>
  );
}
