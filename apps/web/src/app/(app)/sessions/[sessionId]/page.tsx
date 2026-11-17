import Workbench from "@/components/Workbench";

export default async function SessionPage({
  params,
}: {
  params: Promise<{ sessionId: string }>;
}) {
  const { sessionId } = await params;
  return <Workbench sessionId={sessionId} />;
}
