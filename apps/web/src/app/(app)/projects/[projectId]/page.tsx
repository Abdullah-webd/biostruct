import WorkflowView from "@/components/WorkflowView";

export default async function ProjectPage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = await params;
  return <WorkflowView projectId={projectId} />;
}
