import { api } from "@/lib/api";
import { InboxView } from "@/components/inbox/InboxView";
import { ApiDown } from "@/components/ui/ApiDown";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

export default async function InboxPage() {
  let messages;
  try {
    messages = await api.inbox();
  } catch (e) {
    return (
      <>
        <PageHeader
          title="Inbox"
          subtitle="Every reply in one place. Classify, override, hand off."
        />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Inbox"
        subtitle="Every reply in one place. Classify, override, hand off."
      />
      <InboxView initial={messages} />
    </>
  );
}
