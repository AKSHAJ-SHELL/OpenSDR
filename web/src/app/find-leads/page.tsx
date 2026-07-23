import { api } from "@/lib/api";
import { FindLeads } from "@/components/leads/FindLeads";
import { ApiDown } from "@/components/ui/ApiDown";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageHeader } from "@/components/ui/PageHeader";

export const dynamic = "force-dynamic";

const SUBTITLE =
  "Search your own provider account, preview against the same gate a CSV faces, import what's new.";

export default async function FindLeadsPage() {
  let providers: string[];
  try {
    providers = await api.sourceProviders();
  } catch (e) {
    return (
      <>
        <PageHeader title="Find leads" subtitle={SUBTITLE} />
        <ApiDown error={e instanceof Error ? e.message : String(e)} />
      </>
    );
  }

  return (
    <>
      <PageHeader title="Find leads" subtitle={SUBTITLE} />
      {providers.length === 0 ? (
        <EmptyState
          title="No lead source configured"
          body="Set LEAD_SOURCE_PROVIDERS (e.g. apollo,webhook) plus the matching keys — APOLLO_API_KEY or LEAD_SOURCE_WEBHOOK_URL — then reload. Results come from your own provider account; there is no built-in contact database."
        />
      ) : (
        <FindLeads providers={providers} />
      )}
    </>
  );
}
