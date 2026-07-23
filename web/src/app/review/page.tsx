import { api } from "@/lib/api";
import { ReviewQueue } from "@/components/review/ReviewQueue";
import { ApiDown } from "@/components/ui/ApiDown";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

const SUBTITLE =
  "Where the agent hands off. Blocked copy and low-confidence classifications wait here for a human call.";

export default async function ReviewPage() {
  let items;
  try {
    items = await api.review();
  } catch (e) {
    return (
      <>
        <PageHeader title="Review" subtitle={SUBTITLE} />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Review" subtitle={SUBTITLE} />
      <div className="animate-rise-delay-1">
        <ReviewQueue initial={items} />
      </div>
    </>
  );
}
