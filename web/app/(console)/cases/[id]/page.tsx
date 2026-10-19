import { CaseView } from "./case-view";

export const metadata = { title: "Case" };

export default async function CasePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <CaseView caseId={id} />;
}
