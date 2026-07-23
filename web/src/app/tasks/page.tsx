import { api } from "@/lib/api";
import { TaskQueue } from "@/components/tasks/TaskQueue";
import { ApiDown } from "@/components/ui/ApiDown";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

const SUBTITLE =
  "Validated LinkedIn notes and call briefs waiting for a human touch. Craftsman writes and checks the content; you perform the outreach. Nothing here is automated — that's the point.";

export default async function TasksPage() {
  let tasks;
  try {
    tasks = await api.tasks({ status: "open" });
  } catch (e) {
    return (
      <>
        <PageHeader title="Tasks" subtitle={SUBTITLE} />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Tasks" subtitle={SUBTITLE} />
      <div className="animate-rise-delay-1">
        <TaskQueue initial={tasks} />
      </div>
    </>
  );
}
