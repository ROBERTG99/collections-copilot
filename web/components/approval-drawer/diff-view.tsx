import { diffWords } from "diff";

/** Word diff between the AI draft and the edited text (what the audit log stores as a diff). */
export function DiffView({ before, after }: { before: string; after: string }) {
  const parts = diffWords(before, after);
  if (parts.every((p) => !p.added && !p.removed)) {
    return <p className="text-sm text-muted">No changes from the draft.</p>;
  }
  return (
    <p
      className="whitespace-pre-wrap rounded border border-rule bg-surface p-2 text-sm"
      data-testid="diff"
    >
      {parts.map((p, i) =>
        p.added ? (
          <ins
            key={i}
            className="bg-chip-ai text-teal no-underline"
            aria-label={`added: ${p.value}`}
          >
            {p.value}
          </ins>
        ) : p.removed ? (
          <del key={i} className="text-danger" aria-label={`removed: ${p.value}`}>
            {p.value}
          </del>
        ) : (
          <span key={i}>{p.value}</span>
        ),
      )}
    </p>
  );
}
